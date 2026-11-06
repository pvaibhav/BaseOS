#ifndef BASEOS_NATIVE_CHECKS_H
#define BASEOS_NATIVE_CHECKS_H
#include <stdint.h>
#include "native-handoff.h"
static inline uint32_t little32(const unsigned char *p)
{
    return (uint32_t)p[0] | (uint32_t)p[1] << 8 | (uint32_t)p[2] << 16 | (uint32_t)p[3] << 24;
}

static inline uint32_t record_hash(const struct baseos_native_record *record)
{
    const unsigned char *p = (const void *)record;
    uint32_t hash = 2166136261u;
    for (unsigned i = 0; i < sizeof(*record); i++)
        hash = (hash ^ (i >= 196 && i < 200 ? 0 : p[i])) * 16777619u;
    return hash;
}

static inline int checked_extent(uint32_t *offset, uint32_t bytes, uint32_t limit, uint32_t *begin)
{
    if (bytes > limit || bytes > limit - *offset || bytes > 0xfffff800u) return -1;
    uint32_t rounded = (bytes + 2047u) & ~2047u;
    if (rounded > limit - *offset) return -1;
    *begin = *offset;
    *offset += rounded;
    return 0;
}

#endif
