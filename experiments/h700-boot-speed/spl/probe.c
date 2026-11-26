/* SRAM-only bring-up: write one designated result sector, then watchdog reset. */
#include "shim.h"

#define RESULT_SECTOR 0x56000u
#define RESULT_MAGIC 0x50534f42u
static u32 result[128] __attribute__((aligned(64)));
static u32 check[128] __attribute__((aligned(64)));

void *memcpy(void *destination, const void *source, size_t size)
{
    unsigned char *out = destination;
    const unsigned char *in = source;
    for (size_t i = 0; i < size; i++) out[i] = in[i];
    return destination;
}

int printf(const char *message, ...) { (void)message; return 0; }

static u32 ticks(void)
{
    u32 lo, hi;
    __asm__ volatile("mrrc p15, 0, %0, %1, c14" : "=r"(lo), "=r"(hi));
    return lo;
}

unsigned long timer_get_us(void) { return ticks() / 24; }
void udelay(unsigned long usec)
{
    u32 start = ticks();
    while ((u32)(ticks() - start) < usec * 24) { }
}

__attribute__((noreturn)) void panic(const char *message, ...)
{
    (void)message;
    for (;;) __asm__ volatile("wfi");
}

static int wait_bits(volatile u32 *reg, u32 mask, u32 wanted)
{
    u32 start = ticks();
    while ((*reg & mask) != wanted)
        if ((u32)(ticks() - start) > 24000000) return -1;
    return 0;
}

/* The vendor has selected an SDHC card. Reuse that state, using CPU FIFO I/O. */
static int sector_io(u32 *buffer, int writing)
{
    volatile u32 *mmc = (volatile u32 *)0x04020000;
    if (wait_bits(mmc + 0x3c / 4, 1u << 9, 0)) return -1;
    mmc[0x80 / 4] = 0;
    mmc[0x8c / 4] = 0;
    mmc[0] = (mmc[0] & ~(1u << 5)) | (1u << 31) | 2;
    if (wait_bits(mmc, 2, 0)) return -2;
    mmc[0x38 / 4] = ~0u;
    mmc[0x10 / 4] = 512;
    mmc[0x14 / 4] = 512;
    mmc[0x1c / 4] = RESULT_SECTOR;
    mmc[0x18 / 4] = 0x80002340u | (writing ? (24u | 0x400u) : 17u);
    u32 start = ticks();
    for (u32 i = 0; i < 128;) {
        u32 flags = mmc[0x38 / 4];
        if (flags & 0xbfc2u) return -3;
        if ((u32)(ticks() - start) > 24000000) return -4;
        if (!(mmc[0x3c / 4] & (writing ? 8 : 4))) {
            if (writing) mmc[0x200 / 4] = buffer[i];
            else buffer[i] = mmc[0x200 / 4];
            i++;
        }
    }
    if (wait_bits(mmc + 0x38 / 4, 12, 12) || (mmc[0x38 / 4] & 0xbfc2u)) return -5;
    if (wait_bits(mmc + 0x3c / 4, 1u << 9, 0)) return -6;
    mmc[0x38 / 4] = ~0u;
    return 0;
}

static u32 hash(const u32 *data)
{
    const unsigned char *bytes = (const void *)data;
    u32 value = 2166136261u;
    for (u32 i = 0; i < 508; i++) value = (value ^ bytes[i]) * 16777619u;
    return value;
}

static void checkpoint(u32 stage)
{
    result[3] = stage;
    result[6] = ticks();
    result[127] = hash(result);
    if (sector_io(result, 1) || sector_io(check, 0)) panic("result I/O failed");
    for (u32 i = 0; i < 128; i++)
        if (result[i] != check[i]) panic("result readback differs");
}

__attribute__((noreturn)) void probe_main(u32 operation, u32 sctlr, u32 cpsr)
{
    /* No arbitrary writes: the host must have staged this exact protocol. */
    if (operation > 1 || sector_io(result, 0) || result[0] != RESULT_MAGIC ||
        result[1] != 1 || result[2] != operation || result[3] != 10 ||
        result[127] != hash(result)) panic("result sector is not armed");
    result[4] = sctlr;
    result[5] = cpsr;
    result[7] = ticks();
    checkpoint(1);
    if (operation) {
        result[8] = ticks();
        result[9] = sunxi_dram_init();
        result[10] = ticks();
#ifdef DRAM_AUTODETECT
        extern u32 dram_probe_attempts, dram_probe_selected_type;
        result[40] = dram_probe_attempts;
        result[41] = dram_probe_selected_type;
#endif
        volatile u32 *memory = (volatile u32 *)0x41000000;
        for (u32 i = 0; i < 4096; i++) memory[i] = 0x9e3779b9u * (i + 1);
        dsb();
        for (u32 i = 0; i < 4096; i++)
            if (memory[i] != 0x9e3779b9u * (i + 1)) panic("DRAM check failed");
    }
    checkpoint(2);
    volatile u32 *wdt = (volatile u32 *)0x03009000;
    wdt[0xb4 / 4] = 1;
    wdt[0xb8 / 4] = 0x31;
    wdt[0xb0 / 4] = 0x14af;
    dsb();
    panic("return to original boot chain");
}
