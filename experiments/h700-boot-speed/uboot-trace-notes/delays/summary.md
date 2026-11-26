# U-Boot delay inventory: per-build summary

Static analysis of the U-Boot item (file offset 0x800, 1 MiB, linked at 0x4A000000) of each boot-package.bin. Scripts: /tmp/uboot-delays/scripts (ub.py = disassembly core, inventory.py = per-build inventory, gen.py = JSON + sums, report.py = these files). All offsets are relative to 0x4A000000. "board" = code at or above 0x26000, "common" = below 0x26000.

Panel open flow: drv_lcd_open_callback 0x1fa98 runs flow step i, then arms add_timer(flow[i].delay). Steps after the first run in the timer IRQ handler, so the busy-wait delays inside a step (the panel init) stall whatever the CPU is doing at that time. The timer gaps do not block the CPU.

Panel init: lcd_type = !(PE data bit 6) (eor 0x40, ubfx #6) in every build that reads the strap. "PE6 low" = lcd_type 1 = env lcd_type "boe" = fall-through of `cmp lcd_type,#1; bne`. "PE6 high" = lcd_type 0 = env "old" = taken branch.

| build | DT lcd_driver_name (x*y) | flow gaps ms | panel init ms, PE6 low | panel init ms, PE6 high | other board delays: const sites / sum once ms / with loop counts ms | common: const sites / sum once / with loop counts | variable-arg sites (board+common) | timeout loops with const limit (>=500 ms) |
|---|---|---|---|---|---|---|---|---|
| rg28xx | fog_fj035fhd05_v1 (480x640) | 30/50/100/0 = 180 | 295 (no strap read) | 295 (no strap read) | 41 / 3309.24 / 17523.04 | 79 / 2148.458 / 43450.906 | 1+4 | 10 (9) |
| rg34xx | rg34xx_v1 (720x480) | 30/50/100/0 = 180 | 465 | 465 | 41 / 3309.24 / 17523.04 | 79 / 2148.458 / 43450.906 | 1+4 | 10 (9) |
| rg34xxsp | rg34xxsp_v1 (720x480) | 30/50/100/0 = 180 | 465 | 285 | 41 / 3309.24 / 17523.04 | 79 / 2148.458 / 43450.906 | 1+4 | 10 (9) |
| rg35xxh | fog_fj035fhd05_v1 (640x480) | 30/50/100/0 = 180 | 465 | 385 | 41 / 3309.24 / 17523.04 | 79 / 2148.458 / 43450.906 | 1+4 | 10 (9) |
| rg35xxplus | fog_fj035fhd05_v1 (640x480) | 30/50/100/0 = 180 | 465 | 385 | 41 / 3309.24 / 17523.04 | 79 / 2148.458 / 43450.906 | 1+4 | 10 (9) |
| rg35xxpro | fog_fj035fhd05_v1 (640x480) | 30/50/100/0 = 180 | 465 | 385 | 41 / 3309.24 / 17523.04 | 79 / 2148.458 / 43450.906 | 1+4 | 10 (9) |
| rg35xxsp | fog_fj035fhd05_v1 (640x480) | 30/50/100/0 = 180 | 285 | 295 | 41 / 3309.24 / 17523.04 | 79 / 2148.458 / 43450.906 | 1+4 | 10 (9) |
| rg40xxh | fog_fj035fhd05_v1 (640x480) | 30/50/100/0 = 180 | 305 | 385 | 41 / 3309.24 / 17523.04 | 79 / 2148.458 / 43450.906 | 1+4 | 10 (9) |
| rg40xxv | fog_fj035fhd05_v1 (640x480) | 30/50/100/0 = 180 | 305 | 385 | 41 / 3309.24 / 17523.04 | 79 / 2148.458 / 43450.906 | 1+4 | 10 (9) |
| rgcubexx | fog_fj035fhd05_v1 (720x720) | 30/50/100/0 = 180 | 465 (no strap read) | 465 (no strap read) | 41 / 3309.24 / 17523.04 | 79 / 2148.458 / 43450.906 | 1+4 | 10 (9) |
| rgsp | rg34xxsp_v1 (720x480) | 30/50/100/0 = 180 | 465 | 285 | 41 / 3309.24 / 17523.04 | 79 / 2148.458 / 43450.906 | 1+4 | 10 (9) |

"sum once" adds every constant delay site once. "with loop counts" multiplies sites in countdown loops by the resolved count; loops whose bound is a hardware/memory condition count once. These are static sums over all code, most of which never runs on a normal boot. The panel init function is excluded from the "other board" column.

Panel flow total (busy + timer gaps) = gaps + panel init: see the table. The non-init flow steps (power on, tcon enable, backlight) contain no delay calls in any build (searched 5 call levels deep, through the lcd source-op pointers).


## rg28xx

- udelay wrapper (lib/time.c): 0x698a8; sunxi_lcd_delay_ms: 0x2621c (bx *(src_ops+0), pointer filled at run time by 0x2008c; it resolves to disp_delay_ms 0x24dd4)
- panel struct 0xa4c48 'fog_fj035fhd05_v1', open flow 0x26b68: 0x26c0c then 30 ms, 0x262e4 then 50 ms, 0x26230 then 100 ms, 0x26bcc then 0 ms
- other panel structs in the build: default_lcd flow 0x26af8 30/50/100/0; bp101wx1 flow 0x26b38 10/220/0
- panel init 0x262e4: no strap read; delays in order: 15 + 50 + 100 + 120 + 10 = 295 ms
- variable-argument delay sites: 0x59f90 in 0x59f34 (__msdelay/mdelay), 0x9928 in 0x98ae (udelay), 0x13586 in 0x134fc (udelay), 0x23208 in 0x230dc (disp_delay_ms), 0x23452 in 0x23378 (disp_delay_ms)
- timeout loops with a constant limit (site, function, limit ms, compare, region, reachable after logo):
  - 0x34c0 in 0x34b8: 199 ms (cmp r0, #0xc7) common post-logo
  - 0xd518 in 0xd494: 3000 ms (cmp r0, r8) common
  - 0xd530 in 0xd494: 800 ms (cmp.w r0, #0x320) common
  - 0xedea in 0xed48: 1000 ms (cmp.w r0, #0x3e8) common post-logo
  - 0x1335c in 0x13348: 2000 ms (cmp.w r0, #0x7d0) common
  - 0x17a02 in 0x179d4: 1000 ms (cmp.w r0, #0x3e8) common post-logo
  - 0x3d48c in 0x3d478: 39999 ms (cmp r0, r8) board
  - 0x3f1d0 in 0x3efcc: 1000 ms (cmp.w r0, #0x3e8) board
  - 0x5b348 in 0x5af90: 30000 ms (cmp r0, r3) board
  - 0x6aa46 in 0x6aa3c: 5000 ms (cmp r3, r1) board
- timeout loops with a variable limit: 0xa85e in 0xa814, 0x1ae7c in 0x1ae54, 0x1b15e in 0x1b0fc, 0x1b8d4 in 0x1b350, 0x1bb7e in 0x1b350, 0x3f222 in 0x3efcc, 0x6af72 in 0x6af6c, 0x6b8ca in 0x6b784
- add_timer sites: 0x6e66 in 0x6d94: variable; 0xbfc2 in 0xbf8c: variable; 0xc01a in 0xbfe4: variable; 0x1fb00 in 0x1fa98: from LCD open flow table (flow[i].delay); 0x6e818 in 0x6e7f0: variable; 0x6e89a in 0x6e82c: variable; 0x6e916 in 0x6e8d0: variable

## rg34xx

- udelay wrapper (lib/time.c): 0x69260; sunxi_lcd_delay_ms: 0x2621c (bx *(src_ops+0), pointer filled at run time by 0x2008c; it resolves to disp_delay_ms 0x24dd4)
- panel struct 0xa38d0 'rg34xx_v1', open flow 0x2738c: 0x27430 then 30 ms, 0x262e4 then 50 ms, 0x26230 then 100 ms, 0x273f0 then 0 ms
- other panel structs in the build: default_lcd flow 0x2731c 30/50/100/0; bp101wx1 flow 0x2735c 10/220/0
- panel init 0x262e4: before the strap branch 15 + 50 + 100; PE6 low: -; PE6 high: -; after the join: 200 + 100. Totals 465 / 465 ms. Branch: cmp r5, #1 ; bne.w #0x26b18
- variable-argument delay sites: 0x59948 in 0x598ec (__msdelay/mdelay), 0x9928 in 0x98ae (udelay), 0x13586 in 0x134fc (udelay), 0x23208 in 0x230dc (disp_delay_ms), 0x23452 in 0x23378 (disp_delay_ms)
- timeout loops with a constant limit (site, function, limit ms, compare, region, reachable after logo):
  - 0x34c0 in 0x34b8: 199 ms (cmp r0, #0xc7) common post-logo
  - 0xd518 in 0xd494: 3000 ms (cmp r0, r8) common
  - 0xd530 in 0xd494: 800 ms (cmp.w r0, #0x320) common
  - 0xedea in 0xed48: 1000 ms (cmp.w r0, #0x3e8) common post-logo
  - 0x1335c in 0x13348: 2000 ms (cmp.w r0, #0x7d0) common
  - 0x17a02 in 0x179d4: 1000 ms (cmp.w r0, #0x3e8) common post-logo
  - 0x3ce44 in 0x3ce30: 39999 ms (cmp r0, r8) board
  - 0x3eb88 in 0x3e984: 1000 ms (cmp.w r0, #0x3e8) board
  - 0x5ad00 in 0x5a948: 30000 ms (cmp r0, r3) board
  - 0x6a3fe in 0x6a3f4: 5000 ms (cmp r3, r1) board
- timeout loops with a variable limit: 0xa85e in 0xa814, 0x1ae7c in 0x1ae54, 0x1b15e in 0x1b0fc, 0x1b8d4 in 0x1b350, 0x1bb7e in 0x1b350, 0x3ebda in 0x3e984, 0x6a92a in 0x6a924, 0x6b282 in 0x6b13c
- add_timer sites: 0x6e66 in 0x6d94: variable; 0xbfc2 in 0xbf8c: variable; 0xc01a in 0xbfe4: variable; 0x1fb00 in 0x1fa98: from LCD open flow table (flow[i].delay); 0x6e1d0 in 0x6e1a8: variable; 0x6e252 in 0x6e1e4: variable; 0x6e2ce in 0x6e288: variable

## rg34xxsp

- udelay wrapper (lib/time.c): 0x69a88; sunxi_lcd_delay_ms: 0x2621c (bx *(src_ops+0), pointer filled at run time by 0x2008c; it resolves to disp_delay_ms 0x24dd4)
- panel struct 0xa40e8 'rg34xxsp_v1', open flow 0x27bb4: 0x27c58 then 30 ms, 0x262e4 then 50 ms, 0x26230 then 100 ms, 0x27c18 then 0 ms
- other panel structs in the build: default_lcd flow 0x27b44 30/50/100/0; bp101wx1 flow 0x27b84 10/220/0
- panel init 0x262e4: before the strap branch 15 + 50 + 100; PE6 low: 200 + 100; PE6 high: 120; after the join: -. Totals 465 / 285 ms. Branch: cmp r5, #1 ; bne.w #0x26b18
- variable-argument delay sites: 0x5a170 in 0x5a114 (__msdelay/mdelay), 0x9928 in 0x98ae (udelay), 0x13586 in 0x134fc (udelay), 0x23208 in 0x230dc (disp_delay_ms), 0x23452 in 0x23378 (disp_delay_ms)
- timeout loops with a constant limit (site, function, limit ms, compare, region, reachable after logo):
  - 0x34c0 in 0x34b8: 199 ms (cmp r0, #0xc7) common post-logo
  - 0xd518 in 0xd494: 3000 ms (cmp r0, r8) common
  - 0xd530 in 0xd494: 800 ms (cmp.w r0, #0x320) common
  - 0xedea in 0xed48: 1000 ms (cmp.w r0, #0x3e8) common post-logo
  - 0x1335c in 0x13348: 2000 ms (cmp.w r0, #0x7d0) common
  - 0x17a02 in 0x179d4: 1000 ms (cmp.w r0, #0x3e8) common post-logo
  - 0x3d66c in 0x3d658: 39999 ms (cmp r0, r8) board
  - 0x3f3b0 in 0x3f1ac: 1000 ms (cmp.w r0, #0x3e8) board
  - 0x5b528 in 0x5b170: 30000 ms (cmp r0, r3) board
  - 0x6ac26 in 0x6ac1c: 5000 ms (cmp r3, r1) board
- timeout loops with a variable limit: 0xa85e in 0xa814, 0x1ae7c in 0x1ae54, 0x1b15e in 0x1b0fc, 0x1b8d4 in 0x1b350, 0x1bb7e in 0x1b350, 0x3f402 in 0x3f1ac, 0x6b152 in 0x6b14c, 0x6baaa in 0x6b964
- add_timer sites: 0x6e66 in 0x6d94: variable; 0xbfc2 in 0xbf8c: variable; 0xc01a in 0xbfe4: variable; 0x1fb00 in 0x1fa98: from LCD open flow table (flow[i].delay); 0x6e9f8 in 0x6e9d0: variable; 0x6ea7a in 0x6ea0c: variable; 0x6eaf6 in 0x6eab0: variable

## rg35xxh

- udelay wrapper (lib/time.c): 0x6a320; sunxi_lcd_delay_ms: 0x2621c (bx *(src_ops+0), pointer filled at run time by 0x2008c; it resolves to disp_delay_ms 0x24dd4)
- panel struct 0xa5700 'fog_fj035fhd05_v1', open flow 0x275e4: 0x27688 then 30 ms, 0x262e4 then 50 ms, 0x26230 then 100 ms, 0x27648 then 0 ms
- other panel structs in the build: default_lcd flow 0x27574 30/50/100/0; bp101wx1 flow 0x275b4 10/220/0
- panel init 0x262e4: before the strap branch 15 + 50 + 100; PE6 low: 200; PE6 high: 120; after the join: 100. Totals 465 / 385 ms. Branch: cmp r4, #1 ; bne.w #0x26b24
- variable-argument delay sites: 0x5aa08 in 0x5a9ac (__msdelay/mdelay), 0x9928 in 0x98ae (udelay), 0x13586 in 0x134fc (udelay), 0x23208 in 0x230dc (disp_delay_ms), 0x23452 in 0x23378 (disp_delay_ms)
- timeout loops with a constant limit (site, function, limit ms, compare, region, reachable after logo):
  - 0x34c0 in 0x34b8: 199 ms (cmp r0, #0xc7) common post-logo
  - 0xd518 in 0xd494: 3000 ms (cmp r0, r8) common
  - 0xd530 in 0xd494: 800 ms (cmp.w r0, #0x320) common
  - 0xedea in 0xed48: 1000 ms (cmp.w r0, #0x3e8) common post-logo
  - 0x1335c in 0x13348: 2000 ms (cmp.w r0, #0x7d0) common
  - 0x17a02 in 0x179d4: 1000 ms (cmp.w r0, #0x3e8) common post-logo
  - 0x3df04 in 0x3def0: 39999 ms (cmp r0, r8) board
  - 0x3fc48 in 0x3fa44: 1000 ms (cmp.w r0, #0x3e8) board
  - 0x5bdc0 in 0x5ba08: 30000 ms (cmp r0, r3) board
  - 0x6b4be in 0x6b4b4: 5000 ms (cmp r3, r1) board
- timeout loops with a variable limit: 0xa85e in 0xa814, 0x1ae7c in 0x1ae54, 0x1b15e in 0x1b0fc, 0x1b8d4 in 0x1b350, 0x1bb7e in 0x1b350, 0x3fc9a in 0x3fa44, 0x6b9ea in 0x6b9e4, 0x6c342 in 0x6c1fc
- add_timer sites: 0x6e66 in 0x6d94: variable; 0xbfc2 in 0xbf8c: variable; 0xc01a in 0xbfe4: variable; 0x1fb00 in 0x1fa98: from LCD open flow table (flow[i].delay); 0x6f290 in 0x6f268: variable; 0x6f312 in 0x6f2a4: variable; 0x6f38e in 0x6f348: variable

## rg35xxplus

- udelay wrapper (lib/time.c): 0x6a318; sunxi_lcd_delay_ms: 0x2621c (bx *(src_ops+0), pointer filled at run time by 0x2008c; it resolves to disp_delay_ms 0x24dd4)
- panel struct 0xa56f8 'fog_fj035fhd05_v1', open flow 0x275d8: 0x2767c then 30 ms, 0x262e4 then 50 ms, 0x26230 then 100 ms, 0x2763c then 0 ms
- other panel structs in the build: default_lcd flow 0x27568 30/50/100/0; bp101wx1 flow 0x275a8 10/220/0
- panel init 0x262e4: before the strap branch 15 + 50 + 100; PE6 low: 200; PE6 high: 120; after the join: 100. Totals 465 / 385 ms. Branch: cmp r4, #1 ; bne.w #0x26b24
- variable-argument delay sites: 0x5aa00 in 0x5a9a4 (__msdelay/mdelay), 0x9928 in 0x98ae (udelay), 0x13586 in 0x134fc (udelay), 0x23208 in 0x230dc (disp_delay_ms), 0x23452 in 0x23378 (disp_delay_ms)
- timeout loops with a constant limit (site, function, limit ms, compare, region, reachable after logo):
  - 0x34c0 in 0x34b8: 199 ms (cmp r0, #0xc7) common post-logo
  - 0xd518 in 0xd494: 3000 ms (cmp r0, r8) common
  - 0xd530 in 0xd494: 800 ms (cmp.w r0, #0x320) common
  - 0xedea in 0xed48: 1000 ms (cmp.w r0, #0x3e8) common post-logo
  - 0x1335c in 0x13348: 2000 ms (cmp.w r0, #0x7d0) common
  - 0x17a02 in 0x179d4: 1000 ms (cmp.w r0, #0x3e8) common post-logo
  - 0x3defc in 0x3dee8: 39999 ms (cmp r0, r8) board
  - 0x3fc40 in 0x3fa3c: 1000 ms (cmp.w r0, #0x3e8) board
  - 0x5bdb8 in 0x5ba00: 30000 ms (cmp r0, r3) board
  - 0x6b4b6 in 0x6b4ac: 5000 ms (cmp r3, r1) board
- timeout loops with a variable limit: 0xa85e in 0xa814, 0x1ae7c in 0x1ae54, 0x1b15e in 0x1b0fc, 0x1b8d4 in 0x1b350, 0x1bb7e in 0x1b350, 0x3fc92 in 0x3fa3c, 0x6b9e2 in 0x6b9dc, 0x6c33a in 0x6c1f4
- add_timer sites: 0x6e66 in 0x6d94: variable; 0xbfc2 in 0xbf8c: variable; 0xc01a in 0xbfe4: variable; 0x1fb00 in 0x1fa98: from LCD open flow table (flow[i].delay); 0x6f288 in 0x6f260: variable; 0x6f30a in 0x6f29c: variable; 0x6f386 in 0x6f340: variable

## rg35xxpro

- udelay wrapper (lib/time.c): 0x6a320; sunxi_lcd_delay_ms: 0x2621c (bx *(src_ops+0), pointer filled at run time by 0x2008c; it resolves to disp_delay_ms 0x24dd4)
- panel struct 0xa5700 'fog_fj035fhd05_v1', open flow 0x275e4: 0x27688 then 30 ms, 0x262e4 then 50 ms, 0x26230 then 100 ms, 0x27648 then 0 ms
- other panel structs in the build: default_lcd flow 0x27574 30/50/100/0; bp101wx1 flow 0x275b4 10/220/0
- panel init 0x262e4: before the strap branch 15 + 50 + 100; PE6 low: 200; PE6 high: 120; after the join: 100. Totals 465 / 385 ms. Branch: cmp r4, #1 ; bne.w #0x26b24
- variable-argument delay sites: 0x5aa08 in 0x5a9ac (__msdelay/mdelay), 0x9928 in 0x98ae (udelay), 0x13586 in 0x134fc (udelay), 0x23208 in 0x230dc (disp_delay_ms), 0x23452 in 0x23378 (disp_delay_ms)
- timeout loops with a constant limit (site, function, limit ms, compare, region, reachable after logo):
  - 0x34c0 in 0x34b8: 199 ms (cmp r0, #0xc7) common post-logo
  - 0xd518 in 0xd494: 3000 ms (cmp r0, r8) common
  - 0xd530 in 0xd494: 800 ms (cmp.w r0, #0x320) common
  - 0xedea in 0xed48: 1000 ms (cmp.w r0, #0x3e8) common post-logo
  - 0x1335c in 0x13348: 2000 ms (cmp.w r0, #0x7d0) common
  - 0x17a02 in 0x179d4: 1000 ms (cmp.w r0, #0x3e8) common post-logo
  - 0x3df04 in 0x3def0: 39999 ms (cmp r0, r8) board
  - 0x3fc48 in 0x3fa44: 1000 ms (cmp.w r0, #0x3e8) board
  - 0x5bdc0 in 0x5ba08: 30000 ms (cmp r0, r3) board
  - 0x6b4be in 0x6b4b4: 5000 ms (cmp r3, r1) board
- timeout loops with a variable limit: 0xa85e in 0xa814, 0x1ae7c in 0x1ae54, 0x1b15e in 0x1b0fc, 0x1b8d4 in 0x1b350, 0x1bb7e in 0x1b350, 0x3fc9a in 0x3fa44, 0x6b9ea in 0x6b9e4, 0x6c342 in 0x6c1fc
- add_timer sites: 0x6e66 in 0x6d94: variable; 0xbfc2 in 0xbf8c: variable; 0xc01a in 0xbfe4: variable; 0x1fb00 in 0x1fa98: from LCD open flow table (flow[i].delay); 0x6f290 in 0x6f268: variable; 0x6f312 in 0x6f2a4: variable; 0x6f38e in 0x6f348: variable

## rg35xxsp

- udelay wrapper (lib/time.c): 0x69c98; sunxi_lcd_delay_ms: 0x2621c (bx *(src_ops+0), pointer filled at run time by 0x2008c; it resolves to disp_delay_ms 0x24dd4)
- panel struct 0xa42f0 'fog_fj035fhd05_v1', open flow 0x27dc4: 0x27e68 then 30 ms, 0x262e4 then 50 ms, 0x26230 then 100 ms, 0x27e28 then 0 ms
- other panel structs in the build: default_lcd flow 0x27d54 30/50/100/0; bp101wx1 flow 0x27d94 10/220/0
- panel init 0x262e4: before the strap branch 15 + 50 + 100; PE6 low: 120; PE6 high: 120 + 10; after the join: -. Totals 285 / 295 ms. Branch: cmp r4, #1 ; bne.w #0x27308
- variable-argument delay sites: 0x5a380 in 0x5a324 (__msdelay/mdelay), 0x9928 in 0x98ae (udelay), 0x13586 in 0x134fc (udelay), 0x23208 in 0x230dc (disp_delay_ms), 0x23452 in 0x23378 (disp_delay_ms)
- timeout loops with a constant limit (site, function, limit ms, compare, region, reachable after logo):
  - 0x34c0 in 0x34b8: 199 ms (cmp r0, #0xc7) common post-logo
  - 0xd518 in 0xd494: 3000 ms (cmp r0, r8) common
  - 0xd530 in 0xd494: 800 ms (cmp.w r0, #0x320) common
  - 0xedea in 0xed48: 1000 ms (cmp.w r0, #0x3e8) common post-logo
  - 0x1335c in 0x13348: 2000 ms (cmp.w r0, #0x7d0) common
  - 0x17a02 in 0x179d4: 1000 ms (cmp.w r0, #0x3e8) common post-logo
  - 0x3d87c in 0x3d868: 39999 ms (cmp r0, r8) board
  - 0x3f5c0 in 0x3f3bc: 1000 ms (cmp.w r0, #0x3e8) board
  - 0x5b738 in 0x5b380: 30000 ms (cmp r0, r3) board
  - 0x6ae36 in 0x6ae2c: 5000 ms (cmp r3, r1) board
- timeout loops with a variable limit: 0xa85e in 0xa814, 0x1ae7c in 0x1ae54, 0x1b15e in 0x1b0fc, 0x1b8d4 in 0x1b350, 0x1bb7e in 0x1b350, 0x3f612 in 0x3f3bc, 0x6b362 in 0x6b35c, 0x6bcba in 0x6bb74
- add_timer sites: 0x6e66 in 0x6d94: variable; 0xbfc2 in 0xbf8c: variable; 0xc01a in 0xbfe4: variable; 0x1fb00 in 0x1fa98: from LCD open flow table (flow[i].delay); 0x6ec08 in 0x6ebe0: variable; 0x6ec8a in 0x6ec1c: variable; 0x6ed06 in 0x6ecc0: variable

## rg40xxh

- udelay wrapper (lib/time.c): 0x6aa78; sunxi_lcd_delay_ms: 0x2621c (bx *(src_ops+0), pointer filled at run time by 0x2008c; it resolves to disp_delay_ms 0x24dd4)
- panel struct 0xa5e40 'fog_fj035fhd05_v1', open flow 0x27d3c: 0x27de0 then 30 ms, 0x262e4 then 50 ms, 0x26230 then 100 ms, 0x27da0 then 0 ms
- other panel structs in the build: default_lcd flow 0x27ccc 30/50/100/0; bp101wx1 flow 0x27d0c 10/220/0
- panel init 0x262e4: before the strap branch 15 + 50 + 100; PE6 low: 120; PE6 high: 200; after the join: 20. Totals 305 / 385 ms. Branch: cmp r4, #1 ; bne.w #0x272fc
- variable-argument delay sites: 0x5b160 in 0x5b104 (__msdelay/mdelay), 0x9928 in 0x98ae (udelay), 0x13586 in 0x134fc (udelay), 0x23208 in 0x230dc (disp_delay_ms), 0x23452 in 0x23378 (disp_delay_ms)
- timeout loops with a constant limit (site, function, limit ms, compare, region, reachable after logo):
  - 0x34c0 in 0x34b8: 199 ms (cmp r0, #0xc7) common post-logo
  - 0xd518 in 0xd494: 3000 ms (cmp r0, r8) common
  - 0xd530 in 0xd494: 800 ms (cmp.w r0, #0x320) common
  - 0xedea in 0xed48: 1000 ms (cmp.w r0, #0x3e8) common post-logo
  - 0x1335c in 0x13348: 2000 ms (cmp.w r0, #0x7d0) common
  - 0x17a02 in 0x179d4: 1000 ms (cmp.w r0, #0x3e8) common post-logo
  - 0x3e65c in 0x3e648: 39999 ms (cmp r0, r8) board
  - 0x403a0 in 0x4019c: 1000 ms (cmp.w r0, #0x3e8) board
  - 0x5c518 in 0x5c160: 30000 ms (cmp r0, r3) board
  - 0x6bc16 in 0x6bc0c: 5000 ms (cmp r3, r1) board
- timeout loops with a variable limit: 0xa85e in 0xa814, 0x1ae7c in 0x1ae54, 0x1b15e in 0x1b0fc, 0x1b8d4 in 0x1b350, 0x1bb7e in 0x1b350, 0x403f2 in 0x4019c, 0x6c142 in 0x6c13c, 0x6ca9a in 0x6c954
- add_timer sites: 0x6e66 in 0x6d94: variable; 0xbfc2 in 0xbf8c: variable; 0xc01a in 0xbfe4: variable; 0x1fb00 in 0x1fa98: from LCD open flow table (flow[i].delay); 0x6f9e8 in 0x6f9c0: variable; 0x6fa6a in 0x6f9fc: variable; 0x6fae6 in 0x6faa0: variable

## rg40xxv

- udelay wrapper (lib/time.c): 0x6aa78; sunxi_lcd_delay_ms: 0x2621c (bx *(src_ops+0), pointer filled at run time by 0x2008c; it resolves to disp_delay_ms 0x24dd4)
- panel struct 0xa5e40 'fog_fj035fhd05_v1', open flow 0x27d3c: 0x27de0 then 30 ms, 0x262e4 then 50 ms, 0x26230 then 100 ms, 0x27da0 then 0 ms
- other panel structs in the build: default_lcd flow 0x27ccc 30/50/100/0; bp101wx1 flow 0x27d0c 10/220/0
- panel init 0x262e4: before the strap branch 15 + 50 + 100; PE6 low: 120; PE6 high: 200; after the join: 20. Totals 305 / 385 ms. Branch: cmp r4, #1 ; bne.w #0x272fc
- variable-argument delay sites: 0x5b160 in 0x5b104 (__msdelay/mdelay), 0x9928 in 0x98ae (udelay), 0x13586 in 0x134fc (udelay), 0x23208 in 0x230dc (disp_delay_ms), 0x23452 in 0x23378 (disp_delay_ms)
- timeout loops with a constant limit (site, function, limit ms, compare, region, reachable after logo):
  - 0x34c0 in 0x34b8: 199 ms (cmp r0, #0xc7) common post-logo
  - 0xd518 in 0xd494: 3000 ms (cmp r0, r8) common
  - 0xd530 in 0xd494: 800 ms (cmp.w r0, #0x320) common
  - 0xedea in 0xed48: 1000 ms (cmp.w r0, #0x3e8) common post-logo
  - 0x1335c in 0x13348: 2000 ms (cmp.w r0, #0x7d0) common
  - 0x17a02 in 0x179d4: 1000 ms (cmp.w r0, #0x3e8) common post-logo
  - 0x3e65c in 0x3e648: 39999 ms (cmp r0, r8) board
  - 0x403a0 in 0x4019c: 1000 ms (cmp.w r0, #0x3e8) board
  - 0x5c518 in 0x5c160: 30000 ms (cmp r0, r3) board
  - 0x6bc16 in 0x6bc0c: 5000 ms (cmp r3, r1) board
- timeout loops with a variable limit: 0xa85e in 0xa814, 0x1ae7c in 0x1ae54, 0x1b15e in 0x1b0fc, 0x1b8d4 in 0x1b350, 0x1bb7e in 0x1b350, 0x403f2 in 0x4019c, 0x6c142 in 0x6c13c, 0x6ca9a in 0x6c954
- add_timer sites: 0x6e66 in 0x6d94: variable; 0xbfc2 in 0xbf8c: variable; 0xc01a in 0xbfe4: variable; 0x1fb00 in 0x1fa98: from LCD open flow table (flow[i].delay); 0x6f9e8 in 0x6f9c0: variable; 0x6fa6a in 0x6f9fc: variable; 0x6fae6 in 0x6faa0: variable

## rgcubexx

- udelay wrapper (lib/time.c): 0x6a158; sunxi_lcd_delay_ms: 0x2621c (bx *(src_ops+0), pointer filled at run time by 0x2008c; it resolves to disp_delay_ms 0x24dd4)
- panel struct 0xa54f8 'fog_fj035fhd05_v1', open flow 0x2741c: 0x274c0 then 30 ms, 0x262e4 then 50 ms, 0x26230 then 100 ms, 0x27480 then 0 ms
- other panel structs in the build: default_lcd flow 0x273ac 30/50/100/0; bp101wx1 flow 0x273ec 10/220/0
- panel init 0x262e4: no strap read; delays in order: 15 + 50 + 100 + 200 + 100 = 465 ms
- variable-argument delay sites: 0x5a840 in 0x5a7e4 (__msdelay/mdelay), 0x9928 in 0x98ae (udelay), 0x13586 in 0x134fc (udelay), 0x23208 in 0x230dc (disp_delay_ms), 0x23452 in 0x23378 (disp_delay_ms)
- timeout loops with a constant limit (site, function, limit ms, compare, region, reachable after logo):
  - 0x34c0 in 0x34b8: 199 ms (cmp r0, #0xc7) common post-logo
  - 0xd518 in 0xd494: 3000 ms (cmp r0, r8) common
  - 0xd530 in 0xd494: 800 ms (cmp.w r0, #0x320) common
  - 0xedea in 0xed48: 1000 ms (cmp.w r0, #0x3e8) common post-logo
  - 0x1335c in 0x13348: 2000 ms (cmp.w r0, #0x7d0) common
  - 0x17a02 in 0x179d4: 1000 ms (cmp.w r0, #0x3e8) common post-logo
  - 0x3dd3c in 0x3dd28: 39999 ms (cmp r0, r8) board
  - 0x3fa80 in 0x3f87c: 1000 ms (cmp.w r0, #0x3e8) board
  - 0x5bbf8 in 0x5b840: 30000 ms (cmp r0, r3) board
  - 0x6b2f6 in 0x6b2ec: 5000 ms (cmp r3, r1) board
- timeout loops with a variable limit: 0xa85e in 0xa814, 0x1ae7c in 0x1ae54, 0x1b15e in 0x1b0fc, 0x1b8d4 in 0x1b350, 0x1bb7e in 0x1b350, 0x3fad2 in 0x3f87c, 0x6b822 in 0x6b81c, 0x6c17a in 0x6c034
- add_timer sites: 0x6e66 in 0x6d94: variable; 0xbfc2 in 0xbf8c: variable; 0xc01a in 0xbfe4: variable; 0x1fb00 in 0x1fa98: from LCD open flow table (flow[i].delay); 0x6f0c8 in 0x6f0a0: variable; 0x6f14a in 0x6f0dc: variable; 0x6f1c6 in 0x6f180: variable

## rgsp

- udelay wrapper (lib/time.c): 0x69a88; sunxi_lcd_delay_ms: 0x2621c (bx *(src_ops+0), pointer filled at run time by 0x2008c; it resolves to disp_delay_ms 0x24dd4)
- panel struct 0xa40e8 'rg34xxsp_v1', open flow 0x27bb4: 0x27c58 then 30 ms, 0x262e4 then 50 ms, 0x26230 then 100 ms, 0x27c18 then 0 ms
- other panel structs in the build: default_lcd flow 0x27b44 30/50/100/0; bp101wx1 flow 0x27b84 10/220/0
- panel init 0x262e4: before the strap branch 15 + 50 + 100; PE6 low: 200 + 100; PE6 high: 120; after the join: -. Totals 465 / 285 ms. Branch: cmp r5, #1 ; bne.w #0x26b18
- variable-argument delay sites: 0x5a170 in 0x5a114 (__msdelay/mdelay), 0x9928 in 0x98ae (udelay), 0x13586 in 0x134fc (udelay), 0x23208 in 0x230dc (disp_delay_ms), 0x23452 in 0x23378 (disp_delay_ms)
- timeout loops with a constant limit (site, function, limit ms, compare, region, reachable after logo):
  - 0x34c0 in 0x34b8: 199 ms (cmp r0, #0xc7) common post-logo
  - 0xd518 in 0xd494: 3000 ms (cmp r0, r8) common
  - 0xd530 in 0xd494: 800 ms (cmp.w r0, #0x320) common
  - 0xedea in 0xed48: 1000 ms (cmp.w r0, #0x3e8) common post-logo
  - 0x1335c in 0x13348: 2000 ms (cmp.w r0, #0x7d0) common
  - 0x17a02 in 0x179d4: 1000 ms (cmp.w r0, #0x3e8) common post-logo
  - 0x3d66c in 0x3d658: 39999 ms (cmp r0, r8) board
  - 0x3f3b0 in 0x3f1ac: 1000 ms (cmp.w r0, #0x3e8) board
  - 0x5b528 in 0x5b170: 30000 ms (cmp r0, r3) board
  - 0x6ac26 in 0x6ac1c: 5000 ms (cmp r3, r1) board
- timeout loops with a variable limit: 0xa85e in 0xa814, 0x1ae7c in 0x1ae54, 0x1b15e in 0x1b0fc, 0x1b8d4 in 0x1b350, 0x1bb7e in 0x1b350, 0x3f402 in 0x3f1ac, 0x6b152 in 0x6b14c, 0x6baaa in 0x6b964
- add_timer sites: 0x6e66 in 0x6d94: variable; 0xbfc2 in 0xbf8c: variable; 0xc01a in 0xbfe4: variable; 0x1fb00 in 0x1fa98: from LCD open flow table (flow[i].delay); 0x6e9f8 in 0x6e9d0: variable; 0x6ea7a in 0x6ea0c: variable; 0x6eaf6 in 0x6eab0: variable
