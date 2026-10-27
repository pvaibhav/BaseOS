/* Execute the real CLI against raw generation files and modeled device MMIO. */
#define _GNU_SOURCE
#include <assert.h>
#include <errno.h>
#include <fcntl.h>
#include <glob.h>
#include <linux/fb.h>
#include <linux/input.h>
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/file.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <time.h>
#include <unistd.h>

static int device_open(const char *, int, ...);
static int device_close(int);
static void *device_mmap(void *, size_t, int, int, int, off_t);
static int device_munmap(void *, size_t);
static int device_flock(int, int);
static int ready_access(const char *, int);
static int input_glob(const char *, int, int (*)(const char *, int), glob_t *);
static void input_globfree(glob_t *);
static int device_ioctl(int, unsigned long, ...);
static int modeled_clock(clockid_t, struct timespec *);
static int modeled_sleep(const struct timespec *, struct timespec *);
static void observed_fence(void);

#define main native_health_main
#define open device_open
#define close device_close
#define mmap device_mmap
#define munmap device_munmap
#define flock device_flock
#define access ready_access
#define glob input_glob
#define globfree input_globfree
#define ioctl device_ioctl
#define clock_gettime modeled_clock
#define nanosleep modeled_sleep
#define NATIVE_HEALTH_FENCE() observed_fence()
#include "../src/native-health.c"
#undef main
#undef open
#undef close
#undef mmap
#undef munmap
#undef flock
#undef access
#undef glob
#undef globfree
#undef ioctl
#undef clock_gettime
#undef nanosleep

static const uint8_t generation[32] = {51,220,138,33,20,40,7,162,30,111,157,197,174,134,27,55,169,245,83,55,112,92,10,232,100,149,235,63,156,13,133,19};
static const uint8_t collision[32] = {74,182,81,115,117,35,49,238,99,152,42,204,195,108,209,183,211,253,18,153,87,110,90,135,242,200,175,231,206,148,237,203};
static char directory[128], path[256];
static uint32_t rtc[128], timer[128], previous_rtc[128], previous_timer[128], entry_timer[128];
static uint32_t display[1024], initial_display[1024];
struct change { int area; unsigned index; uint32_t value; };
static struct change changes[16];
static unsigned change_count, fences, writable_maps, memory_opens, probes;
static int busy, gpu_missing, input_missing, bad_geometry, loader_missing;
static int startup_fixture;
static int64_t now_ms, ready_at, watchdog_changes_at;
enum fault { NONE, DROP_COOKIE, DROP_MARKER, CHANGE_WDT_AFTER_CLEAR, DROP_DISARM,
             CHANGE_PAIR_ON_REMAP, CHANGE_PAIR_DURING_DISARM, CHANGE_WDT_DURING_DISARM };
static enum fault injected;

