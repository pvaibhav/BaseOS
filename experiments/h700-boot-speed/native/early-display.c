#include <stddef.h>
#include <stdint.h>
#include <string.h>
#include "native-logo.h"
#include "native-display.h"
#include "boot-logo-pixels.h"
#include "panel-spi9.h"
#include "early-display-generated.h"

extern uint64_t native_ticks(void);
extern void release_display_sram(void);
extern int baseos_spl_panel(void);

enum display_operation_kind { DISPLAY_UPDATE, DISPLAY_WRITE, DISPLAY_FRAMEBUFFER,
                              DISPLAY_DELAY_US, DISPLAY_POLL, DISPLAY_BARRIER };
struct display_operation {
    uint32_t kind, address, clear_mask, set_bits, timeout_us;
};

/* Generated tables follow these declarations and contain only pinned constants. */
#include "early-display-operations.h"

static uint32_t read32(void *context, uint32_t address)
{
    (void)context;
    return *(volatile uint32_t *)(uintptr_t)address;
}

static void write32(void *context, uint32_t address, uint32_t value)
{
    (void)context;
    *(volatile uint32_t *)(uintptr_t)address = value;
}

static void barrier(void *context)
{
    (void)context;
    asm volatile("dmb sy" ::: "memory");
}

static __attribute__((noinline)) int wait_us(uint32_t microseconds)
{
    uint64_t start = native_ticks();
    for (unsigned remaining = 100000000u; remaining; --remaining)
        if (native_ticks() - start >= (uint64_t)microseconds * 24u) return 0;
    return -1;
}

static void update(uint32_t address, uint32_t mask, uint32_t value)
{
    uint32_t old = read32(NULL, address);
    barrier(NULL);
    barrier(NULL);
    write32(NULL, address, (old & ~mask) | value);
}

static __attribute__((noinline)) int apply(const struct display_operation *operations, size_t count)
{
    for (size_t i = 0; i < count; ++i) {
        const struct display_operation *op = &operations[i];
        if (op->kind == DISPLAY_BARRIER) {
            asm volatile("dsb sy" ::: "memory");
        } else if (op->kind == DISPLAY_DELAY_US) {
            if (wait_us(op->set_bits)) return -1;
        } else if (op->kind == DISPLAY_POLL) {
            uint64_t start = native_ticks();
            unsigned remaining = 100000000u;
            while ((read32(NULL, op->address) & op->clear_mask) != op->set_bits) {
                if (!--remaining || native_ticks() - start >= (uint64_t)op->timeout_us * 24u) return -1;
            }
        } else if (op->kind == DISPLAY_UPDATE) {
            update(op->address, op->clear_mask, op->set_bits);
        } else if (op->kind == DISPLAY_WRITE || op->kind == DISPLAY_FRAMEBUFFER) {
            barrier(NULL);
            write32(NULL, op->address, op->set_bits +
                    (op->kind == DISPLAY_FRAMEBUFFER ? BASEOS_NATIVE_LOGO_PIXEL_ADDRESS : 0));
        } else return -1;
    }
    asm volatile("dsb sy" ::: "memory");
    return 0;
}

static int prepare_panel(void)
{
    /* The SPL normally ran these steps while it read the card. */
    int ready = baseos_spl_panel();
    if (ready >= 0) return ready;
    update(0x0300b124u, 0xff000000u, 0x11000000u);
    update(0x0300b130u, 0x8000u, 0x8000u);
    if (wait_us(30000)) return -1;
    update(0x0300b130u, 0x4000u, 0x4000u);
    if (wait_us(15000)) return -1;
    update(0x0300b130u, 0x4000u, 0);
    if (wait_us(50000)) return -1;
    update(0x0300b130u, 0x4000u, 0x4000u);
    if (wait_us(100000)) return -1;
    if (BASEOS_PANEL_RULE < 2) return BASEOS_PANEL_RULE;
    update(0x0300b090u, 0x0f000000u, 0x06000000u);
    if (BASEOS_PANEL_PULL_UP) update(0x0300b0acu, 0x3000u, 0x1000u);
    return !(read32(NULL, 0x0300b0a0u) & 0x40u);
}

