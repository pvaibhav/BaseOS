/* The adapter runs in relocated vendor U-Boot; only the probe runs in SRAM. */
typedef unsigned char u8;
typedef unsigned int u32;
extern u32 vendor_phyread(u32, u32, void *);
extern u32 vendor_phywrite(u32, u32, const void *);

static u32 hash_bytes(const u8 *bytes, u32 size)
{
    u32 hash = 2166136261u;
    for (u32 i = 0; i < size; i++) hash = (hash ^ bytes[i]) * 16777619u;
    return hash;
}

int custom_unpack(void *destination, unsigned long capacity, const u8 *source,
                  unsigned long *length)
{
    (void)destination;
    (void)capacity;
    const u32 *frame = (const void *)source;
    u32 bytes = frame[1], operation = frame[2], expected = frame[3];
#ifdef MAINLINE_SPL_TRIAL
    if (bytes < 256 || bytes > 0xd000 || operation != 2 || *length != bytes + 16) return -1;
    u32 spl_bytes = frame[4], bridge_bytes = frame[5];
    if (spl_bytes > 0xc000 || spl_bytes < 0x1000 || bridge_bytes > 1024 ||
        bridge_bytes < 64 || bytes != spl_bytes + bridge_bytes + 8) return -1;
#else
    if (bytes < 256 || bytes > 0x6000 || operation > 1 || *length != bytes + 16) return -1;
#endif
    if (hash_bytes(source + 16, bytes) != expected) return -1;
    /* Persist evidence before changing execution context or SD controller mode. */
    u32 journal[128] __attribute__((aligned(64)));
    if (vendor_phyread(0x56000, 1, journal) != 1 || journal[0] != 0x50534f42 ||
        journal[2] != operation || journal[3] || hash_bytes((const void *)journal, 508) != journal[127]) return -1;
    journal[3] = 8;
    __asm__ volatile("mrc p15, 0, %0, c1, c0, 0" : "=r"(journal[4]));
    __asm__ volatile("mrs %0, cpsr" : "=r"(journal[5]));
    journal[11] = expected;
    __asm__ volatile("mrc p15, 0, %0, c2, c0, 0" : "=r"(journal[13]));
    __asm__ volatile("mrc p15, 0, %0, c3, c0, 0" : "=r"(journal[14]));
    journal[15] = *(volatile u32 *)(journal[13] & 0xffffc000u);
    volatile u32 *mmc = (volatile u32 *)0x04020000;
    for (u32 i = 0; i < 16; i++) journal[16 + i] = mmc[i];
    journal[127] = hash_bytes((const void *)journal, 508);
    if (vendor_phywrite(0x56000, 1, journal) != 1) return -1;
    volatile u8 *sram = (volatile u8 *)0x20000;
#ifdef MAINLINE_SPL_TRIAL
    /* Borrow SRAM C from VE/DE before copying SPL and using its SRAM stack. */
    volatile u32 *system = (volatile u32 *)0x03000000;
    journal[32] = system[0];
    journal[33] = system[1];
    system[0] |= 1;
    system[1] |= 1u << 24;
    for (u32 i = 0; i < spl_bytes; i++) sram[i] = source[24 + i];
    volatile u8 *bridge = (volatile u8 *)0x54000;
    for (u32 i = 0; i < bridge_bytes; i++) bridge[i] = source[24 + spl_bytes + i];
    journal[12] = (hash_bytes((const void *)sram, spl_bytes) == hash_bytes(source + 24, spl_bytes) &&
                   hash_bytes((const void *)bridge, bridge_bytes) == hash_bytes(source + 24 + spl_bytes, bridge_bytes)) ? expected : 0;
#else
    for (u32 i = 0; i < bytes; i++) sram[i] = source[16 + i];
    journal[12] = hash_bytes((const void *)sram, bytes);
#endif
    journal[3] = journal[12] == expected ? 10 : 9;
    journal[127] = hash_bytes((const void *)journal, 508);
    if (vendor_phywrite(0x56000, 1, journal) != 1 || journal[3] != 10) return -1;
    /* Vendor section 0 is strongly ordered and XN. Give SRAM a normal,
     * executable mapping before fetching any instruction from the copy. */
    volatile u32 *descriptor = (volatile u32 *)(journal[13] & 0xffffc000u);
    if ((*descriptor & 3) != 2 || (*descriptor & 0xfff00000u)) return -1;
    *descriptor = (*descriptor & ~0x1cu) | 0xcu;
    u32 table_line = (u32)descriptor, sram_address = 0x20000;
    __asm__ volatile("mcr p15, 0, %0, c7, c10, 1; dsb sy; mcr p15, 0, %1, c8, c7, 1; dsb sy; isb"
                     :: "r"(table_line), "r"(sram_address) : "memory");
    for (u32 at = 0x20000; at < 0x20000 + bytes; at += 32)
        __asm__ volatile("mcr p15, 0, %0, c7, c10, 1" :: "r"(at) : "memory");
#ifdef MAINLINE_SPL_TRIAL
    for (u32 at = 0x54000; at < 0x54000 + bridge_bytes; at += 32)
        __asm__ volatile("mcr p15, 0, %0, c7, c10, 1" :: "r"(at) : "memory");
#endif
    u32 zero = 0;
    __asm__ volatile("dsb sy; mcr p15, 0, %0, c7, c5, 0; dsb sy; isb" :: "r"(zero) : "memory");
#ifdef MAINLINE_SPL_TRIAL
    ((void (*)(u32))0x54000)(0x20060);
#else
    ((void (*)(u32))0x20000)(operation);
#endif
    return -1;
}