static const char *file_path(const char *name)
{
    assert(snprintf(path, sizeof(path), "%s/%s", directory, name) > 0);
    return path;
}
static void put_file(const char *name, const void *value, size_t length)
{
    const char *name_path = file_path(name);
    unlink(name_path);
    int fd = open(name_path, O_CREAT | O_TRUNC | O_WRONLY, 0600);
    assert(fd >= 0 && write(fd, value, length) == (ssize_t)length && !close(fd));
}
static int device_open(const char *name, int flags, ...)
{
    if (startup_fixture && (!strcmp(name, FDT_GENERATION) || !strcmp(name, INSTALLED_GENERATION) ||
                            !strcmp(name, BOOT_MODE) || !strcmp(name, CMDLINE) ||
                            !strcmp(name, BOOT_DISPLAY))) return open(name, flags);
    if (!strcmp(name, "/dev/mem")) { memory_opens++; return 1000; }
    if (!strcmp(name, "/dev/mali0")) { probes++; return gpu_missing ? -1 : 1001; }
    if (!strcmp(name, "/dev/fb0")) return 1002;
    if (!strcmp(name, "/dev/input/event0")) return 1003;
    const char *redirect = NULL;
    if (!strcmp(name, FDT_GENERATION)) redirect = file_path("fdt");
    else if (!strcmp(name, INSTALLED_GENERATION)) redirect = file_path("installed");
    else if (!strcmp(name, BOOT_MODE)) redirect = file_path("boot-mode");
    else if (!strcmp(name, CMDLINE)) redirect = file_path("cmdline");
    else if (!strcmp(name, BOOT_DISPLAY)) redirect = file_path("display-record");
    else if (!strcmp(name, ACK_LOCK)) redirect = file_path("lock");
    else assert(!"unexpected path, network or frontend dependency");
    if (flags & O_CREAT) return open(redirect, flags, 0600);
    return open(redirect, flags);
}
static int device_close(int fd) { return fd >= 1000 ? 0 : close(fd); }
static void *device_mmap(void *address, size_t length, int protection, int flags, int fd, off_t offset)
{
    assert(!address && length == MAP_BYTES && flags == MAP_SHARED && fd == 1000);
    if (protection & PROT_WRITE) {
        writable_maps++;
        assert(now_ms >= ready_at && !bad_geometry && !input_missing && !loader_missing);
        if (injected == CHANGE_PAIR_ON_REMAP && offset == RTC_BASE) rtc[0x13c / 4] ^= 1;
    }
    if (offset == DE_TOP_BASE) {
        assert(protection == PROT_READ);
        return display;
    }
    assert(offset == RTC_BASE || offset == TIMER_BASE);
    return offset == RTC_BASE ? rtc : timer;
}
static int device_munmap(void *address, size_t length)
{
    assert((address == rtc || address == timer || address == display) && length == MAP_BYTES);
    return 0;
}
static int device_flock(int fd, int operation)
{
    assert(fd >= 0 && operation == (LOCK_EX | LOCK_NB));
    if (busy) { errno = EWOULDBLOCK; return -1; }
    return 0;
}
static int ready_access(const char *name, int mode)
{
    assert(mode == F_OK);
    assert(!strcmp(name, "/lib/ld-linux-aarch64.so.1"));
    return loader_missing || now_ms < ready_at ? -1 : 0;
}
static int input_glob(const char *pattern, int flags, int (*error)(const char *, int), glob_t *result)
{
    assert(!strcmp(pattern, "/dev/input/event*") && !flags && !error);
    if (input_missing) return GLOB_NOMATCH;
    result->gl_pathc = 1;
    result->gl_pathv = calloc(2, sizeof(char *));
    assert(result->gl_pathv);
    result->gl_pathv[0] = strdup("/dev/input/event0");
    assert(result->gl_pathv[0]);
    return 0;
}
static void input_globfree(glob_t *result)
{
    for (size_t i = 0; i < result->gl_pathc; i++) free(result->gl_pathv[i]);
    free(result->gl_pathv);
}
static int device_ioctl(int fd, unsigned long request, ...)
{
    va_list args;
    va_start(args, request);
    if (fd == 1002) {
        assert(request == FBIOGET_VSCREENINFO);
        struct fb_var_screeninfo *geometry = va_arg(args, void *);
        memset(geometry, 0, sizeof(*geometry));
        geometry->xres = bad_geometry ? 0 : 720;
        geometry->yres = 480;
        geometry->xres_virtual = 720;
        geometry->yres_virtual = 960;
        geometry->bits_per_pixel = 32;
    } else {
        assert(fd == 1003 && request == EVIOCGVERSION);
        *va_arg(args, int *) = 0x10001;
    }
    va_end(args);
    return 0;
}
static int modeled_clock(clockid_t clock, struct timespec *value)
{
    assert(clock == CLOCK_MONOTONIC);
    value->tv_sec = now_ms / 1000;
    value->tv_nsec = now_ms % 1000 * 1000000;
    return 0;
}
static int modeled_sleep(const struct timespec *delay, struct timespec *remaining)
{
    assert(delay->tv_sec == 0 && delay->tv_nsec == 100000000 && !remaining);
    now_ms += 100;
    if (watchdog_changes_at >= 0 && now_ms >= watchdog_changes_at) timer[WDT_MODE] = 0x91;
    return 0;
}
static void observed_fence(void)
{
    fences++;
    for (unsigned i = 0; i < 128; i++) {
        if (rtc[i] != previous_rtc[i]) {
            assert(i == 0x138 / 4 || i == 0x13c / 4);
            assert(change_count < 16);
            changes[change_count++] = (struct change){0, i, rtc[i]};
        }
        if (timer[i] != previous_timer[i]) {
            assert(i == WDT_MODE && timer[i] == 0);
            assert(change_count < 16);
            changes[change_count++] = (struct change){1, i, timer[i]};
        }
    }
    if (injected == DROP_COOKIE && !rtc[0x138 / 4]) rtc[0x138 / 4] = generation_cookie(generation);
    if (injected == DROP_MARKER && !rtc[0x13c / 4]) rtc[0x13c / 4] = ATTEMPT_PENDING;
    if (injected == CHANGE_WDT_AFTER_CLEAR && !rtc[0x13c / 4]) timer[WDT_MODE] = 0x91;
    if (injected == DROP_DISARM && !timer[WDT_MODE]) timer[WDT_MODE] = 0xb1;
    if (injected == CHANGE_PAIR_DURING_DISARM && !timer[WDT_MODE]) {
        timer[WDT_MODE] = 0xb1;
        rtc[0x138 / 4] = 0x11111111;
        rtc[0x13c / 4] = 0x22222222;
    }
    if (injected == CHANGE_WDT_DURING_DISARM && !timer[WDT_MODE]) timer[WDT_MODE] = 0x91;
    memcpy(previous_rtc, rtc, sizeof(rtc));
    memcpy(previous_timer, timer, sizeof(timer));
}
static void initialize(void)
{
    assert(generation_cookie(generation) == generation_cookie(collision));
    for (unsigned i = 0; i < 128; i++) rtc[i] = timer[i] = 0x70000000u + i;
    rtc[0x138 / 4] = generation_cookie(generation);
    rtc[0x13c / 4] = ATTEMPT_PENDING;
    timer[WDT_CONFIG] = 1;
    timer[WDT_MODE] = 0xb1;
    memset(display, 0, sizeof(display));
    display[0] = display[1] = display[2] = display[0x100 / 4] = 1;
    display[0x108 / 4] = 0x01df02cf;
    memcpy(previous_rtc, rtc, sizeof(rtc));
    memcpy(previous_timer, timer, sizeof(timer));
    changes[0] = (struct change){0};
    change_count = fences = writable_maps = memory_opens = probes = 0;
    busy = gpu_missing = input_missing = bad_geometry = loader_missing = 0;
    now_ms = ready_at = 0;
    watchdog_changes_at = -1;
    injected = NONE;
    put_file("fdt", generation, 32);
    put_file("installed", generation, 32);
    put_file("boot-mode", "0\n", 2);
    put_file("cmdline", "console=ttyS0 lcd_type=old\n", sizeof("console=ttyS0 lcd_type=old\n") - 1);
    unlink(file_path("display-record"));
}
static int run(const char *mode)
{
    char *args[] = {"native-health", (char *)mode, NULL};
    memcpy(entry_timer, timer, sizeof(timer));
    memcpy(initial_display, display, sizeof(display));
    int result = native_health_main(2, args);
    for (unsigned i = 0; i < 128; i++) {
        if (i != 0x138 / 4 && i != 0x13c / 4) assert(rtc[i] == 0x70000000u + i);
        if (i != WDT_CONFIG && i != WDT_MODE) assert(timer[i] == 0x70000000u + i);
    }
    assert(!memcmp(display, initial_display, sizeof(display)));
    return result;
}
static void unchanged(void)
{
    assert(!change_count && !writable_maps);
    if (watchdog_changes_at >= 0 && now_ms >= watchdog_changes_at) entry_timer[WDT_MODE] = 0x91;
    assert(!memcmp(timer, entry_timer, sizeof(timer)));
    assert(rtc[0x138 / 4] == generation_cookie(generation) && rtc[0x13c / 4] == ATTEMPT_PENDING);
}

