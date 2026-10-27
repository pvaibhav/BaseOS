/* Native boot acceptance. rcS calls it after GPU launch/data setup, before
 * any updater can reboot. Charger acceptance precedes the blocking charger path.
 */
#define _GNU_SOURCE
#include <ctype.h>
#include <errno.h>
#include <fcntl.h>
#include <glob.h>
#include <linux/fb.h>
#include <linux/input.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <sys/file.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <time.h>
#include <unistd.h>
#include "native-attempt.h"

#define FDT_GENERATION "/sys/firmware/devicetree/base/chosen/baseos,native-boot"
#define INSTALLED_GENERATION "/etc/baseos/native-generation"
#define BOOT_DISPLAY "/sys/firmware/devicetree/base/chosen/baseos,boot-display"
#define DISPLAY_MAGIC UINT32_C(0x31505344)
#define DISPLAY_BYTES 80
#define BOOT_MODE "/sys/class/power_supply/axp2202-battery/boot_mode"
#define CMDLINE "/proc/cmdline"
#define ACK_LOCK "/run/baseos-native-health.lock"
#define READY_TIMEOUT_MS 6000
#define MAP_BYTES 4096
#define RTC_BASE 0x07000000
#define TIMER_BASE 0x03009000
#define DE_TOP_BASE 0x01008000
#define WDT_CONFIG (0xb4 / 4)
#define WDT_MODE (0xb8 / 4)

#ifndef NATIVE_HEALTH_FENCE
#if defined(__aarch64__)
#define NATIVE_HEALTH_FENCE() __asm__ volatile("dsb sy" ::: "memory")
#else
#define NATIVE_HEALTH_FENCE() __sync_synchronize()
#endif
#endif

struct registers {
    volatile uint32_t *rtc, *timer;
};

enum ready_mode { NORMAL_BOOT, CHARGER_BOOT };

static void unmap_registers(struct registers *registers)
{
    if (registers->rtc != MAP_FAILED) munmap((void *)registers->rtc, MAP_BYTES);
    if (registers->timer != MAP_FAILED) munmap((void *)registers->timer, MAP_BYTES);
    registers->rtc = registers->timer = MAP_FAILED;
}

static int map_registers(struct registers *registers, int writable)
{
    registers->rtc = registers->timer = MAP_FAILED;
    int fd = open("/dev/mem", (writable ? O_RDWR : O_RDONLY) | O_SYNC | O_CLOEXEC);
    if (fd < 0) return 0;
    int protection = PROT_READ | (writable ? PROT_WRITE : 0);
    registers->rtc = mmap(NULL, MAP_BYTES, protection, MAP_SHARED, fd, RTC_BASE);
    if (registers->rtc != MAP_FAILED)
        registers->timer = mmap(NULL, MAP_BYTES, protection, MAP_SHARED, fd, TIMER_BASE);
    close(fd);
    if (registers->rtc == MAP_FAILED || registers->timer == MAP_FAILED) {
        unmap_registers(registers);
        return 0;
    }
    return 1;
}

static int read_generation(const char *path, uint8_t value[32])
{
    int fd = open(path, O_RDONLY | O_CLOEXEC | O_NOFOLLOW);
    if (fd < 0) return 0;
    uint8_t buffer[33];
    size_t used = 0;
    while (used < sizeof(buffer)) {
        ssize_t count = read(fd, buffer + used, sizeof(buffer) - used);
        if (count < 0 && errno == EINTR) continue;
        if (count < 0) { close(fd); return 0; }
        if (!count) break;
        used += (size_t)count;
    }
    close(fd);
    if (used != 32) return 0;
    memcpy(value, buffer, 32);
    return 1;
}

static int read_text(const char *path, char *buffer, size_t capacity)
{
    int fd = open(path, O_RDONLY | O_CLOEXEC | O_NOFOLLOW);
    if (fd < 0) return 0;
    size_t used = 0;
    while (used < capacity) {
        ssize_t count = read(fd, buffer + used, capacity - used);
        if (count < 0 && errno == EINTR) continue;
        if (count < 0) { close(fd); return 0; }
        if (!count) break;
        used += (size_t)count;
    }
    close(fd);
    if (used == capacity || memchr(buffer, 0, used)) return 0;
    buffer[used] = 0;
    return 1;
}

