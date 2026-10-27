#include <stddef.h>
#include <stdint.h>
#include <string.h>
#include <libfdt.h>
#include "native-handoff.h"
#include "native-generated.h"
#include "panel-probe.h"
#include "native-logo.h"
#include "native-display.h"
#include "native-panel.h"
#include "lz4-stream.h"
#include "boot-logo-pixels.h"
#ifdef BASEOS_NATIVE_DIAGNOSTIC
#include "native-trace-constants.h"
#endif

extern void native_enable_mmu(void);
extern uint64_t native_ticks(void);
extern void native_kernel_enter(void) __attribute__((noreturn));
extern int baseos_early_display_start(const struct baseos_native_logo *logo);
extern int baseos_early_display_poll(void);
extern int baseos_early_display_finish(void);

static char command_line[2048];
static uint64_t times[7];

static uint32_t panel_read32(uint32_t address)
{
    uint32_t value = *(volatile uint32_t *)address;
    asm volatile("dmb sy" ::: "memory");
    return value;
}

static void panel_write32(uint32_t address, uint32_t value)
{
    *(volatile uint32_t *)address = value;
    asm volatile("dmb sy" ::: "memory");
}

static const struct baseos_panel_io panel_io = {panel_read32, panel_write32, native_ticks};

static void checkpoint(unsigned stage)
{
#ifdef BASEOS_NATIVE_DIAGNOSTIC
    *(volatile uint32_t *)0x0700013c = 0x42530000u | stage;
    asm volatile("dsb sy" ::: "memory");
#else
    (void)stage;
#endif
}

static uint32_t little32(const unsigned char *p)
{
    return (uint32_t)p[0] | (uint32_t)p[1] << 8 | (uint32_t)p[2] << 16 | (uint32_t)p[3] << 24;
}

static uint32_t record_hash(const struct baseos_native_record *record)
{
    const unsigned char *p = (const void *)record;
    uint32_t hash = 2166136261u;
    for (unsigned i = 0; i < sizeof(*record); i++)
        hash = (hash ^ (i >= 196 && i < 200 ? 0 : p[i])) * 16777619u;
    return hash;
}

#ifdef BASEOS_NATIVE_DIAGNOSTIC
static uint32_t byte_hash(const unsigned char *bytes, unsigned count)
{
    uint32_t hash = 2166136261u;
    while (count--) hash = (hash ^ *bytes++) * 16777619u;
    return hash;
}
#endif

static int checked_extent(uint32_t *offset, uint32_t bytes, uint32_t limit, uint32_t *begin)
{
    if (bytes > limit || bytes > limit - *offset || bytes > 0xfffff800u) return -1;
    uint32_t rounded = (bytes + 2047u) & ~2047u;
    if (rounded > limit - *offset) return -1;
    *begin = *offset;
    *offset += rounded;
    return 0;
}

static void hex_digits(char *out, uint32_t word, unsigned digits)
{
    for (unsigned i = 0; i < digits; i++)
        out[i] = "0123456789abcdef"[(word >> ((digits - 1 - i) * 4)) & 15];
}

/* The SPL ran the vendor panel power, reset and strap steps while it read the card.
 * Use its panel only if the record proves each minimum wait finished in the past and
 * the pins still hold the state it left; otherwise return -1 and redo the steps. */
