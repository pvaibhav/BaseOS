/* Read RTC general-purpose registers; restore only the two lab trace words. */
#include <errno.h>
#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>

enum { GP14 = 0x138 / 4, GP15 = 0x13c / 4 };

static int parse_hex32(const char *text, uint32_t *value)
{
    if (text[0] == '0' && (text[1] == 'x' || text[1] == 'X')) text += 2;
    size_t length = strlen(text);
    if (!length || length > 8) return 0;
    uint32_t result = 0;
    for (size_t i = 0; i < length; i++) {
        unsigned digit;
        if (text[i] >= '0' && text[i] <= '9') digit = text[i] - '0';
        else if (text[i] >= 'a' && text[i] <= 'f') digit = text[i] - 'a' + 10;
        else if (text[i] >= 'A' && text[i] <= 'F') digit = text[i] - 'A' + 10;
        else return 0;
        result = (result << 4) | digit;
    }
    *value = result;
    return 1;
}

static int restore_pair(volatile uint32_t *rtc, const uint32_t expected[2],
                        const uint32_t original[2])
{
    if (rtc[GP14] != expected[0] || rtc[GP15] != expected[1]) return 3;
    __sync_synchronize();
    if (rtc[GP14] != expected[0] || rtc[GP15] != expected[1]) return 3;
    rtc[GP14] = original[0];
    __sync_synchronize();
    if (rtc[GP14] != original[0]) return 4;
    /* The two MMIO writes cannot be atomic. Refuse a changed second word. */
    if (rtc[GP15] != expected[1]) return 3;
    rtc[GP15] = original[1];
    __sync_synchronize();
    return rtc[GP14] == original[0] && rtc[GP15] == original[1] ? 0 : 4;
}

int main(int argc, char **argv)
{
    int single = argc == 3 && !strcmp(argv[1], "--restore");
    int pair = argc == 6 && !strcmp(argv[1], "--restore-pair");
    if (argc != 1 && !single && !pair) return 2;
    uint32_t expected[2] = {0}, original[2] = {0};
    if (single && !parse_hex32(argv[2], &original[1])) return 2;
    if (pair && (!parse_hex32(argv[2], &expected[0]) ||
                 !parse_hex32(argv[3], &expected[1]) ||
                 !parse_hex32(argv[4], &original[0]) ||
                 !parse_hex32(argv[5], &original[1]))) return 2;
    int restore = single || pair;
    int fd = open("/dev/mem", (restore ? O_RDWR : O_RDONLY) | O_SYNC);
    if (fd < 0) { perror("open"); return 1; }
    volatile uint32_t *rtc = mmap(NULL, 4096, PROT_READ | (restore ? PROT_WRITE : 0),
                                  MAP_SHARED, fd, 0x07000000);
    close(fd);
    if (rtc == MAP_FAILED) { perror("mmap"); return 1; }
    for (unsigned i = 0; i < 16; i++) printf("gp%u=%08x\n", i, rtc[0x100 / 4 + i]);
    int result = 0;
    if (pair) {
        result = restore_pair(rtc, expected, original);
        if (result) fprintf(stderr, "pair restore refused or incomplete: gp14=%08x gp15=%08x\n",
                            rtc[GP14], rtc[GP15]);
    } else if (single) {
        uint32_t current = rtc[GP15];
        if ((current & 0xffff0000u) != 0x42530000u && current != 0x4a000000u) return 3;
        __sync_synchronize();
        if (rtc[GP15] != current) return 3;
        rtc[GP15] = original[1];
        __sync_synchronize();
        if (rtc[GP15] != original[1]) result = 4;
    }
    munmap((void *)rtc, 4096);
    return result;
}
