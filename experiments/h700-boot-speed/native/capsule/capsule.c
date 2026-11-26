#include <stddef.h>
#include <stdint.h>
#include "native-attempt.h"

#define RTC_BASE UINT32_C(0x07000000)
#define GP14 (RTC_BASE + UINT32_C(0x100) + 14u * 4u)
#define GP15 (RTC_BASE + UINT32_C(0x100) + 15u * 4u)
#define RETENTION (RTC_BASE + UINT32_C(0x1f8))
#define WDT_CTRL UINT32_C(0x030090b0)
#define WDT_CFG UINT32_C(0x030090b4)
#define WDT_MODE UINT32_C(0x030090b8)
#define STOCK ((const uint8_t *)UINT32_C(0x21000))
#define NATIVE ((const uint8_t *)UINT32_C(0x31000))

extern uint8_t copy_start[], copy_end[], saved_context[];

static uint32_t read32(uint32_t address) { return *(volatile uint32_t *)(uintptr_t)address; }
static void write32(uint32_t address, uint32_t value) { *(volatile uint32_t *)(uintptr_t)address = value; }
static void fence(void *unused) { (void)unused; __asm__ volatile("dsb sy" ::: "memory"); }
static uint32_t rtc_read(void *unused, enum attempt_word word)
{
    (void)unused;
    return read32(word == ATTEMPT_COOKIE ? GP14 : GP15);
}
static void rtc_write(void *unused, enum attempt_word word, uint32_t value)
{
    (void)unused;
    write32(word == ATTEMPT_COOKIE ? GP14 : GP15, value);
}
static struct attempt_io io = {0, rtc_read, rtc_write, fence};

static uint32_t fnv(const uint8_t *data, uint32_t bytes)
{
    uint32_t value = UINT32_C(2166136261);
    for (uint32_t i = 0; i < bytes; i++) value = (value ^ data[i]) * UINT32_C(16777619);
    return value;
}

static int header_valid(void)
{
    const uint8_t *header = (void *)UINT32_C(0x20000);
    const uint8_t magic[] = {'e','G','O','N','.','B','T','0'};
    if (read32(UINT32_C(0x20010)) != UINT32_C(0x20000)) return 0;
    for (uint32_t i = 0; i < sizeof(magic); i++) if (header[i + 4] != magic[i]) return 0;
    return 1;
}

/* On standby wake, stock boot0 skips bank selection and delay training and
 * reinitializes DRAM straight from header 0x38..0xb7. Hand over the outer
 * header's 32-word block after STOCK_FNV passes, unless its clock is zero or
 * its memory type is absent from every bank of the built-in boot0. */
#define OUTER_DRAM ((const uint32_t *)UINT32_C(0x20038))
#define STOCK_DRAM ((uint32_t *)UINT32_C(0x21038))
#define STOCK_BANKS ((const uint32_t *)UINT32_C(0x21374))

static int outer_is_dram_block(void)
{
    if (!OUTER_DRAM[0]) return 0;
    if (OUTER_DRAM[1] == STOCK_DRAM[1]) return 1;
    for (uint32_t k = 0; k < 15; k++)
        if (OUTER_DRAM[1] == STOCK_BANKS[32 * k + 1]) return 1;
    return 0;
}

static const uint8_t *stock_for_wake(void)
{
    if (outer_is_dram_block())
        for (uint32_t i = 0; i < 32; i++) STOCK_DRAM[i] = OUTER_DRAM[i];
    return STOCK;
}

static uint32_t prepare_copy(const uint8_t *source)
{
    volatile uint8_t *stub = (void *)UINT32_C(0x50000);
    volatile uint8_t *context = (void *)UINT32_C(0x52000);
    for (uint32_t i = 0; i < (uint32_t)(copy_end - copy_start); i++) stub[i] = copy_start[i];
    for (uint32_t i = 0; i < 72; i++) context[i] = saved_context[i];
    return (uint32_t)(uintptr_t)source;
}

#if RAW_CAPSULE_ARM_PROTOCOL
static int stop_new_watchdog(void)
{
    write32(WDT_MODE, 0);
    fence(0);
    return !(read32(WDT_MODE) & 1u);
}
static int arm_watchdog(void)
{
    write32(WDT_CFG, 1);
    fence(0);
    if (read32(WDT_CFG) != 1u) return 0;
    write32(WDT_MODE, UINT32_C(0xb1));
    fence(0);
    if ((read32(WDT_MODE) & UINT32_C(0xf1)) != UINT32_C(0xb1)) return 0;
    write32(WDT_CTRL, UINT32_C(0x14af));
    fence(0);
    return read32(WDT_CFG) == 1u &&
           (read32(WDT_MODE) & UINT32_C(0xf1)) == UINT32_C(0xb1);
}
#endif

uint32_t capsule_main(void)
{
    if (!header_valid()) return 0;
    int resume = !!(read32(RETENTION) & UINT32_C(0x10000));
    if (resume) return fnv(STOCK, UINT32_C(0x10000)) == STOCK_FNV ? prepare_copy(stock_for_wake()) : 0;
    struct attempt_pair pair = pair_read(&io);
    if (boot_plan(pair, 0, CAPSULE_COOKIE) != NATIVE_ATTEMPT)
        return fnv(STOCK, UINT32_C(0x10000)) == STOCK_FNV ? prepare_copy(STOCK) : 0;
    if (fnv(STOCK, UINT32_C(0x10000)) != STOCK_FNV) return 0;
    if (fnv(NATIVE, UINT32_C(0xc000)) != NATIVE_FNV) return prepare_copy(STOCK);
#if RAW_CAPSULE_ARM_PROTOCOL
    /* Normal empty-pair route only. Never take ownership of an already active watchdog. */
    if (read32(WDT_MODE) & 1u) return prepare_copy(STOCK);
    if (!arm_watchdog()) return stop_new_watchdog() ? prepare_copy(STOCK) : 0;
    if (attempt_commit(&io, CAPSULE_COOKIE) != IO_COMPLETE)
        return stop_new_watchdog() ? prepare_copy(STOCK) : 0;
#endif
    return prepare_copy(NATIVE);
}
