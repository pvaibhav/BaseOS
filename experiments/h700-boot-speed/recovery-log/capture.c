/* Copy the previous kernel's static printk ring before recovery clears BSS. */
typedef unsigned int u32;

static u32 swap(u32 value) { return __builtin_bswap32(value); }

void capture_log(void)
{
    volatile u32 *fdt = (void *)0x44000000u;
    u32 bytes = swap(fdt[1]);
    if (fdt[0] != 0xedfe0dd0u || bytes < 40 || bytes > 0x80000u) return;
    u32 offset = (bytes + 15) & ~15u;
    u32 *out = (void *)(0x44000000u + offset);
    const u32 *source = (void *)0x4f0fffc0u;
    if (source[0] != 0x4c4b5342 || source[1] != 0x3130474f) return;
    for (u32 i = 0; i < (64 + 0x4000) / 4; i++) out[i] = source[i];
    out[12] = bytes;
    fdt[1] = swap(offset + 64 + 0x4000);
}