static uint32_t le32(const uint8_t *bytes)
{
    return bytes[0] | (uint32_t)bytes[1] << 8 | (uint32_t)bytes[2] << 16 | (uint32_t)bytes[3] << 24;
}

/* The panel the loader drove this boot, from its display record (the A32
 * loader's struct baseos_native_display, little-endian). 0 old, 1 boe,
 * -1 no record, -2 a record that cannot be trusted. A zero-length logo means
 * the loader lit the stock blank framebuffer. */
static int loader_panel(const uint8_t running[32])
{
    uint8_t record[DISPLAY_BYTES + 1];
    int fd = open(BOOT_DISPLAY, O_RDONLY | O_CLOEXEC | O_NOFOLLOW);
    if (fd < 0) return errno == ENOENT ? -1 : -2;
    size_t used = 0;
    while (used < sizeof(record)) {
        ssize_t count = read(fd, record + used, sizeof(record) - used);
        if (count < 0 && errno == EINTR) continue;
        if (count < 0) { close(fd); return -2; }
        if (!count) break;
        used += (size_t)count;
    }
    close(fd);
    if (used != DISPLAY_BYTES) return -2;
    uint32_t hash = UINT32_C(2166136261);
    for (size_t i = 0; i < DISPLAY_BYTES; i++)
        hash = (hash ^ (i >= 12 && i < 16 ? 0 : record[i])) * UINT32_C(16777619);
    uint32_t panel = le32(record + 16);
    int shown = 0, reserved = 0;
    for (size_t i = 64; i < 72; i++) shown |= record[i];
    for (size_t i = 72; i < 80; i++) reserved |= record[i];
    if (le32(record) != DISPLAY_MAGIC || le32(record + 4) != 1 || le32(record + 8) != DISPLAY_BYTES ||
        le32(record + 12) != hash || panel > 1 ||
        (le32(record + 20) && le32(record + 20) < 54) ||
        (!le32(record + 20) && le32(record + 24)) || !shown || reserved ||
        memcmp(record + 32, running, 32)) return -2;
    return (int)panel;
}

/* The kernel's own lcd_type: exactly one token, old or boe. 0 old, 1 boe. */
static int kernel_panel(void)
{
    char command[4096];
    if (!read_text(CMDLINE, command, sizeof(command))) return -1;
    const size_t key = sizeof("lcd_type=") - 1;
    int found = -1;
    for (char *word = command; *word;) {
        while (isspace((unsigned char)*word)) word++;
        char *end = word;
        while (*end && !isspace((unsigned char)*end)) end++;
        if ((size_t)(end - word) >= key && !memcmp(word, "lcd_type=", key)) {
            if (found >= 0 || (size_t)(end - word) != key + 3) return -1;
            if (!memcmp(word + key, "old", 3)) found = 0;
            else if (!memcmp(word + key, "boe", 3)) found = 1;
            else return -1;
        }
        word = end;
    }
    return found;
}

/* No per-card panel file: the panel is detected on every boot. The kernel's
 * lcd_type must be one the loader could have chosen, and when the loader
 * published a display record it must name that same panel. */
static int panel_matches(const uint8_t running[32])
{
    int kernel = kernel_panel(), loader = loader_panel(running);
    return kernel >= 0 && loader != -2 && (loader == -1 || loader == kernel);
}

static int charger_boot(void)
{
    char mode[32];
    if (read_text(BOOT_MODE, mode, sizeof(mode))) {
        char *value = mode;
        while (isspace((unsigned char)*value)) value++;
        char *end = value + strlen(value);
        while (end > value && isspace((unsigned char)end[-1])) *--end = 0;
        if (!strcmp(value, "0")) return 0;
        if (!strcmp(value, "1")) return 1;
    }
    /* Older kernels expose only cmdline. Never accept a token prefix. */
    char command[4096];
    if (!read_text(CMDLINE, command, sizeof(command))) return 0;
    for (char *word = command; *word;) {
        while (isspace((unsigned char)*word)) word++;
        char *end = word;
        while (*end && !isspace((unsigned char)*end)) end++;
        if ((size_t)(end - word) == sizeof("bootreason=charger") - 1 &&
            !memcmp(word, "bootreason=charger", sizeof("bootreason=charger") - 1)) return 1;
        word = end;
    }
    return 0;
}

