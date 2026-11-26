/* Bounded recovery probe. Inspection is read-only; reset requires --test-reset.
 * Register layout matches this vendor U-Boot's reset_cpu at +0x1994 and the
 * sun6i watchdog. This does not use RTC state or modify boot firmware.
 */
#include <errno.h>
#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <sys/mman.h>
#include <time.h>
#include <unistd.h>

static double seconds(void)
{
    struct timespec value;
    clock_gettime(CLOCK_MONOTONIC, &value);
    return value.tv_sec + value.tv_nsec / 1e9;
}

int main(int argc, char **argv)
{
    int fd = open("/dev/mem", O_RDWR | O_SYNC);
    if (fd < 0) { perror("open /dev/mem"); return 1; }
    volatile uint32_t *timer = mmap(NULL, 4096, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0x03009000);
    close(fd);
    if (timer == MAP_FAILED) { perror("mmap"); return 1; }
    uint32_t config = timer[0xb4 / 4], mode = timer[0xb8 / 4];
    printf("wdt_config=%08x wdt_mode=%08x\n", config, mode);
    fflush(stdout);
    if (argc == 1) return 0;
    if (argc == 2 && !strcmp(argv[1], "--reload")) {
        if (mode & 1) {
            timer[0xb0 / 4] = 0x14af;
            __sync_synchronize();
        }
        printf("wdt_mode_after_reload=%08x\n", timer[0xb8 / 4]);
        return timer[0xb8 / 4] == mode ? 0 : 7;
    }
    if (argc == 2 && !strcmp(argv[1], "--disarm")) {
        timer[0xb8 / 4] = 0;
        __sync_synchronize();
        printf("wdt_mode_after_stop=%08x\n", timer[0xb8 / 4]);
        return (timer[0xb8 / 4] & 1) ? 6 : 0;
    }
    if (argc != 2 || strcmp(argv[1], "--test-reset")) return 2;
    if (mode & 1) {
        fprintf(stderr, "refusing: watchdog already running\n");
        return 3;
    }
    timer[0xb4 / 4] = (config & ~3u) | 1u;
    timer[0xb8 / 4] = (mode & ~0xf1u) | 0x21u; /* two seconds, whole-system reset */
    timer[0xb0 / 4] = 0x14af;
    __sync_synchronize();
    double deadline = seconds() + 6;
    while (seconds() < deadline) usleep(10000);
    timer[0xb8 / 4] = mode;
    timer[0xb4 / 4] = config;
    __sync_synchronize();
    fprintf(stderr, "watchdog did not reset; original registers restored\n");
    return 5;
}
