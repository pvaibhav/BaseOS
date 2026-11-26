/* Raw capsule adoption: outer already owns pending+watchdog. */
#if defined(BASEOS_RAW_CAPSULE)
#if defined(BASEOS_PERSISTENT) || defined(BASEOS_PERSISTENT_LAB) || defined(BASEOS_TRACE_HANDOFF)
#error "raw capsule adoption excludes persistent journal, lab, resume and RTC tracing"
#endif
#include <mmc.h>
#include <blk.h>
#include <asm/io.h>
#include "raw-capsule-generated.h"
#include "persistent-attempt.h"
static u32 result[128] __aligned(64);
static void fail(void) { for (;;) asm volatile("wfi"); }
static u32 ticks(void) { u64 counter; asm volatile("mrs %0, cntpct_el0":"=r"(counter)); return counter; }
static u32 hash(const void *data, unsigned int bytes) {
    const u8 *p=data;u32 value=2166136261u;
    for (unsigned i=0;i<bytes;i++) value=(value^p[i])*16777619u;
    return value;
}
/* No BSS, clocks, MMC, DRAM or mutable RTC state is needed to validate adoption. */
void baseos_raw_adopt(void) {
    for (unsigned int pass=0;pass<2;pass++) {
        if (readl((void *)0x07000138ul)!=BASEOS_ATTEMPT_COOKIE ||
            readl((void *)0x0700013cul)!=ATTEMPT_PENDING ||
            readl((void *)0x030090b4ul)!=1u || readl((void *)0x030090b8ul)!=0xb1u) fail();
        asm volatile("dsb sy" ::: "memory");
    }
}
void baseos_checkpoint(struct blk_desc *card, u32 stage) {
    (void)card;result[3]=stage;result[6]=ticks();
    if (stage>=30 && stage<=34) result[64+stage-30]=result[6];
    result[127]=hash(result,508);
}
void baseos_early_mmc(void) {
    baseos_raw_adopt();
    if (mmc_initialize(NULL)) fail();
    struct mmc *mmc=find_mmc_device(0);
    if (!mmc || mmc_init(mmc)) fail();
    struct blk_desc *card=mmc_get_blk_desc(mmc);
    u64 level;asm volatile("mrs %0, CurrentEL":"=r"(level));
    result[35]=level;result[8]=result[10]=ticks();
    result[36]=mmc->clock;result[37]=mmc->bus_width;result[46]=16;
    extern int baseos_vendor_handoff(struct blk_desc *,u32 *,const u8 *);
    baseos_vendor_handoff(card,result,NULL);
    fail();
}
#else
/* Mainline SPL control: reinitialize SD before DRAM, verify a scratch restore. */
#include <mmc.h>
#include <blk.h>
#include <asm/io.h>
#include <linux/delay.h>

#define RESULT_SECTOR 0x56000
#define SOURCE_SECTOR (RESULT_SECTOR + 1)
#define MIRROR_SECTOR 0x56200
#define BOOT0_BYTES 65536
#ifdef BASEOS_PERSISTENT
#include "persistent-generated.h"
#include "persistent-attempt.h"
static int persistent_resume, persistent_recovery, persistent_lab;
_Static_assert(BASEOS_ATTEMPT_COOKIE != 0, "zero generation cookie");
#ifdef BASEOS_TRACE_HANDOFF
#error "persistent builds must not write diagnostic RTC traces"
#endif
#endif
static u32 result[128] __aligned(64);
static u32 check[128] __aligned(64);

static u32 ticks(void)
{
    u64 counter;
    asm volatile("mrs %0, cntpct_el0" : "=r"(counter));
    return counter;
}

static u32 hash(const void *data, unsigned int bytes)
{
    const u8 *p = data;
    u32 value = 2166136261u;
    for (unsigned int i = 0; i < bytes; i++) value = (value ^ p[i]) * 16777619u;
    return value;
}

static void fail(void)
{
    for (;;) asm volatile("wfi");
}

void baseos_checkpoint(struct blk_desc *card, u32 stage)
{
#ifdef BASEOS_TRACE_HANDOFF
    writel(0x42530000u | stage, (void *)0x0700013c);
#endif
    result[3] = stage;
    result[6] = ticks();
    if (stage >= 30 && stage <= 34) result[64 + stage - 30] = result[6];
    result[127] = hash(result, 508);
#ifdef BASEOS_PERSISTENT
    if (!persistent_lab) return;
#endif
    if (blk_dwrite(card, RESULT_SECTOR, 1, result) != 1 ||
        blk_dread(card, RESULT_SECTOR, 1, check) != 1 ||
        memcmp(check, result, 512)) fail();
}