static uint32_t rtc_read(void *context, enum attempt_word word)
{
    volatile uint32_t *rtc = context;
    return rtc[(0x138 / 4) + word];
}

static void rtc_write(void *context, enum attempt_word word, uint32_t value)
{
    volatile uint32_t *rtc = context;
    rtc[(0x138 / 4) + word] = value;
}

static void rtc_fence(void *context)
{
    (void)context;
    NATIVE_HEALTH_FENCE();
}

static int owns_watchdog(const struct registers *registers)
{
    return registers->timer[WDT_CONFIG] == 1 && registers->timer[WDT_MODE] == 0xb1;
}

static int display_ready(const struct fb_var_screeninfo *geometry)
{
    int fd = open("/dev/mem", O_RDONLY | O_SYNC | O_CLOEXEC);
    if (fd < 0) return 0;
    volatile uint32_t *top = mmap(NULL, MAP_BYTES, PROT_READ, MAP_SHARED, fd, DE_TOP_BASE);
    close(fd);
    if (top == MAP_FAILED) return 0;
    uint32_t size = (geometry->xres - 1) | ((geometry->yres - 1) << 16);
    /* A valid fb ioctl did not catch the observed zero-gated green screen. */
    int ready = (top[0] & 1) && (top[1] & 1) && (top[2] & 1) &&
                (top[0x100 / 4] & 1) && top[0x108 / 4] == size;
    munmap((void *)top, MAP_BYTES);
    return ready;
}

/* What the last readiness check was still waiting for, for the failure message. */
static const char *not_ready = "nothing checked yet";

static int platform_ready(enum ready_mode mode)
{
    not_ready = "the system libraries (/lib/ld-linux-aarch64.so.1)";
    if (access("/lib/ld-linux-aarch64.so.1", F_OK)) return 0;
    if (mode == NORMAL_BOOT) {
        not_ready = "the GPU (/dev/mali0)";
        int gpu = open("/dev/mali0", O_RDWR | O_NONBLOCK | O_CLOEXEC | O_NOFOLLOW);
        if (gpu < 0) return 0;
        close(gpu);
    }
    not_ready = "the framebuffer (/dev/fb0)";
    int framebuffer = open("/dev/fb0", O_RDONLY | O_NONBLOCK | O_CLOEXEC | O_NOFOLLOW);
    if (framebuffer < 0) return 0;
    struct fb_var_screeninfo geometry;
    int valid = !ioctl(framebuffer, FBIOGET_VSCREENINFO, &geometry) &&
        geometry.xres && geometry.yres && geometry.xres <= 4096 && geometry.yres <= 4096 &&
        geometry.xres_virtual >= geometry.xres && geometry.yres_virtual >= geometry.yres &&
        (geometry.bits_per_pixel == 16 || geometry.bits_per_pixel == 24 || geometry.bits_per_pixel == 32);
    close(framebuffer);
    if (!valid) return 0;
    not_ready = "the display engine (not running at the framebuffer's size)";
    if (!display_ready(&geometry)) return 0;
    not_ready = "the buttons (no working /dev/input/event*)";
    glob_t inputs = {0};
    if (glob("/dev/input/event*", 0, NULL, &inputs)) { globfree(&inputs); return 0; }
    valid = 0;
    for (size_t i = 0; i < inputs.gl_pathc && !valid; i++) {
        int fd = open(inputs.gl_pathv[i], O_RDONLY | O_NONBLOCK | O_CLOEXEC | O_NOFOLLOW);
        if (fd < 0) continue;
        int version = 0;
        valid = !ioctl(fd, EVIOCGVERSION, &version) && version > 0;
        close(fd);
    }
    globfree(&inputs);
    if (valid) not_ready = "nothing";
    return valid;
}

static int64_t milliseconds(void)
{
    struct timespec value;
    if (clock_gettime(CLOCK_MONOTONIC, &value)) return -1;
    return value.tv_sec * INT64_C(1000) + value.tv_nsec / 1000000;
}