static void clean_pixels(size_t bytes)
{
    uint32_t ctr;
    asm volatile("mrc p15, 0, %0, c0, c0, 1" : "=r"(ctr));
    uintptr_t line = (uintptr_t)4 << ((ctr >> 16) & 15);
    uintptr_t start = BASEOS_NATIVE_LOGO_PIXEL_ADDRESS;
    for (uintptr_t address = start & ~(line - 1); address < start + bytes; address += line)
        asm volatile("mcr p15, 0, %0, c7, c10, 1" :: "r"(address) : "memory");
    asm volatile("dsb sy" ::: "memory");
}

static int scanline_advances(void)
{
    uint32_t first = (read32(NULL, 0x065110fcu) >> 16) & 0xfffu;
    uint64_t start = native_ticks();
    for (unsigned remaining = 100000000u; remaining; --remaining) {
        if (((read32(NULL, 0x065110fcu) >> 16) & 0xfffu) != first) return 0;
        if (native_ticks() - start >= 2400000u) return -1;
    }
    return -1;
}

/* Pixel production has no display side effects; invalid input leaves RAM alone. */
__attribute__((noinline)) int baseos_prepare_pixels(const struct baseos_native_logo *logo)
{
    const unsigned char *bmp = (void *)BASEOS_NATIVE_LOGO_BMP_ADDRESS;
    if (!BASEOS_NATIVE_LOGO_PIXEL_ADDRESS) return -1;
    if (!logo->magic && !logo->bytes && !logo->fnv && !logo->reserved) {
        memset((void *)BASEOS_NATIVE_LOGO_PIXEL_ADDRESS, 0, BASEOS_LOGO_WIDTH * BASEOS_LOGO_HEIGHT * 4u);
        return 0;
    }
    if (logo->magic != BASEOS_NATIVE_LOGO_MAGIC || logo->reserved ||
        logo->bytes > BASEOS_NATIVE_LOGO_CAPACITY || !logo->bytes ||
        baseos_logo_hash(bmp, logo->bytes) != logo->fnv ||
        !baseos_logo_pixels(bmp, logo->bytes, BASEOS_LOGO_WIDTH, BASEOS_LOGO_HEIGHT,
                            (void *)BASEOS_NATIVE_LOGO_PIXEL_ADDRESS, BASEOS_NATIVE_LOGO_CAPACITY)) return -1;
    return 1;
}

/* After the panel stream starts, every remaining vendor wait is a deadline. The
 * loader polls between kernel decode slices, so the waits overlap the decode while
 * each step still runs in vendor order and no earlier than its minimum wait. */
enum display_stage { DISPLAY_IDLE, DISPLAY_STREAM, DISPLAY_PUBLISH, DISPLAY_BACKLIGHT,
                     DISPLAY_DONE, DISPLAY_FAILED };
static struct {
    enum display_stage stage;
    unsigned panel;
    const uint16_t *stream;
    size_t count, next;
    struct baseos_panel_spi9 port;
    uint64_t since, wait;
    const struct baseos_native_logo *logo;
} job;

static __attribute__((noinline)) void defer_us(uint32_t microseconds)
{
    job.since = native_ticks();
    job.wait = (uint64_t)microseconds * 24u;
}

/* Send panel stream operations up to the next delay: 1 at a delay, 0 at the end. */
static int stream_run(void)
{
    while (job.next < job.count) {
        unsigned op = job.stream[job.next++];
        if (op & 0x8000u) {
            defer_us((op & 0x7fffu) * 1000u);
            return 1;
        }
        baseos_panel_spi9_send(&job.port, (uint8_t)op, op & 0x100u);
    }
    return 0;
}