__attribute__((noinline)) int baseos_spl_panel(void)
{
    static const uint32_t wait_ms[4] = BASEOS_PANEL_WAITS_MS;
    const struct baseos_native_panel *panel = (void *)BASEOS_NATIVE_PANEL_ADDRESS;
    const struct baseos_native_record *record = (void *)BASEOS_NATIVE_RECORD_ADDRESS;
    if (panel->magic != BASEOS_NATIVE_PANEL_MAGIC || panel->version != BASEOS_NATIVE_PANEL_VERSION ||
        panel->bytes != sizeof(*panel) || panel->rule != BASEOS_PANEL_RULE || panel->panel > 1 ||
        (BASEOS_PANEL_RULE < 2 && panel->panel != BASEOS_PANEL_RULE) ||
        baseos_panel_hash(panel) != panel->fnv || memcmp(panel->generation, baseos_generation, 32) ||
        panel->ticks[0] < record->started_ticks || panel->ticks[4] > native_ticks()) return -1;
    for (unsigned i = 0; i < 4; i++)
        if (panel->ticks[i + 1] < panel->ticks[i] ||
            panel->ticks[i + 1] - panel->ticks[i] < (uint64_t)wait_ms[i] * BASEOS_PANEL_TICKS_PER_MS) return -1;
    /* PI14/PI15 outputs, power on, reset released; PE6 still configured and strapped alike. */
    if ((panel_read32(0x0300b124u) & 0xff000000u) != 0x11000000u ||
        (panel_read32(0x0300b130u) & 0xc000u) != 0xc000u) return -1;
    if (BASEOS_PANEL_RULE == 2 &&
        ((panel_read32(0x0300b090u) & 0x0f000000u) != 0x06000000u ||
         (BASEOS_PANEL_PULL_UP && (panel_read32(0x0300b0acu) & 0x3000u) != 0x1000u) ||
         (unsigned)!(panel_read32(0x0300b0a0u) & 0x40u) != panel->panel)) return -1;
    return (int)panel->panel;
}

__attribute__((noinline)) int baseos_native_display_state(void)
{
    const struct baseos_native_display *display = (void *)BASEOS_NATIVE_DISPLAY_ADDRESS;
    int ready = baseos_display_valid(display, baseos_generation);
    if (ready <= 0) return ready;
    const struct baseos_native_record *record = (void *)BASEOS_NATIVE_RECORD_ADDRESS;
    const struct baseos_native_logo *logo = (void *)BASEOS_NATIVE_LOGO_RECORD_ADDRESS;
    int blank = !logo->magic && !logo->bytes && !logo->fnv && !logo->reserved;
    if ((!blank && logo->magic != BASEOS_NATIVE_LOGO_MAGIC) || logo->reserved ||
        (BASEOS_PANEL_RULE < 2 && display->panel != BASEOS_PANEL_RULE) ||
        display->logo_bytes != logo->bytes || display->logo_fnv != logo->fnv ||
        display->shown_ticks < record->loaded_ticks || display->shown_ticks > native_ticks()) return -1;
    /* The display program hashed the BMP against the SPL descriptor and the scanned-out
     * pixels into this record moments ago, in this same run; nothing writes either
     * buffer since, so they are not hashed again here. */
    return 1;
}

static int make_command_line(const unsigned char *image)
{
    const struct baseos_native_record *record = (void *)BASEOS_NATIVE_RECORD_ADDRESS;
    static const char *const reasons[] = {"button", "irq", "usb", "charger", "battery", "unknow"};
    volatile uint32_t *sid = (void *)0x03006000;
    char serial[20];
    hex_digits(serial, sid[0x204 / 4] >> 20, 3);
    hex_digits(serial + 3, sid[0x208 / 4], 8);
    hex_digits(serial + 11, sid[0x20c / 4], 8);
    serial[19] = 0;
    int early = baseos_native_display_state();
    if (early < 0) return -1;
    const struct baseos_native_display *display = (void *)BASEOS_NATIVE_DISPLAY_ADDRESS;
    int detected = early ? (int)display->panel : baseos_spl_panel();
    if (detected < 0) detected = baseos_panel_probe(BASEOS_PANEL_RULE, BASEOS_PANEL_PULL_UP, &panel_io);
    if (detected < 0) return -1;
    const char *panel = detected ? "boe" : "old";
    const char *reserve = record->ram_bytes == 0x40000000ull ? baseos_display_reserve[0] : baseos_display_reserve[1];
    /* Android v2 has two separately bounded command-line fields. */
    char android[1537];
    size_t first = strnlen((const char *)image + 64, 512);
    size_t extra = strnlen((const char *)image + 608, 1024);
    if (first == 512 || extra == 1024) return -1;
    memcpy(android, image + 64, first);
    memcpy(android + first, image + 608, extra);
    android[first + extra] = 0;
    size_t used = 0;
    for (const char *p = baseos_bootargs_template; *p;) {
        const char *value = NULL;
        unsigned skip = 0;
        if (!strncmp(p, "@SERIAL@", 8)) { value = serial; skip = 8; }
        else if (!strncmp(p, "@PANEL@", 7)) { value = panel; skip = 7; }
        else if (!strncmp(p, "@ANDROID@", 9)) { value = android; skip = 9; }
        else if (!strncmp(p, "@BOOTREASON@", 12)) { value = reasons[record->boot_reason]; skip = 12; }
        else if (!strncmp(p, "@RESERVE@", 9)) { value = reserve; skip = 9; }
        if (value) {
            size_t bytes = strlen(value);
            if (bytes >= sizeof(command_line) - used) return -1;
            memcpy(command_line + used, value, bytes);
            used += bytes;
            p += skip;
        } else {
            if (used + 1 >= sizeof(command_line)) return -1;
            command_line[used++] = *p++;
        }
    }
    command_line[used] = 0;
    return 0;
}