static int accept_boot(int write_ack, enum ready_mode mode,
                       const uint8_t running[32], const uint8_t installed[32])
{
    struct registers registers;
    if (!map_registers(&registers, 0)) return 1;
    struct attempt_io io = {(void *)registers.rtc, rtc_read, rtc_write, rtc_fence};
    struct attempt_pair pending = {generation_cookie(installed), ATTEMPT_PENDING};
    struct attempt_pair before = pair_read(&io);
    int result = 4;
    if (!before.cookie && !before.marker) {
        /* A repeated invocation never disarms a watchdog without its pair. */
        puts("native-health: already acknowledged; watchdog unchanged");
        result = 0;
        goto read_done;
    }
    if (!pair_equal(before, pending) || !owns_watchdog(&registers)) goto read_done;
    int64_t started = milliseconds();
    if (started < 0) { result = 1; goto read_done; }
    for (;;) {
        if (!pair_equal(pair_read(&io), pending) || !owns_watchdog(&registers)) goto read_done;
        int64_t now = milliseconds();
        if (now < 0 || now - started >= READY_TIMEOUT_MS) {
            result = 6;
            goto read_done;
        }
        if (platform_ready(mode)) break;
        if (!write_ack) {
            result = 6;
            goto read_done;
        }
        const struct timespec pause = {0, 100000000};
        if (nanosleep(&pause, NULL) && errno != EINTR) { result = 1; goto read_done; }
    }
    if (!write_ack) { puts("native-health: ready; read-only check"); result = 0; goto read_done; }
    unmap_registers(&registers);
    if (!map_registers(&registers, 1)) return 1;
    io.context = (void *)registers.rtc;
    if (!pair_equal(pair_read(&io), pending) || !owns_watchdog(&registers)) goto write_done;
    if (attempt_ack(&io, 1, running, installed) != IO_COMPLETE) { result = 5; goto write_done; }
    if (!owns_watchdog(&registers)) goto write_done;
    /* Pair clear has completed/read back. End only this SPL-owned watchdog. */
    registers.timer[WDT_MODE] = 0;
    NATIVE_HEALTH_FENCE();
    result = registers.timer[WDT_MODE] == 0 && registers.timer[WDT_CONFIG] == 1 ? 0 : 5;
    if (result && owns_watchdog(&registers)) {
        const struct attempt_pair empty = {0, 0};
        if (pair_equal(pair_read(&io), empty))
            (void)attempt_commit(&io, pending.cookie);
    }
    if (!result) puts("native-health: accepted; pending cleared and watchdog stopped");
write_done:
    unmap_registers(&registers);
    return result;
read_done:
    unmap_registers(&registers);
    return result;
}

/* Stock latch for boot-area writes (baseos-boot-migrate). While the pair is
 * nonzero the capsule always starts the embedded stock boot0, so a reset in
 * the middle of a write boots stock. Holding converges any pair to
 * (cookie(installed), pending); once nonzero it never passes through zero.
 * Releasing clears only that exact pair, cookie first, as attempt_ack does. */
enum latch_mode { LATCH_SHOW, LATCH_HOLD, LATCH_RELEASE };

static int write_word(struct attempt_io *io, enum attempt_word word, uint32_t value,
                      struct attempt_pair expected)
{
    io->write(io->context, word, value);
    io->fence(io->context);
    return pair_equal(pair_read(io), expected);
}

static int stock_latch(enum latch_mode mode)
{
    uint8_t installed[32];
    uint32_t cookie;
    if (!read_generation(INSTALLED_GENERATION, installed) || !(cookie = generation_cookie(installed))) {
        fprintf(stderr, "native-health: absent or malformed installed generation\n");
        return 3;
    }
    struct registers registers;
    if (!map_registers(&registers, mode != LATCH_SHOW)) return 1;
    struct attempt_io io = {(void *)registers.rtc, rtc_read, rtc_write, rtc_fence};
    const struct attempt_pair held = {cookie, ATTEMPT_PENDING}, empty = {0, 0};
    struct attempt_pair pair = pair_read(&io);
    int result = 0;
    if (mode == LATCH_SHOW) {
        printf("%08x %08x %s\n", (unsigned)pair.cookie, (unsigned)pair.marker,
               pair_equal(pair, held) ? "held" : pair_equal(pair, empty) ? "idle" : "other");
        goto done;
    }
    /* An armed watchdog means a native attempt still owns the pair. */
    if (registers.timer[WDT_MODE] & 1) { result = 4; goto done; }
    if (mode == LATCH_HOLD) {
        if (pair_equal(pair, held)) goto done;
        if (pair_equal(pair, empty)) {
            result = attempt_commit(&io, cookie) == IO_COMPLETE ? 0 : 5;
            goto done;
        }
        io.fence(io.context);
        if (!pair_equal(pair_read(&io), pair)) { result = 5; goto done; }
        if (pair.marker != ATTEMPT_PENDING) {
            pair.marker = ATTEMPT_PENDING;
            if (!write_word(&io, ATTEMPT_MARKER, ATTEMPT_PENDING, pair)) { result = 5; goto done; }
        }
        if (pair.cookie != cookie && !write_word(&io, ATTEMPT_COOKIE, cookie, held)) result = 5;
        goto done;
    }
    if (pair_equal(pair, empty)) goto done;
    if (!pair_equal(pair, held)) { result = 5; goto done; }
    io.fence(io.context);
    const struct attempt_pair clearing = {0, ATTEMPT_PENDING};
    if (!pair_equal(pair_read(&io), held) || !write_word(&io, ATTEMPT_COOKIE, 0, clearing) ||
        !write_word(&io, ATTEMPT_MARKER, 0, empty)) result = 5;
done:
    if (mode != LATCH_SHOW)
        printf("native-health: stock latch %s\n", result ? "unchanged or unverified" :
               mode == LATCH_HOLD ? "held" : "released");
    unmap_registers(&registers);
    return result;
}

