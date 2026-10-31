/* Measure sleep granularity without spinning or touching the SD card. */
#include <stdio.h>
#include <time.h>
static long long ns(struct timespec t) { return (long long)t.tv_sec * 1000000000 + t.tv_nsec; }
int main(void) {
    struct timespec res, a, b, delay = { .tv_nsec = 1000000 };
    if (clock_getres(CLOCK_MONOTONIC, &res)) return 1;
    long long total = 0, min = 1000000000, max = 0;
    for (int i = 0; i < 100; ++i) {
        if (clock_gettime(CLOCK_MONOTONIC, &a) || nanosleep(&delay, NULL) || clock_gettime(CLOCK_MONOTONIC, &b)) return 1;
        long long elapsed = ns(b) - ns(a);
        total += elapsed;
        if (elapsed < min) min = elapsed;
        if (elapsed > max) max = elapsed;
    }
    printf("resolution_ns=%lld sleep_1ms_min_ns=%lld mean_ns=%lld max_ns=%lld samples=100\n", ns(res), min, total / 100, max);
    return 0;
}
