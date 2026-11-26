#ifndef BASEOS_NATIVE_DISPLAY_H
#define BASEOS_NATIVE_DISPLAY_H

#include <stdint.h>
#include "native-logo.h"

#define BASEOS_NATIVE_DISPLAY_MAGIC 0x31505344u
#define BASEOS_NATIVE_DISPLAY_ADDRESS 0x4a300300u

/* Published only after a real plane, TCON and backlight have been enabled. */
struct baseos_native_display {
    uint32_t magic, version, bytes, fnv;
    uint32_t panel, logo_bytes, logo_fnv, pixel_fnv;
    unsigned char generation[32];
    uint64_t shown_ticks;
    uint32_t reserved[2];
};
_Static_assert(sizeof(struct baseos_native_display) == 80, "native display record ABI");
_Static_assert(__builtin_offsetof(struct baseos_native_display, shown_ticks) == 64,
               "native display timing ABI");

static inline uint32_t baseos_display_hash(const struct baseos_native_display *record)
{
    const unsigned char *bytes = (const void *)record;
    uint32_t hash = 2166136261u;
    for (unsigned i = 0; i < sizeof(*record); ++i)
        hash = (hash ^ (i >= 12 && i < 16 ? 0 : bytes[i])) * 16777619u;
    return hash;
}

/* A nonzero damaged record cannot safely imply a cold, inactive display. */
static inline int baseos_display_valid(const struct baseos_native_display *record,
                                       const unsigned char generation[32])
{
    const unsigned char *bytes = (const void *)record;
    unsigned occupied = 0;
    for (unsigned i = 0; i < sizeof(*record); ++i) occupied |= bytes[i];
    if (!occupied) return 0;
    if (record->magic != BASEOS_NATIVE_DISPLAY_MAGIC || record->version != 1 ||
        record->bytes != sizeof(*record) || record->panel > 1 ||
        (record->logo_bytes && record->logo_bytes < 54) || record->logo_bytes > BASEOS_NATIVE_LOGO_CAPACITY ||
        (!record->logo_bytes && record->logo_fnv) ||
        record->reserved[0] || record->reserved[1] || !record->shown_ticks ||
        baseos_display_hash(record) != record->fnv) return -1;
    for (unsigned i = 0; i < 32; ++i)
        if (record->generation[i] != generation[i]) return -1;
    return 1;
}

#endif
