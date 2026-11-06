#include <stdint.h>
#include <string.h>
#include "native-handoff.h"
#include "native-generated.h"
#include "native-panel.h"
#include "panel-probe.h"
extern uint64_t native_ticks(void);
static uint32_t panel_read32(uint32_t a) { uint32_t v=*(volatile uint32_t *)(uintptr_t)a; asm volatile("dmb sy" ::: "memory"); return v; }
__attribute__((noinline)) int baseos_spl_panel(void)
{
    static const uint32_t wait_ms[4] = BASEOS_PANEL_WAITS_MS;
    const struct baseos_native_panel *panel = (void *)BASEOS_NATIVE_PANEL_ADDRESS;
    const struct baseos_native_record *record = (void *)BASEOS_NATIVE_RECORD_ADDRESS;
    if (panel->magic != BASEOS_NATIVE_PANEL_MAGIC || panel->version != BASEOS_NATIVE_PANEL_VERSION ||
        panel->bytes != sizeof(*panel) || panel->rule != BASEOS_PANEL_RULE || panel->panel > 1 ||
        (BASEOS_PANEL_RULE < 2 && panel->panel != BASEOS_PANEL_RULE) ||
        baseos_panel_hash(panel) != panel->fnv || memcmp(panel->generation, baseos_generation, 32) ||
        panel->ticks[0] < record->started_ticks || panel->ticks[4] > native_ticks()) return -1;
    for (unsigned i = 0; i < 4; i++)
        if (panel->ticks[i + 1] < panel->ticks[i] ||
            panel->ticks[i + 1] - panel->ticks[i] < (uint64_t)wait_ms[i] * BASEOS_PANEL_TICKS_PER_MS) return -1;
    /* PI14/PI15 outputs, power on, reset released; PE6 still configured and strapped alike. */
    if ((panel_read32(0x0300b124u) & 0xff000000u) != 0x11000000u ||
        (panel_read32(0x0300b130u) & 0xc000u) != 0xc000u) return -1;
    if (BASEOS_PANEL_RULE == 2 &&
        ((panel_read32(0x0300b090u) & 0x0f000000u) != 0x06000000u ||
         (BASEOS_PANEL_PULL_UP && (panel_read32(0x0300b0acu) & 0x3000u) != 0x1000u) ||
         (unsigned)!(panel_read32(0x0300b0a0u) & 0x40u) != panel->panel)) return -1;
    return (int)panel->panel;
}
