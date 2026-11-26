# Delay sites: rg34xxsp against rgsp, and all eleven builds

## 1. rg34xxsp against rgsp: no difference

The two U-Boot items are the same build of the same source and configuration, made on different days. Checked three ways (scripts in /tmp/uboot-delays/scripts):

- codeeq.py: every 32-bit word in 0..code_end (0x73220 in both) is identical, except the item header checksum at 0xc and literal-pool words that point into .rodata. All but one of those differ by +3. The exception is 0x66b6c, a pointer to the string "39": rgsp 0x4a09c578, rg34xxsp 0x4a07c57e.
- dataeq.py: .rodata/.data from 0x73000 to the end of the item match, apart from one 3-byte shift. The only differences in content are the build stamp strings: "U-Boot 2018.05 (Jun 24 2026 - 19:09:39 +0800)", "06/24/2026" and "19:09:39" in rgsp, against "U-Boot 2018.05 (May 26 2026 - 15:48:53 +0800)", "05/26/2026" and "15:48:53" in rg34xxsp. The shift has a simple cause. In rgsp the linker tail-merges the string "39" into "19:09:39". In rg34xxsp it cannot, so it emits a separate "39" at 0x7c57e. Three bytes of alignment padding at 0x9d815 in rgsp absorb the shift again.
- Every delay site, timeout loop, add_timer site and panel-flow table entry is at the same offset with the same constant in both builds: inventory-rgsp.json and inventory-rg34xxsp.json are identical apart from the build name. Both builds carry the same three panel structs (default_lcd, bp101wx1 and rg34xxsp_v1 at 0xa40e8, flow 0x27bb4: 0x27c58 then 30 ms, 0x262e4 then 50 ms, 0x26230 then 100 ms, 0x27c18) and the same panel init 0x262e4. The DT of both selects rg34xxsp_v1 720x480.

Therefore no delay constant, loop bound or timeout limit in U-Boot explains the 1.5 s. The same code must take a different path on the two devices, or wait on hardware for longer. Section 3 lists the waits in this identical code whose length depends on hardware state or data.

## 2. All eleven builds

Apart from the panel init function, every delay site, timeout limit and add_timer site in every build matches rgsp. Matching is by function (string set, or a position-independent mnemonic signature for functions without strings). Some functions are split differently between builds (for example the r_i2c init 0x3d2e4/0x3d368 in rgsp, merged into one function in the others) or have different string sets. These carry the same delay multiset and are listed as "equivalent". The sums in the summary table are therefore the same for all builds, except the panel init column.

