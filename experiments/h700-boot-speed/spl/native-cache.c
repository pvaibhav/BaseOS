/* The SD owner enables caching only after successful DRAM training. */
#include <linux/types.h>
#include <asm/io.h>

extern void __asm_invalidate_dcache_all(void);

int baseos_native_cache_on(void)
{
    u64 control;
    asm volatile("mrs %0, sctlr_el3" : "=r"(control));
    if (control & 5) return -1;
    volatile u64 *root = (void *)0x4a400000;
    volatile u64 *low = (void *)0x4a401000;
    volatile u64 *dram = (void *)0x4a402000;
    for (unsigned int i = 0; i < 512; i++) {
        root[i] = 0;
        /* Attr0 Device-nGnRnE, AF, XN. No peripheral execution. */
        low[i] = ((u64)i << 21) | (1ull << 54) | 0x401;
        /* First 256 MiB is the bounded working arena; Attr1 Normal WBWA.
         * SPL stages the BMP here. Only A32 accesses the high logo pixels. */
        dram[i] = i < 128 ? (0x40000000ull + ((u64)i << 21)) | 0x705 : 0;
    }
    low[0] = 0x705; /* SRAM code, BSS, restore buffer and stack. */
    root[0] = 0x4a401003;
    root[1] = 0x4a402003;
    __asm_invalidate_dcache_all();
    asm volatile("dsb sy; msr mair_el3, %0; msr tcr_el3, %1; msr ttbr0_el3, %2; "
                 "isb; tlbi alle3; ic iallu; dsb sy; isb; msr sctlr_el3, %3; isb"
                 :: "r"(0xff00ul), "r"(0x80803519ul), "r"(0x4a400000ul),
                    "r"(control | 0x1005) : "memory");
    return 0;
}