__attribute__((noinline)) int baseos_prepare_logo(void *tree)
{
    int early = baseos_native_display_state();
    /* Never promise warm ownership for pixels that are not actually scanned out. */
    if (early <= 0) return -1;
    char description[72];
    const uint32_t fields[] = {BASEOS_NATIVE_LOGO_PIXEL_ADDRESS, BASEOS_LOGO_WIDTH,
        BASEOS_LOGO_HEIGHT, 32, BASEOS_LOGO_WIDTH * 4, 0, 0, BASEOS_LOGO_WIDTH, BASEOS_LOGO_HEIGHT};
    unsigned used = 0;
    for (unsigned i = 0; i < 9; i++) {
        if (i) description[used++] = ',';
        unsigned digits = 1;
        while (digits < 8 && fields[i] >> (digits * 4)) digits++;
        hex_digits(description + used, fields[i], digits);
        used += digits;
    }
    description[used] = 0;
    int display = fdt_path_offset(tree, "/soc@03000000/disp@01000000");
    /* libfdt inserts new properties at the front. Match the vendor's call order,
     * including the order in which its strings are first added. */
    if (display < 0 || fdt_setprop_u32(tree, display, "boot_disp", BASEOS_DISPLAY_BOOT_DISP) ||
        fdt_setprop_u32(tree, display, "boot_disp1", BASEOS_DISPLAY_BOOT_DISP1) ||
        fdt_setprop_u32(tree, display, "boot_disp2", BASEOS_DISPLAY_BOOT_DISP2)) return -1;
    const struct baseos_native_logo *logo = (void *)BASEOS_NATIVE_LOGO_RECORD_ADDRESS;
    if (logo->bytes && fdt_setprop_string(tree, display, "boot_fb0", description)) return -1;
    return 1;
}

/* Keep the native health properties after every stock /chosen property. libfdt
 * appends their names to the strings table, but normally prepends properties.
 * Move just the new property to the end, leaving every stock byte in order. */
static int append_chosen(void *tree, const char *name, const void *value, int bytes)
{
    int chosen = fdt_path_offset(tree, "/chosen");
    if (chosen < 0 || fdt_getprop(tree, chosen, name, NULL) || bytes > 80 ||
        fdt_setprop(tree, chosen, name, value, bytes)) return -1;
    int first = fdt_first_property_offset(tree, chosen), last = first, next, length;
    if (first < 0) return -1;
    while ((next = fdt_next_property_offset(tree, last)) >= 0) last = next;
    if (next != -FDT_ERR_NOTFOUND || !fdt_getprop_by_offset(tree, last, NULL, &length)) return -1;
    unsigned size = 12u + (((unsigned)bytes + 3u) & ~3u);
    unsigned end = (unsigned)last + 12u + (((unsigned)length + 3u) & ~3u);
    unsigned char property[92];
    unsigned char *start = (unsigned char *)tree + fdt_off_dt_struct(tree) + first;
    memcpy(property, start, size);
    memmove(start, start + size, end - (unsigned)first - size);
    memcpy(start + end - (unsigned)first - size, property, size);
    return 0;
}