| build | DT panel | panel init function | delays before strap branch | PE6 low (lcd_type 1, "boe") | PE6 high (lcd_type 0, "old") | after join | total low / high ms | flow gaps ms |
|---|---|---|---|---|---|---|---|---|
| rg28xx | fog_fj035fhd05_v1 | 0x262e4 ['lcd_panel_uboot_fj035fhd05_v1_init'] | 15 + 50 + 100 + 120 + 10 (no PE read) | - | - | - | 295 / 295 | 180 |
| rg34xx | rg34xx_v1 | 0x262e4 ['--allen--PE_pull[31:0] = 0x%x', '--allen--PE_pull[31:0] = 0x%x'] | 15 + 50 + 100 | - | - | 200 + 100 | 465 / 465 | 180 |
| rg34xxsp | rg34xxsp_v1 | 0x262e4 ['--allen--PE_pull[31:0] = 0x%x', '--allen--PE_pull[31:0] = 0x%x'] | 15 + 50 + 100 | 200 + 100 | 120 | - | 465 / 285 | 180 |
| rg35xxh | fog_fj035fhd05_v1 | 0x262e4 ['--allen--PE_pull[31:0] = 0x%x', '--allen--PE_pull[31:0] = 0x%x'] | 15 + 50 + 100 | 200 | 120 | 100 | 465 / 385 | 180 |
| rg35xxplus | fog_fj035fhd05_v1 | 0x262e4 ['--allen--PE_pull[31:0] = 0x%x', '--allen--PE_pull[31:0] = 0x%x'] | 15 + 50 + 100 | 200 | 120 | 100 | 465 / 385 | 180 |
| rg35xxpro | fog_fj035fhd05_v1 | 0x262e4 ['--allen--PE_pull[31:0] = 0x%x', '--allen--PE_pull[31:0] = 0x%x'] | 15 + 50 + 100 | 200 | 120 | 100 | 465 / 385 | 180 |
| rg35xxsp | fog_fj035fhd05_v1 | 0x262e4 ['--allen--PE_data[31:0] = 0x%x,lcd_type = %d', 'lcd_panel_uboot_fj035fhd05_v1_init'] | 15 + 50 + 100 | 120 | 120 + 10 | - | 285 / 295 | 180 |
| rg40xxh | fog_fj035fhd05_v1 | 0x262e4 ['--allen--PE_data[31:0] = 0x%x,lcd_type = %d', 'lcd_panel_uboot_fj035fhd05_v1_init'] | 15 + 50 + 100 | 120 | 200 | 20 | 305 / 385 | 180 |
| rg40xxv | fog_fj035fhd05_v1 | 0x262e4 ['--allen--PE_data[31:0] = 0x%x,lcd_type = %d', 'lcd_panel_uboot_fj035fhd05_v1_init'] | 15 + 50 + 100 | 120 | 200 | 20 | 305 / 385 | 180 |
| rgcubexx | fog_fj035fhd05_v1 | 0x262e4 ['lcd_panel_uboot_fj035fhd05_v1_init'] | 15 + 50 + 100 + 200 + 100 (no PE read) | - | - | - | 465 / 465 | 180 |
| rgsp | rg34xxsp_v1 | 0x262e4 ['--allen--PE_pull[31:0] = 0x%x', '--allen--PE_pull[31:0] = 0x%x'] | 15 + 50 + 100 | 200 + 100 | 120 | - | 465 / 285 | 180 |

In every build that reads the strap, the delays before the branch are the panel reset sequence on PI14 (0x0300b130 bit 14): high, 15 ms; low, 50 ms; high, 100 ms. The delays after the branch are the sleep-out (0x11) and display-on (0x29) waits of the bit-banged init commands. Site offsets per build are in summary.md and in inventory-<build>.json under panels[].steps[].strap.

### Differences from rgsp, per build