static void seal_record(uint8_t record[80])
{
    memset(record + 12, 0, 4);
    uint32_t hash = 2166136261u;
    for (unsigned i = 0; i < 80; i++) hash = (hash ^ record[i]) * 16777619u;
    memcpy(record + 12, &hash, 4);
}
static void make_record(uint8_t record[80], uint32_t panel, const uint8_t owner[32])
{
    const uint32_t words[] = {0x31505344u, 1, 80, 0, panel, 921654, 0x12345678u, 0x9abcdef0u};
    memset(record, 0, 80);
    memcpy(record, words, sizeof(words));
    memcpy(record + 32, owner, 32);
    const uint64_t shown = 36312000;
    memcpy(record + 64, &shown, 8);
    seal_record(record);
}
static void put_record(uint32_t panel, const uint8_t owner[32])
{
    uint8_t record[80];
    make_record(record, panel, owner);
    put_file("display-record", record, 80);
}

static void settle(void)
{
    memcpy(previous_rtc, rtc, sizeof(rtc));
    memcpy(previous_timer, timer, sizeof(timer));
}

static void cleanup(void)
{
    assert(!unlink(file_path("fdt")) && !unlink(file_path("installed")));
    assert(!unlink(file_path("boot-mode")) && !unlink(file_path("cmdline")));
    unlink(file_path("display-record"));
    unlink(file_path("lock"));
    assert(!rmdir(directory));
}

