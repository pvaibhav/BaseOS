/* Diagnostic witness reads backing RAM after A32 caches/MMU are disabled. */
#include "native-trace-constants.h"
typedef unsigned char u8;
typedef unsigned int u32;

static u32 checksum(const u8 *bytes, u32 count)
{
    u32 hash = 2166136261u;
    while (count >= 4) {
        u32 word = *(const u32 *)bytes;
        for (unsigned shift = 0; shift < 32; shift += 8)
            hash = (hash ^ (word >> shift & 255)) * 16777619u;
        bytes += 4;
        count -= 4;
    }
    while (count--) hash = (hash ^ *bytes++) * 16777619u;
    return hash;
}

unsigned native_check_entry(void)
{
    if (checksum((void *)0x40080000ul, BASEOS_KERNEL_BYTES) != BASEOS_KERNEL_FNV) return 0x9001;
    *(volatile u32 *)0x0700013cul = 0x42530032;
    const volatile u32 *proof = (void *)0x4a300100ul;
    u32 bytes = proof[0], wanted = proof[1];
    if (bytes < 40 || bytes > 0x100000 || checksum((void *)0x44000000ul, bytes) != wanted) return 0x9002;
    return 0;
}
