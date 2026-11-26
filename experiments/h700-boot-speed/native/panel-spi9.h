#ifndef BASEOS_PANEL_SPI9_H
#define BASEOS_PANEL_SPI9_H

#include <stdint.h>

/* Callbacks perform one raw 32-bit access. barrier supplies DMB SY on ARM.
 * Single owner; zero-initialize configured before the first byte. */
struct baseos_panel_spi9_io {
    void *context;
    uint32_t (*read)(void *, uint32_t address);
    void (*write)(void *, uint32_t address, uint32_t value);
    void (*barrier)(void *);
};
struct baseos_panel_spi9 {
    struct baseos_panel_spi9_io io;
    unsigned int configured;
};

static inline void baseos_panel_spi9_update(struct baseos_panel_spi9 *port,
                                           uint32_t address, uint32_t mask, uint32_t bits)
{
    uint32_t value = port->io.read(port->io.context, address);
    port->io.barrier(port->io.context);
    value = (value & ~mask) | bits;
    port->io.barrier(port->io.context);
    port->io.write(port->io.context, address, value);
}

static inline void baseos_panel_spi9_send(struct baseos_panel_spi9 *port,
                                         uint8_t byte, unsigned int data)
{
    if (!port->configured) {
        /* Force PI8/9/10/14/15 output, preserve all other mux fields. */
        baseos_panel_spi9_update(port, 0x0300b124u, 0xff000fffu, 0x11000111u);
        port->configured = 1;
    }
    baseos_panel_spi9_update(port, 0x0300b130u, 0x100u, 0);
    uint32_t frame = byte | (data ? 0x100u : 0);
    for (unsigned int bit = 0; bit < 9; ++bit) {
        baseos_panel_spi9_update(port, 0x0300b130u, 0x200u, 0);
        baseos_panel_spi9_update(port, 0x0300b130u, 0x400u, frame & 0x100u ? 0x400u : 0);
        baseos_panel_spi9_update(port, 0x0300b130u, 0x200u, 0x200u);
        frame <<= 1;
    }
    baseos_panel_spi9_update(port, 0x0300b130u, 0x100u, 0x100u);
}
#endif