**rg28xx**: no delay difference outside the panel init.
- function with strings ['--allen--PE_data[31:0] = 0x%x', '--allen--PE_pull[31:0] = 0x%x', '===%s:L%d,lcd_type = %d', 'boe', 'lcd_panel_uboot_fj035fhd05_v1_init', 'lcd_type']: rgsp 0x26316:15, 0x26346:50, 0x26376:100, 0x26aec:200, 0x26af8:100, 0x279f0:120 | rg28xx 
- function with strings ['===%s:L%d,lcd_type = %d', 'boe', 'lcd_panel_uboot_fj035fhd05_v1_init', 'lcd_type', 'old']: rgsp  | rg28xx 0x26316:15, 0x26346:50, 0x2637a:100, 0x26914:120, 0x26950:10
- equivalent (same delays, different function boundary or strings): ['twi_send_clk_9pulse'] 'sunxi_usb_fastboot_status=%d'<->'start addr %s'
**rg34xx**: no delay difference outside the panel init.
- function with strings ['--allen--PE_data[31:0] = 0x%x', '--allen--PE_pull[31:0] = 0x%x', '===%s:L%d,lcd_type = %d', 'boe', 'lcd_panel_uboot_fj035fhd05_v1_init', 'lcd_type']: rgsp 0x26316:15, 0x26346:50, 0x26376:100, 0x26aec:200, 0x26af8:100, 0x279f0:120 | rg34xx 0x26316:15, 0x26346:50, 0x26376:100, 0x26aea:200, 0x26af6:100
- equivalent (same delays, different function boundary or strings): ['twi_send_clk_9pulse'] '[mmc]: switch to HS mode fail'<->'[mmc]: switch to HS mode fail'
**rg34xxsp**: identical to rgsp (section 1).
**rg35xxh**: no delay difference outside the panel init.
- equivalent (same delays, different function boundary or strings): ['twi_send_clk_9pulse'] 
**rg35xxplus**: no delay difference outside the panel init.
- equivalent (same delays, different function boundary or strings): ['twi_send_clk_9pulse'] 
**rg35xxpro**: no delay difference outside the panel init.
- equivalent (same delays, different function boundary or strings): ['twi_send_clk_9pulse'] 
**rg35xxsp**: no delay difference outside the panel init.
- function with strings ['--allen--PE_data[31:0] = 0x%x', '--allen--PE_pull[31:0] = 0x%x', '===%s:L%d,lcd_type = %d', 'boe', 'lcd_panel_uboot_fj035fhd05_v1_init', 'lcd_type']: rgsp 0x26316:15, 0x26346:50, 0x26376:100, 0x26aec:200, 0x26af8:100, 0x279f0:120 | rg35xxsp 
- function with strings ['--allen--PE_data[31:0] = 0x%x,lcd_type = %d', '===%s:L%d,lcd_type = %d', 'boe', 'lcd_panel_uboot_fj035fhd05_v1_init', 'lcd_type', 'old']: rgsp  | rg35xxsp 0x26316:15, 0x26346:50, 0x26376:100, 0x272e0:120, 0x27bf2:120, 0x27c04:10
- equivalent (same delays, different function boundary or strings): ['twi_send_clk_9pulse'] 
**rg40xxh**: no delay difference outside the panel init.
- function with strings ['--allen--PE_data[31:0] = 0x%x', '--allen--PE_pull[31:0] = 0x%x', '===%s:L%d,lcd_type = %d', 'boe', 'lcd_panel_uboot_fj035fhd05_v1_init', 'lcd_type']: rgsp 0x26316:15, 0x26346:50, 0x26376:100, 0x26aec:200, 0x26af8:100, 0x279f0:120 | rg40xxh 
- function with strings ['--allen--PE_data[31:0] = 0x%x,lcd_type = %d', '===%s:L%d,lcd_type = %d', 'boe', 'lcd_panel_uboot_fj035fhd05_v1_init', 'lcd_type', 'old']: rgsp  | rg40xxh 0x26316:15, 0x26346:50, 0x26376:100, 0x272c8:120, 0x272da:20, 0x27b6e:200
- equivalent (same delays, different function boundary or strings): ['twi_send_clk_9pulse'] 'battery_exist'<->'battery probe reset :STATUS0:0'
**rg40xxv**: no delay difference outside the panel init.
- function with strings ['--allen--PE_data[31:0] = 0x%x', '--allen--PE_pull[31:0] = 0x%x', '===%s:L%d,lcd_type = %d', 'boe', 'lcd_panel_uboot_fj035fhd05_v1_init', 'lcd_type']: rgsp 0x26316:15, 0x26346:50, 0x26376:100, 0x26aec:200, 0x26af8:100, 0x279f0:120 | rg40xxv 
- function with strings ['--allen--PE_data[31:0] = 0x%x,lcd_type = %d', '===%s:L%d,lcd_type = %d', 'boe', 'lcd_panel_uboot_fj035fhd05_v1_init', 'lcd_type', 'old']: rgsp  | rg40xxv 0x26316:15, 0x26346:50, 0x26376:100, 0x272c8:120, 0x272da:20, 0x27b6e:200
- equivalent (same delays, different function boundary or strings): ['twi_send_clk_9pulse'] 'battery_exist'<->'battery probe reset :STATUS0:0'
**rgcubexx**: no delay difference outside the panel init.
- function with strings ['--allen--PE_data[31:0] = 0x%x', '--allen--PE_pull[31:0] = 0x%x', '===%s:L%d,lcd_type = %d', 'boe', 'lcd_panel_uboot_fj035fhd05_v1_init', 'lcd_type']: rgsp 0x26316:15, 0x26346:50, 0x26376:100, 0x26aec:200, 0x26af8:100, 0x279f0:120 | rgcubexx 
- function with strings ['===%s:L%d,lcd_type = %d', 'boe', 'lcd_panel_uboot_fj035fhd05_v1_init', 'lcd_type', 'old']: rgsp  | rgcubexx 0x26316:15, 0x26346:50, 0x2637a:100, 0x26b8c:200, 0x26b9e:100
- equivalent (same delays, different function boundary or strings): ['twi_send_clk_9pulse'] 

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