static void baseos_control_mmc(void)
{
    u32 started = ticks();
    if (mmc_initialize(NULL)) fail();
    struct mmc *mmc = find_mmc_device(0);
    if (!mmc || mmc_init(mmc)) fail();
    struct blk_desc *card = mmc_get_blk_desc(mmc);
    if (blk_dread(card, RESULT_SECTOR, 1, result) != 1 ||
        result[0] != 0x50534f42 || result[1] != 1 || result[2] != 2 ||
        result[3] != 10 || result[127] != hash(result, 508)) fail();
#ifdef BASEOS_PERSISTENT
    if (!persistent_lab || result[44] || result[47]) fail();
#endif
    u64 level;
    asm volatile("mrs %0, CurrentEL" : "=r"(level));
    result[35] = level;
    result[8] = started;
    result[10] = ticks();
    result[36] = mmc->clock;
    result[37] = mmc->bus_width;
    if (result[45] != 0 && result[45] != 16) fail();
    u32 destination = result[45] ? 16 : MIRROR_SECTOR;
    baseos_checkpoint(card, 20);
    /* Everything used here, including MMC's BSS and malloc, lives in SRAM. */
    u32 *source = (void *)0x38000;
    if (blk_dread(card, SOURCE_SECTOR, BOOT0_BYTES / 512, source) != BOOT0_BYTES / 512 ||
        source[0] != 0xea0004be || source[4] != BOOT0_BYTES ||
        hash(source, BOOT0_BYTES) != BASEOS_BOOT0_FNV) fail();
    baseos_checkpoint(card, 21);
    if (blk_dwrite(card, destination, BOOT0_BYTES / 512, source) != BOOT0_BYTES / 512) fail();
    for (unsigned int i = 0; i < BOOT0_BYTES / 512; i++) {
        if (blk_dread(card, destination + i, 1, check) != 1 ||
            memcmp(check, source + i * 128, 512)) fail();
    }
    result[38] = hash(source, BOOT0_BYTES);
    result[39] = BOOT0_BYTES;
    result[46] = destination;
#ifdef BASEOS_VENDOR_HANDOFF
    if (result[47]) {
        extern int baseos_vendor_handoff(struct blk_desc *, u32 *, const u8 *);
        baseos_vendor_handoff(card, result, (const void *)source);
        fail();
    }
#endif
#ifdef BASEOS_DRAM_CONTROL
    if (result[44] > 1) fail();
    if (result[44]) {
    extern unsigned long sunxi_dram_init(void);
    extern u32 dram_probe_attempts, dram_probe_selected_type;
    baseos_checkpoint(card, 22);
    result[42] = ticks();
    result[9] = sunxi_dram_init();
    result[43] = ticks();
    result[40] = dram_probe_attempts;
    result[41] = dram_probe_selected_type;
    volatile u32 *memory = (void *)0x41000000;
    for (unsigned int i = 0; i < 4096; i++) memory[i] = 0x9e3779b9u * (i + 1);
    asm volatile("dsb sy" ::: "memory");
    for (unsigned int i = 0; i < 4096; i++)
        if (memory[i] != 0x9e3779b9u * (i + 1)) fail();
    }
#endif
    baseos_checkpoint(card, 2);
#ifdef BASEOS_PERSISTENT
    extern void baseos_finish_lab(void);
    baseos_finish_lab();
#endif
    writel(1, (void *)0x030090b4);
    writel(0x31, (void *)0x030090b8);
    writel(0x14af, (void *)0x030090b0);
    fail();
}

#ifndef BASEOS_PERSISTENT
void baseos_early_mmc(void) { baseos_control_mmc(); }
#else
static uint32_t rtc_read(void *context, enum attempt_word word)
{
    (void)context;
    return readl((void *)(0x07000138ul + 4 * word));
}
static void rtc_write(void *context, enum attempt_word word, uint32_t value)
{
    (void)context;
    writel(value, (void *)(0x07000138ul + 4 * word));
}
static void rtc_barrier(void *context)
{
    (void)context;
    asm volatile("dsb sy" ::: "memory");
}
static struct attempt_io rtc_io = {NULL, rtc_read, rtc_write, rtc_barrier};

/* Called after BSS clearing and watchdog arm, but before clock/SD/DRAM work.
 * Resume is passed by the board entry and always takes precedence. */
