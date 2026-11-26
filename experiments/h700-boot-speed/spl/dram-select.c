/* SRAM-only candidate selection around the upstream H616 training routines. */
#ifdef DRAM_NATIVE
#include <init.h>
#include <log.h>
#include <asm/io.h>
#include <asm/arch/cpu.h>
#include <asm/arch/dram.h>
#include <asm/arch/dram_dw_helpers.h>
#include <asm/arch/prcm.h>
#include <vsprintf.h>
#include <axp_pmic.h>
#include <linux/delay.h>
void baseos_set_master_priority(void);
int baseos_dram_power;
static u32 trained_vendor_params[32];
#else
#include "shim.h"
#endif
#include "dram-candidates.h"
#ifdef DRAM_NATIVE
const u32 *baseos_selected_vendor_params(void) { return trained_vendor_params; }

/* Vendor boot0 publishes trained geometry, rather than the prepared bank's
 * defaults. BL31 consumes this same block when resuming the memory controller. */
int baseos_normalize_vendor_params(const struct dram_config *config,
                                  unsigned long size, unsigned int candidate)
{
    if (config->cols < 8 || config->cols > 11 || config->rows < 13 || config->rows > 17 ||
        (config->ranks != 1 && config->ranks != 2) || config->bus_full_width > 1 ||
        candidate >= ARRAY_SIZE(dram_vendor_candidates)) return -1;
    u64 expected = (1ull << (config->cols + config->rows + 3)) *
                   (config->bus_full_width ? 4 : 2) * config->ranks;
    if (size != expected || (size >> 20) > 0x7fff) return -1;
    for (unsigned int i = 0; i < 32; i++)
        trained_vendor_params[i] = dram_vendor_candidates[candidate][i];
    trained_vendor_params[6] = (3u << 12) | (config->rows << 4) | config->cols;
    trained_vendor_params[7] = (size >> 20 << 16) |
                              (config->ranks == 2 ? 0x1000u : 0u) |
                              (config->bus_full_width ? 0u : 1u);
    if (!(trained_vendor_params[30] & 0x801u))
        trained_vendor_params[30] |= 0x6001u;
    return 0;
}
#endif

int dram_checkpoint(void) __attribute__((returns_twice));
void dram_panic(const char *, ...) __attribute__((noreturn));
void timing_ddr3(const struct dram_para *);
void timing_lpddr3(const struct dram_para *);
void timing_lpddr4(const struct dram_para *);
u32 dram_probe_attempts;
u32 dram_probe_selected_type;

/* Size detection on a bank whose training "passed" without a working data path
 * sees no aliasing and reports the largest layout (11 columns, 17 rows, at
 * least 4 GiB). mctl_calc_size then probes at 3/4 of that size, outside the
 * DRAM window, and the bus hangs until the watchdog. Refuse any layout whose
 * probe would leave the 32-bit window (so at most 2 GiB) before anything probes. */
static int baseos_layout_in_window(const struct dram_config *config)
{
    u64 size = (1ull << (config->cols + config->rows + 3)) *
               (config->bus_full_width ? 4 : 2) * config->ranks;
    return (u64)CFG_SYS_SDRAM_BASE + size / 4 * 3 <= 0xfffffffcull;
}

/* Before accepting a bank: the first words and the words halfway up must hold
 * their own values. Catches a broken data path and a size detected too large
 * (the upper half would land on the lower). The old contents are put back. */
static int baseos_memory_holds(unsigned long size)
{
    volatile u32 *low = (volatile u32 *)(unsigned long)CFG_SYS_SDRAM_BASE;
    volatile u32 *high = (volatile u32 *)((unsigned long)CFG_SYS_SDRAM_BASE + size / 2);
    u32 saved_low[4], saved_high[4];
    int good = 1;
    unsigned int i;

    for (i = 0; i < 4; i++) {
        saved_low[i] = low[i];
        saved_high[i] = high[i];
    }
    for (i = 0; i < 4; i++) {
        low[i] = 0x5aa5c33cu ^ (i * 0x11111111u);
        high[i] = ~(0x5aa5c33cu ^ (i * 0x11111111u));
    }
    __asm__ volatile("dsb sy" ::: "memory");
    for (i = 0; i < 4; i++)
        if (low[i] != (0x5aa5c33cu ^ (i * 0x11111111u)) ||
            high[i] != ~(0x5aa5c33cu ^ (i * 0x11111111u))) good = 0;
    for (i = 0; i < 4; i++) {
        high[i] = saved_high[i];
        low[i] = saved_low[i];
    }
    __asm__ volatile("dsb sy" ::: "memory");
    return good;
}

void mctl_set_timing_params(const struct dram_para *para)
{
    switch (para->type) {
    case SUNXI_DRAM_TYPE_DDR3: timing_ddr3(para); return;
    case SUNXI_DRAM_TYPE_LPDDR3: timing_lpddr3(para); return;
    case SUNXI_DRAM_TYPE_LPDDR4: timing_lpddr4(para); return;
    default: dram_panic("unsupported candidate type");
    }
}

unsigned long sunxi_dram_init(void)
{
    struct dram_config config;
    volatile unsigned int candidate;
    dram_probe_attempts = 0;
    dram_probe_selected_type = 0;
    setbits_le32(SUNXI_PRCM_BASE + CCU_PRCM_RES_CAL_CTRL, BIT(8));
    clrbits_le32(SUNXI_PRCM_BASE + CCU_PRCM_OHMS240, 0x3f);

    for (candidate = 0; candidate < ARRAY_SIZE(dram_candidates); candidate++) {
        const struct dram_para *para = &dram_candidates[candidate];
        dram_probe_attempts++;
        /* Upstream timeout or exhausted rank/width attempts unwind to SRAM.
         * The next mctl_core_init resets the controller and PHY again. */
        if (dram_checkpoint()) continue;
#ifdef DRAM_NATIVE
        if (baseos_dram_power) {
            unsigned int millivolts = para->type == SUNXI_DRAM_TYPE_LPDDR4 ? 1100 :
                                     para->type == SUNXI_DRAM_TYPE_LPDDR3 ? 1200 : 1500;
            if (axp_set_dcdc3(millivolts)) continue;
            udelay(1000);
        }
#endif
        mctl_auto_detect_rank_width(para, &config);
        mctl_auto_detect_dram_size(para, &config);
        if (!baseos_layout_in_window(&config)) continue;
        if (!mctl_core_init(para, &config)) continue;
        unsigned long size = mctl_calc_size(&config);
        if (!size || !baseos_memory_holds(size)) continue;
        /* Kept in the same translation unit as upstream's static helper. */
        baseos_set_master_priority();
        dram_probe_selected_type = para->type;
#ifdef DRAM_NATIVE
        if (baseos_normalize_vendor_params(&config, size, candidate)) continue;
#endif
        return size;
    }
    panic("all DRAM candidates failed");
}
