/* Host-only proof. It never calls the renamed /dev/mem entry point. */
#include <assert.h>
#define main rtc_trace_device_main
#include "rtc-trace.c"
#undef main

int main(void)
{
    const char *valid[] = {"0", "00000000", "0x08100010", "FFFFFFFF", "0XfFfFfFfF"};
    const uint32_t values[] = {0, 0, 0x08100010, UINT32_MAX, UINT32_MAX};
    for (unsigned i = 0; i < sizeof(values) / sizeof(values[0]); i++) {
        uint32_t value = 0x12345678;
        assert(parse_hex32(valid[i], &value) && value == values[i]);
    }
    const char *invalid[] = {"", "0x", "-1", "+1", " 1", "1 ", "100000000", "0x100000000", "gg", "1z"};
    for (unsigned i = 0; i < sizeof(invalid) / sizeof(invalid[0]); i++) {
        uint32_t value = 0x12345678;
        assert(!parse_hex32(invalid[i], &value) && value == 0x12345678);
    }
    uint32_t rtc[1024], before[1024];
    for (unsigned i = 0; i < 1024; i++) rtc[i] = before[i] = 0x12340000 + i;
    const uint32_t expected[] = {rtc[GP14], rtc[GP15]}, original[] = {0, 0};
    uint32_t wrong[] = {expected[0] ^ 1, expected[1]};
    assert(restore_pair(rtc, wrong, original) == 3 && !memcmp(rtc, before, sizeof(rtc)));
    wrong[0] = expected[0];
    wrong[1] ^= 1;
    assert(restore_pair(rtc, wrong, original) == 3 && !memcmp(rtc, before, sizeof(rtc)));
    assert(restore_pair(rtc, expected, original) == 0);
    before[GP14] = before[GP15] = 0;
    assert(!memcmp(rtc, before, sizeof(rtc)));
    /* Replaying stale expectations must not overwrite even the owned words. */
    assert(restore_pair(rtc, expected, original) == 3 && !memcmp(rtc, before, sizeof(rtc)));
    assert(restore_pair(rtc, original, original) == 0 && !memcmp(rtc, before, sizeof(rtc)));
    puts("RTC trace parser and bounded expected-value restore proof passed");
    return 0;
}