int baseos_prepare_kernel(void)
{
    const struct baseos_native_record *record = (void *)BASEOS_NATIVE_RECORD_ADDRESS;
    const unsigned char *image = (void *)BASEOS_NATIVE_IMAGE_ADDRESS;
    if (record->magic != BASEOS_NATIVE_MAGIC || record->version != BASEOS_NATIVE_RECORD_VERSION ||
        record->bytes != sizeof(*record) || record->model != BASEOS_MODEL_ID ||
        record->image_bytes != BASEOS_IMAGE_BYTES || record->image_fnv != BASEOS_IMAGE_FNV ||
        memcmp(record->generation, baseos_generation, 32) || record_hash(record) != record->record_fnv ||
        !BASEOS_NATIVE_LOGO_PIXEL_ADDRESS || record->ram_bytes != (uint64_t)(record->dram_words[7] >> 16) << 20 ||
        record->dram_type != record->dram_words[1] ||
        !baseos_boot_reason_valid(record->boot_reason, record->boot_reason_source)) return 1;
    if (record->image_bytes < 2048 || record->image_bytes > BASEOS_NATIVE_IMAGE_CAPACITY ||
        memcmp(image, "ANDROID!", 8) || little32(image + 36) != 2048 ||
        little32(image + 40) != 2 || little32(image + 1644) != 1660 ||
        little32(image + 24) || little32(image + 1632)) return 2;
    uint32_t kernel_bytes = little32(image + 8), initrd_bytes = little32(image + 16);
    uint32_t dtb_bytes = little32(image + 1648), offset = 2048, kernel, initrd, dtb;
    if (!kernel_bytes || !dtb_bytes || initrd_bytes > 0x100000 || dtb_bytes > BASEOS_NATIVE_FDT_CAPACITY ||
        checked_extent(&offset, kernel_bytes, record->image_bytes, &kernel) ||
        checked_extent(&offset, initrd_bytes, record->image_bytes, &initrd) ||
        checked_extent(&offset, dtb_bytes, record->image_bytes, &dtb) ||
        offset != record->image_bytes) return 3;
    /* Draw where vendor U-Boot did: after BL31, from DRAM, with SRAM C unused. */
    const struct baseos_native_logo *logo = (void *)BASEOS_NATIVE_LOGO_RECORD_ADDRESS;
    int panel = baseos_early_display_start(logo);
    if (panel < 0) return 14;
    times[0] = record->started_ticks;
    times[1] = record->read_started_ticks;
    times[2] = record->loaded_ticks;
    times[3] = native_ticks();
    checkpoint(44);
    /* Decode in slices; the panel and TCON waits run out in between. */
    struct baseos_lz4 lz4;
    if (baseos_lz4_begin(&lz4, (void *)0x40080000, 0x01f80000, image + kernel, kernel_bytes)) return 4;
    int step;
    do {
        step = baseos_lz4_step(&lz4);
        if (baseos_early_display_poll() < 0) return 14;
    } while (step == 1);
    if (step) return 4;
    unsigned long output_bytes = (unsigned long)(lz4.limit - lz4.dst);
#ifdef BASEOS_NATIVE_DIAGNOSTIC
    if (output_bytes != BASEOS_KERNEL_BYTES ||
        byte_hash((void *)0x40080000, output_bytes) != BASEOS_KERNEL_FNV) return 12;
#endif
    times[4] = native_ticks();
    checkpoint(45);
    if (output_bytes < 64 || little32((const void *)0x40080038) != 0x644d5241) return 5;
    memcpy((void *)0x42000000, image + initrd, initrd_bytes);
    void *tree = (void *)BASEOS_NATIVE_FDT_ADDRESS;
    uint32_t capacity = (fdt_totalsize(image + dtb) + 4096u + 31u) & ~31u;
    if (capacity > BASEOS_NATIVE_FDT_CAPACITY) return 6;
    memset(tree, 0, capacity);
    if (fdt_check_full(image + dtb, dtb_bytes) ||
        fdt_open_into(image + dtb, tree, capacity)) return 6;
    int dram = fdt_path_offset(tree, "/dram");
    for (unsigned i = 0; i < 24; i++)
        if (fdt_setprop_u32(tree, dram, baseos_dram_properties[i], record->dram_words[i])) return 7;
    int memory = fdt_path_offset(tree, "/memory@40000000");
    fdt64_t memory_reg[2] = {cpu_to_fdt64(0x40000000), cpu_to_fdt64(record->ram_bytes)};
    if (fdt_setprop(tree, memory, "reg", memory_reg, sizeof(memory_reg))) return 8;
    /* Copying the initrd and opening the tree also fit in the display waits; the command
     * line needs the lit display's record. */
    if (baseos_early_display_finish() < 0) return 14;
    if (baseos_prepare_logo(tree) < 0) return 13;
    int chosen = fdt_path_offset(tree, "/chosen");
    if (make_command_line(image) ||
        fdt_setprop_string(tree, chosen, "bootargs", command_line) ||
        fdt_setprop_u64(tree, chosen, "linux,initrd-start", 0x42000000) ||
        fdt_setprop_u64(tree, chosen, "linux,initrd-end", 0x42000000 + initrd_bytes) ||
        fdt_add_mem_rsv(tree, 0x42000000, initrd_bytes)) return 9;
    /* Image input carries BaseOS's late USB policy. HDMI keeps its stock "okay": the
     * display driver registers its HDMI output at startup only for that exact string. */
    times[5] = native_ticks();
    times[6] = native_ticks();
    chosen = fdt_path_offset(tree, "/chosen");
    fdt64_t stamps[7];
    for (unsigned i = 0; i < 7; i++) stamps[i] = cpu_to_fdt64(times[i]);
    if (append_chosen(tree, "baseos,native-boot", record->generation, 32) ||
        append_chosen(tree, "baseos,native-timing", stamps, sizeof(stamps)) ||
        append_chosen(tree, "baseos,boot-display", (void *)BASEOS_NATIVE_DISPLAY_ADDRESS,
                      sizeof(struct baseos_native_display))) return 11;
#ifdef BASEOS_NATIVE_DIAGNOSTIC
    volatile uint32_t *proof = (void *)0x4a300100;
    proof[0] = fdt_totalsize(tree);
    proof[1] = byte_hash(tree, proof[0]);
#endif
    checkpoint(46);
    return 0;
}

__attribute__((noinline)) void release_display_sram(void)
{
    volatile uint32_t *system = (void *)0x03000000;
    system[0] &= 1u << 31;
    system[1] &= ~(1u << 24);
    asm volatile("dsb sy" ::: "memory");
}

void baseos_loader_boot(void)
{
    checkpoint(41);
    volatile uint32_t *table = (void *)0x4a0f8000;
    const struct baseos_native_record *record = (void *)BASEOS_NATIVE_RECORD_ADDRESS;
    unsigned ram_end = record->ram_bytes == 0x80000000ull ? 0xc00 : 0x800;
    for (unsigned i = 0; i < 4096; i++)
        table[i] = (i << 20) | (i == 0 || (i >= 0x400 && i < ram_end) ? 0x1c0e : 0x10c16);
    checkpoint(42);
    native_enable_mmu();
    checkpoint(43);
    int error = baseos_prepare_kernel();
    if (!error) {
        release_display_sram();
        checkpoint(47);
        native_kernel_enter();
    }
    checkpoint(0x8000u | error);
    /* Bring-up uses the already established restore-first watchdog recovery. */
    for (;;) asm volatile("wfi");
}