static void publish_record(void)
{
    struct baseos_native_display *record = (void *)BASEOS_NATIVE_DISPLAY_ADDRESS;
    size_t pixel_bytes = BASEOS_LOGO_WIDTH * BASEOS_LOGO_HEIGHT * 4u;
    struct baseos_native_display next;
    memset(&next, 0, sizeof(next));
    next.magic = BASEOS_NATIVE_DISPLAY_MAGIC;
    next.version = 1;
    next.bytes = sizeof(next);
    next.panel = job.panel;
    next.logo_bytes = job.logo->bytes;
    next.logo_fnv = job.logo->fnv;
    next.pixel_fnv = baseos_logo_hash((void *)BASEOS_NATIVE_LOGO_PIXEL_ADDRESS, (unsigned)pixel_bytes);
    memcpy(next.generation, baseos_generation, sizeof(next.generation));
    next.shown_ticks = native_ticks();
    next.fnv = baseos_display_hash(&next);
    memcpy((unsigned char *)record + 4, (const unsigned char *)&next + 4, sizeof(next) - 4);
    asm volatile("dsb sy" ::: "memory");
    record->magic = next.magic;
    asm volatile("dsb sy" ::: "memory");
}

/* Run every display step whose wait has passed: 1 while waiting, 0 when the logo is
 * lit (or nothing was started), -1 after a failed step. */
int baseos_early_display_poll(void)
{
    while (job.stage != DISPLAY_IDLE && job.stage != DISPLAY_DONE) {
        if (job.stage == DISPLAY_FAILED) return -1;
        if (native_ticks() - job.since < job.wait) return 1;
        if (job.stage == DISPLAY_STREAM) {
            if (stream_run()) continue;
            defer_us(50000);
            job.stage = DISPLAY_PUBLISH;
        } else if (job.stage == DISPLAY_PUBLISH) {
            if (apply(baseos_display_publish, sizeof(baseos_display_publish) / sizeof(*baseos_display_publish)) ||
                apply(baseos_display_enable, sizeof(baseos_display_enable) / sizeof(*baseos_display_enable))) {
                job.stage = DISPLAY_FAILED;
                return -1;
            }
            defer_us(100000);
            job.stage = DISPLAY_BACKLIGHT;
        } else {
            if (scanline_advances() || apply(baseos_display_backlight,
                    sizeof(baseos_display_backlight) / sizeof(*baseos_display_backlight))) {
                job.stage = DISPLAY_FAILED;
                return -1;
            }
            publish_record();
            job.stage = DISPLAY_DONE;
        }
    }
    return 0;
}

/* Wait out the remaining steps, with the same counter-stall bound as wait_us. */
int baseos_early_display_finish(void)
{
    for (unsigned remaining = 100000000u; remaining; --remaining) {
        int state = baseos_early_display_poll();
        if (state <= 0) return state;
    }
    job.stage = DISPLAY_FAILED;
    return -1;
}

/* Convert, release SRAM C, prepare DE/TCON, take the panel out of reset and start its
 * stream. A missing logo uses the vendor's zero-filled framebuffer and the same
 * warm display path. A malformed descriptor/BMP refuses boot. */
int baseos_early_display_start(const struct baseos_native_logo *logo)
{
    struct baseos_native_display *record = (void *)BASEOS_NATIVE_DISPLAY_ADDRESS;
    memset(record, 0, sizeof(*record));
    size_t pixel_bytes = BASEOS_LOGO_WIDTH * BASEOS_LOGO_HEIGHT * 4u;
    if (baseos_prepare_pixels(logo) < 0) return -2;
    clean_pixels(pixel_bytes);
    release_display_sram();
    if (apply(baseos_display_prepare, sizeof(baseos_display_prepare) / sizeof(*baseos_display_prepare))) return -1;
    int panel = prepare_panel();
    if (panel < 0) return -1;
    job.stage = DISPLAY_STREAM;
    job.panel = (unsigned)panel;
    job.stream = panel ? baseos_panel_boe : baseos_panel_old;
    job.count = panel ? sizeof(baseos_panel_boe) / sizeof(*baseos_panel_boe)
                      : sizeof(baseos_panel_old) / sizeof(*baseos_panel_old);
    job.next = 0;
    job.port = (struct baseos_panel_spi9){{NULL, read32, write32, barrier}, 0};
    job.since = native_ticks();
    job.wait = 0;
    job.logo = logo;
    return baseos_early_display_poll() < 0 ? -1 : panel;
}
