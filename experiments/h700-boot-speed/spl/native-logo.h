#ifndef BASEOS_NATIVE_LOGO_H
#define BASEOS_NATIVE_LOGO_H

#define BASEOS_NATIVE_LOGO_MAGIC 0x31474f4cu
#define BASEOS_NATIVE_LOGO_RECORD_ADDRESS 0x4a300200u
#define BASEOS_NATIVE_LOGO_BMP_ADDRESS 0x4b400000u
#define BASEOS_NATIVE_LOGO_CAPACITY 0x00400000u

/* Only these trained sizes have been replayed through the vendor allocator.
 * The per-model offset is generated from that replay, never from a lab address.
 * SPL reads the BMP in the low arena; only A32 produces or scans out pixels. */
static inline unsigned int baseos_logo_address(unsigned long long ram_bytes, unsigned int from_end)
{
    if (ram_bytes != 0x40000000ull && ram_bytes != 0x80000000ull) return 0;
    return (unsigned int)(0x40000000ull + ram_bytes - from_end);
}
#define BASEOS_NATIVE_LOGO_PIXEL_ADDRESS \
    baseos_logo_address(*(volatile unsigned long long *)0x4a300010u, BASEOS_LOGO_RAM_END_OFFSET)

struct baseos_native_logo {
    unsigned int magic, bytes, fnv, reserved;
};
_Static_assert(sizeof(struct baseos_native_logo) == 16, "native logo descriptor ABI");

static inline unsigned int baseos_logo_hash(const unsigned char *bytes, unsigned int count)
{
    unsigned int hash = 2166136261u;
    while (count--) hash = (hash ^ *bytes++) * 16777619u;
    return hash;
}

#endif
