#ifndef BASEOS_NATIVE_PANEL_H
#define BASEOS_NATIVE_PANEL_H

#include <stdint.h>

/* The SPL runs the vendor panel power, reset and strap steps (GPIO only) while it
 * reads the card, then leaves this record for the A32 loader. The loader trusts it
 * only when every vendor minimum wait is proved and the pins still match. */
#define BASEOS_NATIVE_PANEL_MAGIC 0x314c4e50u
#define BASEOS_NATIVE_PANEL_VERSION 1u
#define BASEOS_NATIVE_PANEL_ADDRESS 0x4a300280u
#define BASEOS_PANEL_TICKS_PER_MS 24000u

/* Minimum waits after power on, reset high, reset low and the final reset high. */
#define BASEOS_PANEL_WAITS_MS {30u, 15u, 50u, 100u}

struct baseos_native_panel {
    uint32_t magic, version, bytes, fnv;
    uint32_t panel, rule;
    /* Counter values taken after each step: power on, reset high, reset low,
     * reset high, and the strap read (or the end of the last wait for fixed panels). */
    uint64_t ticks[5];
    unsigned char generation[32];
};
_Static_assert(sizeof(struct baseos_native_panel) == 96, "native panel record ABI");
_Static_assert(__builtin_offsetof(struct baseos_native_panel, ticks) == 24, "native panel timing ABI");

static inline uint32_t baseos_panel_hash(const struct baseos_native_panel *record)
{
    const unsigned char *bytes = (const void *)record;
    uint32_t hash = 2166136261u;
    for (unsigned i = 0; i < sizeof(*record); ++i)
        hash = (hash ^ (i >= 12 && i < 16 ? 0 : bytes[i])) * 16777619u;
    return hash;
}

#endif
