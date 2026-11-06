/* SPDX-License-Identifier: BSD-3-Clause */
#ifndef BASEOS_BL31_HANDOFF_H
#define BASEOS_BL31_HANDOFF_H

#include <stddef.h>
#include <stdint.h>

#define BASEOS_HANDOFF_ADDRESS      0x4a300400u
#define BASEOS_HANDOFF_MAGIC        0x48534f42u
#define BASEOS_HANDOFF_VERSION      1u
#define BASEOS_HANDOFF_FDT          0x4a200000u
#define BASEOS_HANDOFF_FDT_CAPACITY 0x00100000u
#define BASEOS_HANDOFF_NS_LOW       0x4a000000u
#define BASEOS_HANDOFF_NS_HIGH      0x4e000000u
#define BASEOS_HANDOFF_KERNEL       0x40200000u
#define BASEOS_HANDOFF_FLAG_TRIAL   1u
#define BASEOS_DRAM_VENDOR_WORDS    1u
#define BASEOS_RESET_SOC_WDT        0u
#define BASEOS_HANDOFF_RECORD_LOW   0x4a300000u
#define BASEOS_HANDOFF_RECORD_HIGH  0x4a301000u
#define BASEOS_HANDOFF_T4_ADDRESS   0x4a300800u
#define BASEOS_HANDOFF_T4_CAPACITY  512u

/* Fixed little-endian ABI. Integrity detects damage, not hostile firmware. */
struct baseos_bl31_handoff {
    uint32_t magic, version, bytes, fnv;
    uint64_t fdt;
    uint32_t fdt_capacity, flags;
    uint64_t ns_entry;
    uint64_t bl31_base, bl31_limit;
    uint64_t ram_bytes;
    uint32_t dram_type, dram_flags;
    uint32_t dram_words[32];
    uint8_t generation[32];
    uint32_t reset_policy;
    uint32_t reserved[5];
};

#define BASEOS_HANDOFF_OFFSET(field, offset) \
    _Static_assert(offsetof(struct baseos_bl31_handoff, field) == offset, "handoff " #field)
_Static_assert(sizeof(struct baseos_bl31_handoff) == 256, "handoff bytes");
BASEOS_HANDOFF_OFFSET(magic, 0x00);
BASEOS_HANDOFF_OFFSET(version, 0x04);
BASEOS_HANDOFF_OFFSET(bytes, 0x08);
BASEOS_HANDOFF_OFFSET(fnv, 0x0c);
BASEOS_HANDOFF_OFFSET(fdt, 0x10);
BASEOS_HANDOFF_OFFSET(fdt_capacity, 0x18);
BASEOS_HANDOFF_OFFSET(flags, 0x1c);
BASEOS_HANDOFF_OFFSET(ns_entry, 0x20);
BASEOS_HANDOFF_OFFSET(bl31_base, 0x28);
BASEOS_HANDOFF_OFFSET(bl31_limit, 0x30);
BASEOS_HANDOFF_OFFSET(ram_bytes, 0x38);
BASEOS_HANDOFF_OFFSET(dram_type, 0x40);
BASEOS_HANDOFF_OFFSET(dram_flags, 0x44);
BASEOS_HANDOFF_OFFSET(dram_words, 0x48);
BASEOS_HANDOFF_OFFSET(generation, 0xc8);
BASEOS_HANDOFF_OFFSET(reset_policy, 0xe8);
BASEOS_HANDOFF_OFFSET(reserved, 0xec);
#undef BASEOS_HANDOFF_OFFSET

static inline uint32_t baseos_handoff_fnv(const struct baseos_bl31_handoff *h)
{
    const uint8_t *p = (const void *)h;
    uint32_t hash = 2166136261u;
    for (unsigned int i = 0; i < sizeof(*h); i++)
        hash = (hash ^ (i >= 12 && i < 16 ? 0 : p[i])) * 16777619u;
    return hash;
}

/* The consumer must validate the physical pointer before copying this object.
 * U2 is trial-only and enters the already validated Image at its fixed arena. */
static inline int baseos_handoff_valid(const struct baseos_bl31_handoff *h,
                                      uint64_t bl31_base, uint64_t bl31_limit)
{
    if (h->magic != BASEOS_HANDOFF_MAGIC || h->version != BASEOS_HANDOFF_VERSION ||
        h->bytes != sizeof(*h) || h->fnv != baseos_handoff_fnv(h) ||
        h->flags != BASEOS_HANDOFF_FLAG_TRIAL ||
        h->dram_flags != BASEOS_DRAM_VENDOR_WORDS ||
        h->reset_policy != BASEOS_RESET_SOC_WDT ||
        h->bl31_base != bl31_base || h->bl31_limit != bl31_limit ||
        (h->ram_bytes != 0x40000000ull && h->ram_bytes != 0x80000000ull) ||
        h->ns_entry != BASEOS_HANDOFF_KERNEL || h->ns_entry < bl31_limit ||
        h->ns_entry >= 0x40000000ull + h->ram_bytes ||
        h->dram_type != h->dram_words[1] ||
        h->ram_bytes != (uint64_t)(h->dram_words[7] >> 16) << 20 ||
        (h->fdt & 7) || h->fdt < BASEOS_HANDOFF_NS_LOW || h->fdt >= BASEOS_HANDOFF_NS_HIGH ||
        h->fdt_capacity < 0x10000 || h->fdt_capacity > 0x200000 ||
        (h->fdt_capacity & 0xfff) || h->fdt_capacity > BASEOS_HANDOFF_NS_HIGH - h->fdt)
        return 0;
    uint64_t end = h->fdt + h->fdt_capacity;
    if (h->fdt < BASEOS_HANDOFF_RECORD_HIGH && end > BASEOS_HANDOFF_RECORD_LOW)
        return 0;
    for (unsigned int i = 0; i < 5; i++)
        if (h->reserved[i])
            return 0;
    uint8_t generation = 0;
    for (unsigned int i = 0; i < sizeof(h->generation); i++)
        generation |= h->generation[i];
    return generation != 0;
}

#endif