int main(int argc, char **argv)
{
    int charger = argc == 2 && !strcmp(argv[1], "--accept-charger");
    int write_ack = charger || (argc == 2 && !strcmp(argv[1], "--accept"));
    int check = argc == 2 && !strcmp(argv[1], "--check");
    enum latch_mode latch = LATCH_SHOW;
    int latching = argc == 2 && (!strcmp(argv[1], "--stock-latch") ||
        (!strcmp(argv[1], "--hold-stock") && (latch = LATCH_HOLD)) ||
        (!strcmp(argv[1], "--release-stock") && (latch = LATCH_RELEASE)));
    if (!write_ack && !check && !latching) {
        fprintf(stderr, "usage: native-health --check|--accept|--accept-charger|"
                        "--stock-latch|--hold-stock|--release-stock\n");
        return 2;
    }
    if (latching) {
        int lock = open(ACK_LOCK, O_CREAT | O_RDWR | O_CLOEXEC | O_NOFOLLOW, 0600);
        if (lock < 0 || flock(lock, LOCK_EX | LOCK_NB)) {
            if (lock >= 0) close(lock);
            fprintf(stderr, "native-health: another supervisor owns the pair\n");
            return 4;
        }
        int result = stock_latch(latch);
        close(lock);
        return result;
    }
    uint8_t running[32], installed[32];
    if (!read_generation(FDT_GENERATION, running) || !read_generation(INSTALLED_GENERATION, installed) ||
        memcmp(running, installed, 32) || !generation_cookie(installed)) {
        fprintf(stderr, "native-health: absent, malformed or mismatched generation\n");
        return 3;
    }
    if (!panel_matches(running)) {
        fprintf(stderr, "native-health: kernel lcd_type is malformed or differs from the loader's panel\n");
        return 3;
    }
    if (charger && !charger_boot()) {
        fprintf(stderr, "native-health: charger acceptance requires a latched charger boot\n");
        return 4;
    }
    int lock = -1;
    if (write_ack) {
        lock = open(ACK_LOCK, O_CREAT | O_RDWR | O_CLOEXEC | O_NOFOLLOW, 0600);
        if (lock < 0 || flock(lock, LOCK_EX | LOCK_NB)) {
            if (lock >= 0) close(lock);
            fprintf(stderr, "native-health: another supervisor owns acceptance\n");
            return 4;
        }
    }
    int result = accept_boot(write_ack, charger ? CHARGER_BOOT : NORMAL_BOOT, running, installed);
    if (lock >= 0) close(lock);
    if (result == 6)
        fprintf(stderr, "native-health: acceptance refused (6): still waiting for %s after %d s; "
                        "watchdog not reloaded\n", not_ready, READY_TIMEOUT_MS / 1000);
    else if (result)
        fprintf(stderr, "native-health: acceptance refused (%d: %s); watchdog not reloaded\n", result,
                result == 4 ? "the start record or the watchdog is not this loader's" :
                result == 5 ? "the start record did not clear" : "the chip registers cannot be read");
    return result;
}
