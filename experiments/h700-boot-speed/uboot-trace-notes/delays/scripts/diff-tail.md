
## 3. Waits in the shared rgsp/rg34xxsp code whose length depends on hardware or data

Offsets are the same in rgsp and rg34xxsp. "post-logo" means the site can be reached from board_late_init after the logo call (0x2b22 onwards), main_loop, or the card read and bootm path. This is a static call-graph approximation.

| where | what | length | condition | when |
|---|---|---|---|---|
| panel init 0x262e4 (flow step 1, timer IRQ) | PE6 strap branch at 0x263ec | 465 ms (PE6 low, "boe": 15+50+100 then 200 at 0x26aec and 100 at 0x26af8) or 285 ms (PE6 high, "old": 15+50+100 then 120 at 0x279f0) | the panel fitted, read from PE6 | busy-waits in the timer0/1 IRQ about 30 ms after boot_gui_init. It stalls whatever code runs then. Largest possible gap between the two devices: 180 ms |
| LCD flow gaps (add_timer from 0x1fb00) | 30, 50, 100 ms after steps 0, 1, 2 | 180 ms | none | timer only, no busy-wait. Nothing on the boot path waits for the "open finished" flag 0x4a0f19f0 |
| bmu_axp2202 SOC getter 0x3c620 (bmu op +0x1c, called through 0x3b7a4 = bx *(*0x4a09f2cc + 0x1c), a pointer set at run time) -> bmu_axp2202_battery_check 0x3c4dc | gauge reset: two countdown loops of 500 x udelay(1000) (0x3c580/0x3c58a or 0x3c5de/0x3c5e8) | about 1000 ms | once per boot (flag 0x4a0e40fc, initial 1, cleared at 0x3c672). Only if soc (PMIC reg 0xa4) == 0 and PMIC reg 0x00 bit 5 is set ("%s adapt reset gauge: soc = 0"), or soc > 60 and bat_vol <= 3499 mV ("%s adapt reset gauge: soc > 60% , bat_vol < 3500") | at the first SOC read. Callers of 0x3b7a4: 0x76e2 in 0x7660 (from power init 0x25d8, pre-logo), and 0x7790/0x77f0 in 0x776c (from the battery check 0x7b44, post-logo) |
| bmu_axp2202_probe 0x3c440 -> 0x3c324 ("battery probe reset :STATUS0:0x%x") | countdowns of 200 x udelay(1000) at 0x3c424 and 2000 x udelay(1000) at 0x3c42e | up to about 2.2 s | battery or gauge status read from the PMIC | PMU probe, pre-logo |
| 0x34b8 (board_late_init, 0x2b46) | busy poll on get_timer_masked, limit > 199 ms | <= 200 ms | while gd->0x130 == 1 (IR boot recovery is armed and the IR timer has not fired yet). add_timer at 0x6e66 takes its period from the ir_boot_recovery DT config | post-logo |
| battery check 0x7b44 and its callees 0x776c / 0x7904 / 0x7958 | udelay(1000) loops: 3000 x on the shutdown paths 0x7c78/0x7cb6/0x7cf8; loops with variable bounds at 0x78be/0x78c8 (low battery "pmu_safe_vol"/"pmu_safe_ratio" screen), 0x793a/0x7944 ("dcin is out,to be shutdown"), 0x7a98/0x7aa2 (battery temperature "safe_temp_H/L") | 3 s, or variable | battery voltage, ratio and temperature, DC-in | post-logo |
| autoboot 0xed48 | udelay(10000) loop until get_timer >= 1000 ms (0xedea), once per bootdelay second | bootdelay x 1 s | env bootdelay | post-logo |
| verified-boot screen 0x6a38 | countdown loops of 1000, 1000 and 30000 x udelay(1000) | 1, 1 or 30 s | gd->0xc0 != 0 (orange or red state) | bootm, post-logo |
| MMC: 0x179d4 (poll with udelay(100), 1000 ms limit at 0x17a02); 0x17acc ("Timeout waiting card ready", udelay(1000) loop); mmc_rint_wait 0x1ae54 and update_clk 0x1b0fc (variable limits); FIFO read-remain loop 0x1b4a8 (1001 x 1 us) | | card dependent | card state and speed | card reads, post-logo |

Sites of 100 ms or more (constant argument, or countdown loops that total 100 ms or more). They are the same in all eleven builds except the panel init. Offsets are for rgsp/rg34xxsp:
- udelay(250000) 0x13462 in 0x1338c and 0x138c2 in 0x13868 (command code without strings, not on the boot path)
- disp_delay_ms(300) 0x20c12 in 0x20b90 (display device switch, only when switching to output type 2; the LCD is type 1); disp_delay_ms(1000) 0x25f34 in 0x25eac ("tv set func null hdl!", TV output)
- mdelay(3000) 0x5c708 in usb_update_main_loop 0x5c040 ("Device will shutdown in 3 Secends"); udelay(100000) 0x69d3a in 0x69d30 (called from panic 0x69d50)
- countdown loops: 0x6a38 vboot (1000/1000/30000 ms), 0x7b44 (3 x 3000 ms, shutdown), 0x3c324 (200, 2000 ms), 0x3c4dc (4 x 500 ms), 0x5c84c and 0x5cf5c (USB fastboot, 1000 ms), 0x6e75c and 0x722e4 (sprite/card burn, 3000 ms), 0x7147c (sprite cartoon, 4 x 500 ms), 0x9e04 and 0x9e94 (loads/loady, 100 ms)
- panel init 0x262e4: 100, 200, 100 or 120 ms (section 2)

Timeout loops with a constant limit of 500 ms or more (same in all builds; rgsp offsets): usb burn 0xd494 (3000 ms at 0xd518, 800 ms at 0xd530; gated by /soc/target burn_key, which is 0), autoboot 0xedea (1000 ms per second of bootdelay), 0x13348 (2000 ms, no strings), MMC 0x179d4 (1000 ms), SPI flash 0x3d658 ("flash operation timed out", 39999 ms), 0x3f1ac ("tx wait_dma_send_over fail", 1000 ms), USB mass storage 0x5b170 (30000 ms), net ARP 0x6ac1c (5000 ms). Data-dependent limits: mmc_rint_wait 0x1ae7c, update_clk 0x1b15e, sunxi_mmc_do_send_cmd_common 0x1b8d4/0x1bb7e, and the net/tftp loops.

## 4. Caveats

- Function extents come from recursive descent. A site counts as "in a loop" only when a later backward branch returns to a point from which the site is reachable again. Loop counts are resolved only for countdown registers set by an immediate.
- r0 is resolved only from a short straight run of instructions before the call (mov/movs/movw/movt, literal loads, simple arithmetic). Values loaded from memory or set under an IT block are reported as "variable" or "conditional(...)".
- sunxi_lcd_delay_ms (0x2621c in every build) and the other src_ops wrappers read their target from the panel driver's ops struct (0x4a0f3d48 in rgsp/rg34xxsp). 0x2008c fills that struct at run time from code literals (op +0 = 0x24dd5 disp_delay_ms, +4 = 0x24ddf disp_delay_us). The word in the image at that address is not the run-time value.
- No panel driver in these builds uses a sunxi_lcd_delay_us wrapper. The only display us-delays are direct calls of disp_delay_us in the TCON/DSI code (0x20b68, 0x2fd00.., 0x30294/0x302e0 in rgsp).