void baseos_persistent_prepare(int resume)
{
    persistent_resume = resume;
    if (resume) return;
    struct attempt_pair pair = pair_read(&rtc_io);
#ifdef BASEOS_PERSISTENT_LAB
    if (pair.cookie == BASEOS_ATTEMPT_COOKIE && pair.marker == BASEOS_LAB_MAGIC) {
        rtc_write(NULL, ATTEMPT_MARKER, BASEOS_LAB_RUNNING);
        rtc_barrier(NULL);
        const struct attempt_pair running = {BASEOS_ATTEMPT_COOKIE, BASEOS_LAB_RUNNING};
        persistent_lab = pair_equal(pair_read(&rtc_io), running);
        persistent_recovery = !persistent_lab;
        return;
    }
#endif
    persistent_recovery = boot_plan(pair, 0, BASEOS_ATTEMPT_COOKIE) != NATIVE_ATTEMPT ||
                          attempt_commit(&rtc_io, BASEOS_ATTEMPT_COOKIE) != IO_COMPLETE;
}

void baseos_finish_lab(void)
{
    const struct attempt_pair lab = {BASEOS_ATTEMPT_COOKIE, BASEOS_LAB_RUNNING};
    const struct attempt_pair clearing = {0, BASEOS_LAB_RUNNING}, empty = {0, 0};
    if (!persistent_lab || !pair_equal(pair_read(&rtc_io), lab)) fail();
    rtc_barrier(NULL);
    if (!pair_equal(pair_read(&rtc_io), lab)) fail();
    rtc_write(NULL, ATTEMPT_COOKIE, 0);
    rtc_barrier(NULL);
    if (!pair_equal(pair_read(&rtc_io), clearing)) fail();
    rtc_write(NULL, ATTEMPT_MARKER, 0);
    rtc_barrier(NULL);
    if (!pair_equal(pair_read(&rtc_io), empty)) fail();
}

static u32 *read_stock(struct blk_desc *card, unsigned blocks)
{
    u32 *source = (void *)0x38000;
    if (blk_dread(card, SOURCE_SECTOR, blocks, source) != blocks ||
        source[0] != 0xea0004be || source[4] != BOOT0_BYTES ||
        memcmp((u8 *)source + 4, "eGON.BT0", 8) ||
        hash(source, blocks * 512) != (blocks == 1 ? BASEOS_BOOT0_HEADER_FNV : BASEOS_BOOT0_FNV)) fail();
    return source;
}

static void recover_stock(struct blk_desc *card)
{
    u32 *source = read_stock(card, BOOT0_BYTES / 512);
    if (blk_dwrite(card, 16, BOOT0_BYTES / 512, source) != BOOT0_BYTES / 512) fail();
    for (unsigned i = 0; i < BOOT0_BYTES / 512; i++)
        if (blk_dread(card, 16 + i, 1, check) != 1 ||
            memcmp(check, source + i * 128, 512)) fail();
    /* Keep failed pair; do not call native DRAM/monitor. Whole-system reset. */
    writel(1, (void *)0x030090b4);
    writel(1, (void *)0x030090b8);
    writel(0x14af, (void *)0x030090b0);
    fail();
}

void baseos_early_mmc(void)
{
    if (persistent_lab) { baseos_control_mmc(); fail(); }
    if (mmc_initialize(NULL)) fail();
    struct mmc *mmc = find_mmc_device(0);
    if (!mmc) fail();
    struct mmc_config resume_config;
    if (persistent_resume) {
        resume_config = *mmc->cfg;
        resume_config.f_max = 24000000;
        mmc->cfg = &resume_config;
    }
    if (mmc_init(mmc)) fail();
    struct blk_desc *card = mmc_get_blk_desc(mmc);
    if (persistent_resume) {
        (void)read_stock(card, BOOT0_BYTES / 512);
        static const u32 trampoline[] = {
#include "stock-resume64.inc"
        };
        memcpy((void *)0x50000, trampoline, sizeof(trampoline));
        asm volatile("dsb sy; ic iallu; dsb sy; isb" ::: "memory");
        ((void (*)(void))0x50000)();
        fail();
    }
    if (persistent_recovery) recover_stock(card);
    if (blk_dread(card, RESULT_SECTOR, 1, result) != 1 ||
        hash(result, 512) != BASEOS_PERSISTENT_JOURNAL_FNV ||
        result[0] != 0x50534f42 || result[1] != 1 || result[2] != 3 ||
        result[3] != 10 || result[127] != hash(result, 508)) recover_stock(card);
    const u8 *stock = (void *)read_stock(card, 1);
    u64 level;
    asm volatile("mrs %0, CurrentEL" : "=r"(level));
    result[35] = level;
    result[8] = result[10] = ticks();
    result[36] = mmc->clock;
    result[37] = mmc->bus_width;
    result[46] = 16;
    extern int baseos_vendor_handoff(struct blk_desc *, u32 *, const u8 *);
    baseos_vendor_handoff(card, result, stock);
    fail();
}
#endif

#endif /* BASEOS_RAW_CAPSULE */
