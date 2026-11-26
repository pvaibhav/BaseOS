#ifndef BASEOS_NATIVE_PANEL_H
#define BASEOS_NATIVE_PANEL_H

#include <stdint.h>

/* The SPL runs the vendor panel power, reset and strap steps (GPIO only) and
 * begins the SPI9 init stream while reading the card, then leaves this record
 * for the A32 loader. The loader trusts it only when waits, pins, ticks and
 * stream hash prove valid; otherwise it finishes or restarts cleanly. */
#define BASEOS_NATIVE_PANEL_MAGIC 0x314c4e50u
#define BASEOS_NATIVE_PANEL_VERSION 2u
#define BASEOS_NATIVE_PANEL_ADDRESS 0x4a300280u
#define BASEOS_PANEL_TICKS_PER_MS 24000u

/* Minimum waits after power on, reset high, reset low and the final reset high. */
#define BASEOS_PANEL_WAITS_MS {30u, 15u, 50u, 100u}

/* Compact SPI9 stream table staged via firmware.bin in the non-overlapping records arena. */
#define BASEOS_PANEL_STREAMS_ADDRESS 0x4a302000u
#define BASEOS_PANEL_STREAMS_CAPACITY 0x2000u
#define BASEOS_PANEL_STREAMS_MAGIC 0x31535042u /* "BPS1" */

#define BASEOS_PANEL_STREAM_NONE 0u
#define BASEOS_PANEL_STREAM_PARTIAL 1u
#define BASEOS_PANEL_STREAM_DONE 2u

struct baseos_panel_streams {
    uint32_t magic, version;
    uint32_t old_offset, old_count;
    uint32_t boe_offset, boe_count;
    uint32_t old_fnv, boe_fnv;
};
_Static_assert(sizeof(struct baseos_panel_streams) == 32, "panel streams header ABI");

struct baseos_native_panel {
    uint32_t magic, version, bytes, fnv;
    uint32_t panel, rule;
    /* Counter values taken after each step: power on, reset high, reset low,
     * reset high, and the strap read (or the end of the last wait for fixed panels). */
    uint64_t ticks[5];
    unsigned char generation[32];
    /* SPI9 stream handoff from SPL: NONE, PARTIAL (waiting at stream_wait_ms since
     * stream_ticks before stream_next), or DONE (all ops sent, 50 ms post-stream
     * wait started at stream_ticks). */
    uint32_t stream_state, stream_next, stream_fnv, stream_wait_ms;
    uint64_t stream_ticks;
    uint64_t reserved;
};
_Static_assert(sizeof(struct baseos_native_panel) == 128, "native panel record ABI");
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
