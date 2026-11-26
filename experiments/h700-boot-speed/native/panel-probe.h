#ifndef BASEOS_PANEL_PROBE_H
#define BASEOS_PANEL_PROBE_H

#include <stdint.h>

struct baseos_panel_io {
    uint32_t (*read32)(uint32_t address);
    void (*write32)(uint32_t address, uint32_t value);
    uint64_t (*ticks)(void);
};

static inline void baseos_panel_update(const struct baseos_panel_io *io,
                                      uint32_t address, uint32_t mask, uint32_t value)
{
    io->write32(address, (io->read32(address) & ~mask) | value);
}

static inline int baseos_panel_wait(const struct baseos_panel_io *io, unsigned milliseconds)
{
    uint64_t start = io->ticks();
    for (unsigned remaining = 100000000u; remaining; remaining--)
        if (io->ticks() - start >= (uint64_t)milliseconds * 24000u) return 0;
    return -1;
}

/* Return old=0, boe=1, or -1 without acknowledging the pending boot on failure. */
static inline int baseos_panel_probe(unsigned rule, unsigned pull_up,
                                    const struct baseos_panel_io *io)
{
    if (rule < 2) return (int)rule;
    if (rule != 2 || pull_up > 1) return -1;

    baseos_panel_update(io, 0x0300b124u, 0xff000000u, 0x11000000u);
    baseos_panel_update(io, 0x0300b130u, 0x8000u, 0x8000u);
    if (baseos_panel_wait(io, 30)) return -1;
    baseos_panel_update(io, 0x0300b130u, 0x4000u, 0x4000u);
    if (baseos_panel_wait(io, 15)) return -1;
    baseos_panel_update(io, 0x0300b130u, 0x4000u, 0);
    if (baseos_panel_wait(io, 50)) return -1;
    baseos_panel_update(io, 0x0300b130u, 0x4000u, 0x4000u);
    if (baseos_panel_wait(io, 100)) return -1;

    baseos_panel_update(io, 0x0300b090u, 0x0f000000u, 0x06000000u);
    if (pull_up) baseos_panel_update(io, 0x0300b0acu, 0x3000u, 0x1000u);
    return !(io->read32(0x0300b0a0u) & 0x40u);
}

#endif