int main(int argc, char **argv)
{
    char temporary[] = "/tmp/baseos-native-health.XXXXXX";
    assert(mkdtemp(temporary));
    assert(snprintf(directory, sizeof(directory), "%s", temporary) > 0);
    if (argc == 2) {
        /* The staged rcS runs the real CLI with raw files and fake hardware. */
        startup_fixture = 1;
        initialize();
        gpu_missing = getenv("HEALTH_MODEL_GPU_MISSING") != NULL;
        if (getenv("HEALTH_MODEL_BAD_DE")) {
            memset(display, 0, sizeof(display));
            display[0xc / 4] = 0xc;
        }
        int result = run(argv[1]);
        if (result || !strcmp(argv[1], "--check")) unchanged();
        else assert(change_count == 3 && !rtc[0x138 / 4] && !rtc[0x13c / 4] && !timer[WDT_MODE]);
        cleanup();
        return result;
    }
    assert(argc == 1);
    const char *panel_modes[] = {"--check", "--accept", "--accept-charger"};
    for (unsigned i = 0; i < sizeof(panel_modes) / sizeof(panel_modes[0]); i++) {
        /* The regression: healthy GPU/DE/input, but the kernel was told another
         * panel than the one the loader drove. That must not ack. */
        initialize(); put_file("boot-mode", "1\n", 2); put_record(1, generation);
        assert(run(panel_modes[i]) == 3 && !memory_opens); unchanged();
        initialize(); put_file("boot-mode", "1\n", 2); put_record(0, generation);
        put_file("cmdline", "lcd_type=boe\n", sizeof("lcd_type=boe\n") - 1);
        assert(run(panel_modes[i]) == 3 && !memory_opens); unchanged();
        initialize(); put_file("boot-mode", "1\n", 2); put_record(1, generation);
        put_file("cmdline", "quiet\tlcd_type=boe\tloglevel=0\n", sizeof("quiet\tlcd_type=boe\tloglevel=0\n") - 1);
        assert(run(panel_modes[i]) == 0);
        /* A lit blank framebuffer still authenticates the chosen panel. */
        initialize(); put_file("boot-mode", "1\n", 2);
        uint8_t blank[80]; make_record(blank, 0, generation);
        memset(blank + 20, 0, 8); seal_record(blank);
        put_file("display-record", blank, sizeof(blank));
        assert(run(panel_modes[i]) == 0);
        if (!strcmp(panel_modes[i], "--check")) unchanged();
        else assert(change_count == 3 && !rtc[0x138 / 4] && !rtc[0x13c / 4] && !timer[WDT_MODE]);
        /* No logo, no record: the loader's own lcd_type stands. */
        initialize(); put_file("boot-mode", "1\n", 2);
        put_file("cmdline", "lcd_type=boe\n", sizeof("lcd_type=boe\n") - 1);
        assert(run(panel_modes[i]) == 0);
    }
    const char *bad_panels[] = {"quiet\n", "lcd_type=old lcd_type=old\n", "lcd_type=old lcd_type=boe\n",
                               "lcd_type=boe lcd_type=old\n", "lcd_type=old-extra\n", "xlcd_type=old\n",
                               "lcd_type=\n", "lcd_type= old\n", "lcd_type=OLD\n"};
    for (unsigned i = 0; i < sizeof(bad_panels) / sizeof(bad_panels[0]); i++) {
        initialize(); put_file("cmdline", bad_panels[i], strlen(bad_panels[i]));
        assert(run("--accept") == 3 && !memory_opens); unchanged();
        initialize(); put_file("cmdline", bad_panels[i], strlen(bad_panels[i])); put_record(0, generation);
        assert(run("--accept") == 3 && !memory_opens); unchanged();
    }
    /* A damaged or foreign record is not trusted, whatever lcd_type says. */
    for (unsigned field = 0; field < 11; field++) {
        initialize(); put_record(0, generation);
        uint8_t record[80]; make_record(record, 0, generation);
        switch (field) {
        case 0: record[0] ^= 1; break;                       /* magic */
        case 1: record[4] = 2; break;                        /* version */
        case 2: record[8] = 79; break;                       /* size field */
        case 3: record[12] ^= 1; break;                      /* checksum */
        case 4: record[16] = 2; break;                       /* panel out of range */
        case 5: memset(record + 20, 0, 4); record[20] = 53; break;     /* logo shorter than a BMP header */
        case 6: memset(record + 64, 0, 8); break;            /* never shown */
        case 7: record[72] = 1; break;                       /* reserved */
        case 8: record[32] ^= 1; break;                      /* another generation */
        case 9: put_file("display-record", record, 79); break;
        default: put_file("display-record", record, 80); { int fd = open(file_path("display-record"), O_WRONLY | O_APPEND);
                 assert(fd >= 0 && write(fd, "", 1) == 1 && !close(fd)); } break;
        }
        if (field != 3 && field < 9) seal_record(record);
        if (field < 9) put_file("display-record", record, 80);
        assert(run("--accept") == 3 && !memory_opens); unchanged();
    }
    initialize(); put_file("display-record", "", 0);
    assert(run("--accept") == 3 && !memory_opens); unchanged();
    initialize(); put_record(0, generation);
    char record_target[256]; assert(snprintf(record_target, sizeof(record_target), "%s/record-target", directory) > 0);
    assert(!rename(file_path("display-record"), record_target) && !symlink(record_target, file_path("display-record")));
    assert(run("--accept") == 3 && !memory_opens); unchanged();
    assert(!unlink(record_target));
    puts("native-health panel matrix passed; mismatches leave MMIO unopened");
    initialize(); assert(run("--accept") == 0);
    assert(change_count == 3 && changes[0].area == 0 && changes[0].index == 0x138 / 4 && !changes[0].value);
    assert(changes[1].area == 0 && changes[1].index == 0x13c / 4 && !changes[1].value);
    assert(changes[2].area == 1 && changes[2].index == WDT_MODE && !changes[2].value);
    assert(!rtc[0x138 / 4] && !rtc[0x13c / 4] && !timer[WDT_MODE]);
    initialize(); assert(run("--check") == 0); unchanged();
    initialize(); ready_at = 500; assert(run("--accept") == 0 && now_ms == 500);
    initialize(); ready_at = 6000; assert(run("--accept") == 6 && now_ms == 6000); unchanged();
    initialize(); gpu_missing = 1; assert(run("--accept") == 6 && now_ms == 6000); unchanged();
    initialize(); put_file("boot-mode", "1\n", 2); gpu_missing = 1;
    assert(run("--accept") == 6 && now_ms == 6000); unchanged();
    initialize(); put_file("boot-mode", "1\n", 2); gpu_missing = 1;
    assert(run("--accept-charger") == 0 && !probes && change_count == 3);
    assert(!rtc[0x138 / 4] && !rtc[0x13c / 4] && !timer[WDT_MODE]);
    initialize(); put_file("cmdline", "bootreason=charger lcd_type=old\n", sizeof("bootreason=charger lcd_type=old\n") - 1);
    assert(run("--accept-charger") == 4 && !memory_opens); unchanged();
    initialize(); assert(!unlink(file_path("boot-mode")));
    put_file("cmdline", "quiet bootreason=charger loglevel=0 lcd_type=old\n", sizeof("quiet bootreason=charger loglevel=0 lcd_type=old\n") - 1); gpu_missing = 1;
    assert(run("--accept-charger") == 0 && !probes);
    initialize(); put_file("boot-mode", "invalid\n", 8);
    put_file("cmdline", "quiet\tbootreason=charger\tloglevel=0\tlcd_type=old\n", sizeof("quiet\tbootreason=charger\tloglevel=0\tlcd_type=old\n") - 1); gpu_missing = 1;
    assert(run("--accept-charger") == 0 && !probes);
    const char *wrong_reasons[] = {"bootreason=charger-extra\n", "xbootreason=charger\n",
                                 "bootreason=normal\n", "quiet\n"};
    for (unsigned i = 0; i < sizeof(wrong_reasons) / sizeof(wrong_reasons[0]); i++) {
        initialize(); assert(!unlink(file_path("boot-mode")));
        char command[128];
        assert(snprintf(command, sizeof(command), "%slcd_type=old\n", wrong_reasons[i]) > 0);
        put_file("cmdline", command, strlen(command));
        assert(run("--accept-charger") == 4 && !memory_opens); unchanged();
    }
    initialize(); put_file("boot-mode", "1\0000", 3);
    assert(run("--accept-charger") == 4 && !memory_opens); unchanged();
    initialize(); bad_geometry = 1; assert(run("--accept") == 6); unchanged();
    /* Actual bad DE capture: fb/GPU/input valid; gates/control/size0, status+c. */
    initialize(); memset(display, 0, sizeof(display)); display[0xc / 4] = 0xc;
    assert(run("--accept") == 6 && now_ms == 6000); unchanged();
    initialize(); put_file("boot-mode", "1\n", 2); gpu_missing = 1;
    memset(display, 0, sizeof(display)); display[0xc / 4] = 0xc;
    assert(run("--accept-charger") == 6 && now_ms == 6000 && !probes); unchanged();
    initialize(); display[0x108 / 4] = 0x01df027f;
    assert(run("--accept") == 6); unchanged();
    initialize(); display[0x100 / 4] = 0;
    assert(run("--accept") == 6); unchanged();
    initialize(); input_missing = 1; assert(run("--accept") == 6); unchanged();
    initialize(); loader_missing = 1; assert(run("--accept") == 6); unchanged();
    initialize(); busy = 1; assert(run("--accept") == 4 && !memory_opens); unchanged();
    initialize(); put_file("fdt", collision, 32); assert(run("--accept") == 3 && !memory_opens); unchanged();
    for (unsigned length = 0; length <= 33; length++) {
        if (length == 32) continue;
        uint8_t bytes[33] = {0}; initialize(); put_file("fdt", bytes, length);
        assert(run("--accept") == 3 && !memory_opens); unchanged();
        initialize(); put_file("installed", bytes, length);
        assert(run("--accept") == 3 && !memory_opens); unchanged();
    }
    initialize(); assert(!unlink(file_path("fdt")));
    char target[256]; assert(snprintf(target, sizeof(target), "%s/installed", directory) > 0);
    assert(!symlink(target, file_path("fdt")));
    assert(run("--accept") == 3 && !memory_opens); unchanged();
    initialize(); timer[WDT_MODE] = 0; assert(run("--accept") == 4); unchanged();
    initialize(); timer[WDT_CONFIG] = 2; assert(run("--accept") == 4); unchanged();
    initialize(); rtc[0x13c / 4] ^= 1; assert(run("--accept") == 4 && !change_count && !writable_maps);
    initialize(); rtc[0x138 / 4] = rtc[0x13c / 4] = 0; assert(run("--accept") == 0 && !change_count && !writable_maps && timer[WDT_MODE] == 0xb1);
    initialize(); ready_at = 500; watchdog_changes_at = 200; assert(run("--accept") == 4); unchanged();
    initialize(); injected = CHANGE_PAIR_ON_REMAP; assert(run("--accept") == 4 && !change_count && timer[WDT_MODE] == 0xb1);
    initialize(); injected = DROP_COOKIE; assert(run("--accept") == 5 && timer[WDT_MODE] == 0xb1 && rtc[0x13c / 4] == ATTEMPT_PENDING);
    initialize(); injected = DROP_MARKER; assert(run("--accept") == 5 && timer[WDT_MODE] == 0xb1 && rtc[0x13c / 4] == ATTEMPT_PENDING);
    initialize(); injected = CHANGE_WDT_AFTER_CLEAR; assert(run("--accept") == 4 && timer[WDT_MODE] == 0x91 && change_count == 2);
    initialize(); injected = DROP_DISARM; assert(run("--accept") == 5 && timer[WDT_MODE] == 0xb1 && change_count == 5);
    assert(rtc[0x138 / 4] == generation_cookie(generation) && rtc[0x13c / 4] == ATTEMPT_PENDING);
    assert(boot_plan((struct attempt_pair){rtc[0x138 / 4], rtc[0x13c / 4]}, 0,
                     generation_cookie(generation)) == STOCK_RECOVERY);
    assert(run("--accept") == 5 && timer[WDT_MODE] == 0xb1 && change_count == 10);
    assert(rtc[0x138 / 4] == generation_cookie(generation) && rtc[0x13c / 4] == ATTEMPT_PENDING);
    initialize(); injected = CHANGE_PAIR_DURING_DISARM; assert(run("--accept") == 5 && timer[WDT_MODE] == 0xb1 && change_count == 3);
    assert(rtc[0x138 / 4] == 0x11111111 && rtc[0x13c / 4] == 0x22222222);
    initialize(); injected = CHANGE_WDT_DURING_DISARM; assert(run("--accept") == 5 && timer[WDT_MODE] == 0x91 && change_count == 3);
    assert(!rtc[0x138 / 4] && !rtc[0x13c / 4]);
    /* Stock latch for boot-area writes: no FDT needed, only the installed generation. */
    const uint32_t cookie = generation_cookie(generation);
    initialize(); timer[WDT_MODE] = 0; rtc[0x138 / 4] = rtc[0x13c / 4] = 0; assert(!unlink(file_path("fdt"))); settle();
    assert(run("--hold-stock") == 0 && change_count == 2);
    assert(changes[0].index == 0x138 / 4 && changes[0].value == cookie);
    assert(changes[1].index == 0x13c / 4 && changes[1].value == ATTEMPT_PENDING);
    change_count = 0; assert(run("--hold-stock") == 0 && !change_count);
    assert(run("--stock-latch") == 0 && !change_count);
    assert(run("--release-stock") == 0 && change_count == 2);
    assert(changes[0].index == 0x138 / 4 && !changes[0].value && changes[1].index == 0x13c / 4 && !changes[1].value);
    assert(!rtc[0x138 / 4] && !rtc[0x13c / 4]);
    change_count = 0; assert(run("--release-stock") == 0 && !change_count);
    /* Taking over another generation's latch never lets the pair pass through zero. */
    const uint32_t foreign[][2] = {{0x2064f435u, ATTEMPT_PENDING}, {0, ATTEMPT_PENDING}, {0x2064f435u, 0},
                                   {0x11111111u, 0x22222222u}, {0, 0x22222222u}};
    for (unsigned i = 0; i < sizeof(foreign) / sizeof(foreign[0]); i++) {
        initialize(); timer[WDT_MODE] = 0; rtc[0x138 / 4] = foreign[i][0]; rtc[0x13c / 4] = foreign[i][1];
        settle();
        uint32_t seen[2] = {foreign[i][0], foreign[i][1]};
        assert(run("--release-stock") == 5 && !change_count);
        assert(run("--hold-stock") == 0 && change_count >= 1 && change_count <= 2);
        for (unsigned c = 0; c < change_count; c++) {
            seen[changes[c].index == 0x13c / 4] = changes[c].value;
            assert(seen[0] || seen[1]);
        }
        assert(rtc[0x138 / 4] == cookie && rtc[0x13c / 4] == ATTEMPT_PENDING);
    }
    /* An armed watchdog is a native attempt in progress: hands off. */
    initialize(); rtc[0x138 / 4] = rtc[0x13c / 4] = 0; settle();
    assert(run("--hold-stock") == 4 && !change_count && !rtc[0x138 / 4]);
    initialize(); assert(run("--release-stock") == 4 && !change_count && rtc[0x138 / 4] == cookie && rtc[0x13c / 4] == ATTEMPT_PENDING);
    initialize(); timer[WDT_MODE] = 0; rtc[0x138 / 4] = rtc[0x13c / 4] = 0; settle(); injected = DROP_MARKER;
    assert(run("--hold-stock") == 5);
    initialize(); timer[WDT_MODE] = 0; settle(); injected = DROP_COOKIE;
    assert(run("--release-stock") == 5 && rtc[0x13c / 4] == ATTEMPT_PENDING);
    initialize(); timer[WDT_MODE] = 0; settle(); busy = 1; assert(run("--hold-stock") == 4 && !memory_opens);
    initialize(); timer[WDT_MODE] = 0; settle(); assert(!unlink(file_path("installed")));
    assert(run("--hold-stock") == 3 && !memory_opens); put_file("installed", generation, 32);
    initialize(); assert(run("--bogus") == 2 && !memory_opens);
    puts("native-health stock latch passed: exact pair, never zero while held");
    cleanup();
    puts("native-health behavior passed: file/MMIO model only; no devices");
    return 0;
}
