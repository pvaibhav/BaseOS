/* Restore has already completed before this first-stage handoff is armed. */
#include <mmc.h>
#include <blk.h>
#include <asm/global_data.h>
#include <asm/io.h>
#include <linux/string.h>

DECLARE_GLOBAL_DATA_PTR;
extern int baseos_dram_power;
extern u32 dram_probe_attempts, dram_probe_selected_type;
extern const u32 *baseos_selected_vendor_params(void);
extern void sunxi_board_init(void);
extern void baseos_checkpoint(struct blk_desc *, u32);
extern int baseos_stamp_fdt(void *, const u64 *);
#ifdef BASEOS_TRACE_HANDOFF
#include "handoff-trace.h"
#endif

static u32 fnv(const u8 *bytes, unsigned int size)
{
    u32 hash = 2166136261u;
    for (unsigned int i = 0; i < size; i += 4) {
        u32 word = *(const u32 *)(bytes + i);
        for (unsigned int shift = 0; shift < 32; shift += 8) hash = (hash ^ (word >> shift & 255)) * 16777619u;
    }
    return hash;
}

int baseos_vendor_handoff(struct blk_desc *card, u32 *result, const u8 *stock)
{
    u64 level;
    asm volatile("mrs %0, CurrentEL" : "=r"(level));
    if (level != 12 || result[46] != 16) return -1;
    baseos_checkpoint(card, 30);
    baseos_dram_power = 1;
    sunxi_board_init();
    result[9] = gd->ram_size;
    result[40] = dram_probe_attempts;
    result[41] = dram_probe_selected_type;
    baseos_checkpoint(card, 31);
    u8 *package = (void *)0x42e00000;
    if (blk_dread(card, 32800, 0xa00, package) != 0xa00 || fnv(package, 0x140000) != BASEOS_TOC_FNV)
        return -1;
    baseos_checkpoint(card, 32);
    u32 count = *(u32 *)(package + 32);
    if (count > 16 || *(u32 *)(package + 36) != 0x140000) return -1;
    unsigned int found = 0;
    for (unsigned int i = 0; i < count; i++) {
        u8 *item = package + 64 + i * 368;
        u32 offset = *(u32 *)(item + 64), size = *(u32 *)(item + 68);
        if (offset > 0x140000 || size > 0x140000 - offset) return -1;
        void *destination;
        if (!strcmp((char *)item, "u-boot")) { destination = (void *)0x4a000000; found |= 1; }
        else if (!strcmp((char *)item, "monitor")) { destination = (void *)0x48000000; found |= 2; }
        else if (!strcmp((char *)item, "dtbo")) { destination = (void *)0x4a100000; found |= 4; }
        else if (!strcmp((char *)item, "dtb")) { destination = (void *)0x4a200000; found |= 8; }
        else return -1;
        memcpy(destination, package + offset, size);
    }
    if (found != 15) return -1;
    u8 *uboot = (void *)0x4a000000, *monitor = (void *)0x48000000;
    const u32 *parameters = baseos_selected_vendor_params();
    memcpy(uboot + 0x30, parameters, 128);
    *(u32 *)(uboot + 0x4f8) = gd->ram_size >> 20;
    *(u32 *)(uboot + 0x4f4) = 0x140000;
    *(u32 *)(uboot + 0xe4) = 1;
    uboot[0x4e8] = 0;
    uboot[0x4e9] = 1;
    uboot[0x4ea] = 0;
    memcpy(monitor + 0x30, parameters, 128);
    memcpy(monitor + 0x24, stock + 0xf0, 8);
    *(u32 *)(monitor + 0xb0) = 0x4a200000;
    u64 tag[4];
    tag[0] = 0x313030764c505342ul;
    asm volatile("mrs %0, cntpct_el0" : "=r"(tag[1]));
    tag[2] = gd->ram_size;
    tag[3] = dram_probe_selected_type;
    if (baseos_stamp_fdt((void *)0x4a200000, tag)) return -1;
    result[49] = tag[1];
    baseos_checkpoint(card, 34);
#ifdef BASEOS_TRACE_HANDOFF
    trace_monitor();
#endif
    writel(0x14af, (void *)0x030090b0);
    /* Vendor boot0 enters BL31 through a core warm reset, with r0 pointing to
     * BL31 and r1 retaining its alternative RVBAR-high address. Match that
     * complete entry contract rather than branching with SPL's CPU state. */
    ulong rvbar = (readl((void *)0x03000024) & 7) == 2 ? 0x08100040 : 0x09010040;
    writel(0x48000000, (void *)rvbar);
    writel(0, (void *)(rvbar + 4));
    asm volatile("dsb sy; isb; mov x0, %0; movz x1, #0x44; movk x1, #0x810, lsl #16; "
                 "mov x2, #3; mov x3, %1; msr rmr_el3, x2; isb; 1: wfi; b 1b"
                 :: "r"(0x48000000ul), "r"(rvbar + 4) : "x0", "x1", "x2", "x3", "memory");
    return -1;
}
