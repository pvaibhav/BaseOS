# Vendor U-Boot under emulation: where the time goes from the logo to the kernel jump

October 2026. `experiments/h700-boot-speed/uboot-trace.py` runs each target's vendor
U-Boot 2018.05 (the derived BaseOS package: gzip kernel, `debug_mode` 0) under Unicorn
with a virtual clock, from the card init through the boot logo to the kernel jump.
Static analysis and emulation only; no device was used. Evidence:
[`uboot-trace-all.json`](uboot-trace-all.json) (panel strap PE6 high, the RG SP's
`lcd_type=old`) and [`uboot-trace-pe6-low.json`](uboot-trace-pe6-low.json) (PE6 low).
Both were made with

    python3 experiments/h700-boot-speed/uboot-trace.py --json experiments/h700-boot-speed/evidence/uboot-trace-all.json
    python3 experiments/h700-boot-speed/uboot-trace.py --pe6 low --json experiments/h700-boot-speed/evidence/uboot-trace-pe6-low.json

About 75 s per target (gunzip of the kernel dominates the emulation time).

## Result

The trace reaches the kernel jump (`boot_jump_linux`, entry 0x40080000, FDT 0x44000000)
on all eleven targets with both strap values. No spinning register had to be
auto-resolved, no secure monitor call was reached, and no timer is left pending at the
jump.

Phases (virtual ms): `init` sunxi_flash_init 12.5 ms on every target; `pre` (pre-logo
display driver init, bootreason) 24.5 ms; then `late` (board_late_init: logo and the
post-logo steps) and `main` (main_loop to the kernel jump) below. The virtual clock
counts delays, timer waits, card transfers at 20 MB/s and CPU time at one instruction
per cycle at 1.008 GHz.

### (a) Per target, PE6 high

| target | done | late ms | main ms | delay ms late (irq) | delay ms main | timers (ms armed: ms ran) | card bytes late / main | read cmds late / main | instructions late / main (M) | lcd_type |
|---|---|---|---|---|---|---|---|---|---|---|
| rg28xx | yes | 351.4 | 655.5 | 0.0 (295.0) | 0 | 30: 295.7, 50: 0.0, 100: 1.8 | 1122816 / 7168000 | 281 / 14 | 19.0 / 301.2 | old |
| rg34xx | yes | 521.9 | 655.6 | 0.0 (465.0) | 0 | 30: 465.8, 50: 0.0, 100: 1.8 | 1238016 / 7170048 | 281 / 14 | 19.5 / 301.3 | old |
| rg34xxsp | yes | 342.9 | 662.1 | 0.0 (285.0) | 0 | 30: 286.7, 50: 0.0, 100: 1.8 | 1238016 / 7245824 | 281 / 14 | 19.6 / 304.0 | old |
| rg35xxh | yes | 351.8 | 655.9 | 0.0 (295.0) | 0 | 30: 296.0, 50: 0.0, 100: 1.8 | 1122816 / 7176192 | 281 / 14 | 19.1 / 301.3 | old |
| rg35xxplus | yes | 351.8 | 675.6 | 0.0 (295.0) | 0 | 30: 296.0, 50: 0.0, 100: 1.8 | 1122816 / 7397376 | 281 / 14 | 19.1 / 309.9 | old |
| rg35xxpro | yes | 351.8 | 656.0 | 0.0 (295.0) | 0 | 30: 296.0, 50: 0.0, 100: 1.8 | 1122816 / 7176192 | 281 / 14 | 19.1 / 301.3 | old |
| rg35xxsp | yes | 351.7 | 655.8 | 0.0 (295.0) | 0 | 30: 296.0, 50: 0.0, 100: 1.8 | 1122816 / 7172096 | 281 / 14 | 19.1 / 301.4 | old |
| rg40xxh | yes | 521.7 | 662.2 | 0.0 (465.0) | 0 | 30: 466.0, 50: 0.0, 100: 1.8 | 1122816 / 7245824 | 281 / 14 | 19.1 / 304.0 | old |
| rg40xxv | yes | 521.7 | 662.2 | 0.0 (465.0) | 0 | 30: 466.0, 50: 0.0, 100: 1.8 | 1122816 / 7245824 | 281 / 14 | 19.1 / 304.0 | old |
| rgcubexx | yes | 524.1 | 655.9 | 0.0 (465.0) | 0 | 30: 465.9, 50: 0.0, 100: 1.8 | 1756160 / 7176192 | 281 / 14 | 21.6 / 301.3 | boe |
| rgsp | yes | 342.6 | 662.4 | 0.0 (285.0) | 0 | 30: 286.7, 50: 0.0, 100: 1.8 | 1122816 / 7245824 | 281 / 14 | 19.3 / 304.3 | old |

### The same with PE6 low

| target | done | late ms | main ms | delay ms late (irq) | delay ms main | timers (ms armed: ms ran) | card bytes late / main | read cmds late / main | instructions late / main (M) | lcd_type |
|---|---|---|---|---|---|---|---|---|---|---|
| rg28xx | yes | 351.4 | 655.5 | 0.0 (295.0) | 0 | 30: 295.7, 50: 0.0, 100: 1.8 | 1122816 / 7168000 | 281 / 14 | 19.0 / 301.2 | old |
| rg34xx | yes | 522.0 | 655.6 | 0.0 (465.0) | 0 | 30: 465.8, 50: 0.0, 100: 1.8 | 1238016 / 7170048 | 281 / 14 | 19.5 / 301.3 | boe |
| rg34xxsp | yes | 522.0 | 662.1 | 0.0 (465.0) | 0 | 30: 465.8, 50: 0.0, 100: 1.8 | 1238016 / 7245824 | 281 / 14 | 19.5 / 304.0 | boe |
| rg35xxh | yes | 521.5 | 655.9 | 0.0 (465.0) | 0 | 30: 465.8, 50: 0.0, 100: 1.8 | 1122816 / 7176192 | 281 / 14 | 19.1 / 301.3 | boe |
| rg35xxplus | yes | 521.5 | 675.6 | 0.0 (465.0) | 0 | 30: 465.8, 50: 0.0, 100: 1.8 | 1122816 / 7397376 | 281 / 14 | 19.0 / 309.9 | boe |
| rg35xxpro | yes | 521.5 | 656.0 | 0.0 (465.0) | 0 | 30: 465.8, 50: 0.0, 100: 1.8 | 1122816 / 7176192 | 281 / 14 | 19.1 / 301.3 | boe |
| rg35xxsp | yes | 342.4 | 655.8 | 0.0 (285.0) | 0 | 30: 286.7, 50: 0.0, 100: 1.8 | 1122816 / 7172096 | 281 / 14 | 19.1 / 301.4 | boe |
| rg40xxh | yes | 362.4 | 662.2 | 0.0 (305.0) | 0 | 30: 306.7, 50: 0.0, 100: 1.8 | 1122816 / 7245824 | 281 / 14 | 19.2 / 304.0 | boe |
| rg40xxv | yes | 362.4 | 662.2 | 0.0 (305.0) | 0 | 30: 306.7, 50: 0.0, 100: 1.8 | 1122816 / 7245824 | 281 / 14 | 19.2 / 304.0 | boe |
| rgcubexx | yes | 524.1 | 655.9 | 0.0 (465.0) | 0 | 30: 465.9, 50: 0.0, 100: 1.8 | 1756160 / 7176192 | 281 / 14 | 21.6 / 301.3 | boe |
| rgsp | yes | 521.7 | 662.4 | 0.0 (465.0) | 0 | 30: 465.8, 50: 0.0, 100: 1.8 | 1122816 / 7245824 | 281 / 14 | 19.2 / 304.3 | boe |

Columns: `delay ms late (irq)` is the delay time in board_late_init itself and, in
brackets, in timer callbacks that interrupt it (the panel init). `timers` lists the
three LCD-open steps (armed delay: time the callback ran). Instructions are estimates
(bytes of code executed / 3).

### Where the time goes (RG SP, PE6 high; the other targets have the same shape)

Virtual ms from the start of `init`:

| ms | what |
|---:|---|
| 0 - 37 | card init (8 ms of mmc delays), display driver init (pre-logo) |
| 37.1 | board_bootlogo_display: boot_gui_init 6.1 ms (display switch, LCD open step 0, arms a 30 ms timer) |
| 43.8 - 363 | sunxi_bmp_display 319 ms: GPT lookups and `fatload` of bootlogo.bmp (921600 bytes, 36.5 ms of card time), **interrupted at 72 ms by LCD open step 1 (panel init over bit-banged SPI) which runs 286.7 ms in timer-interrupt context**: sunxi_lcd_delay_ms 15 + 50 + 100 + 120 = 285 ms. The FAT load cannot finish until the callback returns |
| 363 - 380 | the post-logo steps of board_late_init, 17 ms together; update_serial_feature 11.6 ms (seven GPT re-reads looking for a `private` partition) is the largest |
| 384 - 747 | `sunxi_flash read 45000000 boot`: 7.2 MB in one CMD18, 363 ms at 20 MB/s |
| 408.7 | LCD open step 2 (TCON on), 50 ms after step 1 ended |
| 508.7 | LCD open step 3 (backlight), "LCD open finish": the logo is visible from here |
| 748 - 1014 | bootm: update_bootargs 0.7 ms, gunzip of the 17.7 MB kernel 265 ms (CPU estimate) |
| 1042 | kernel jump |

So between "logo drawn into the framebuffer" (363 ms) and the kernel jump (1042 ms) the
bootloader spends about 680 ms: 363 ms reading the boot image, about 300 ms of CPU in
bootm (gunzip), 17 ms of vendor steps. The panel-init delays (285 to 465 ms depending on
build and strap) all fall before the logo is drawn, inside the FAT load, because they
run in interrupt context.

Panel path per build (sum of sunxi_lcd_delay_ms in the step-1 callback):

| build | PE6 high | PE6 low |
|---|---|---|
| rgsp, rg34xxsp | old, 285 ms | boe, 465 ms |
| rg35xxh, rg35xxplus, rg35xxpro | old, 295 ms | boe, 465 ms |
| rg35xxsp | old, 295 ms | boe, 285 ms |
| rg40xxh, rg40xxv | old, 465 ms | boe, 305 ms |
| rg34xx | old, 465 ms | boe, 465 ms |
| rg28xx | old, 295 ms (no strap read) | same |
| rgcubexx | boe, 465 ms (no strap read) | same |

Only the RG SP's strap is known (its kernel command line carries `lcd_type=old`, PE6
high). For the others the table gives both possibilities.

Console of note (printf is gated off at console level 0 as on the BaseOS package; the
formats are logged in `printf_formats`): "Get Reserve0 partition number fail" /
"partno erro: can't find partition Reserve0" (no disp_rsl.fex; defaults used, also on a
device), seven "can't find partition private" from update_serial_feature, "rtc domain
status: error, need restore" (RTC registers read 0 here; restore path, no delays),
"no misc partition is found", "Hit any key to stop autoboot" (bootdelay 0, no wait),
"LCD open finish" from the timer callback during the card read. The final bootargs
match the RG SP's recorded command line except for the card layout (BaseOS partitions),
serial, disp_reserve address (model heap), `bootreason=button` (device: `unknow`) and the
missing `androidboot.secure_os_exist=0` (both set by pre-logo code that does not run).

## (b) Models and shortcuts

The full list with what each hides is the `MODELLED` string at the top of
`uboot-trace.py`. In short:

- Harness models (env dict, bump malloc, puts/pr_msg capture, cache/IRQ no-ops,
  kernel-jump stop, hang). Added here: calloc, realloc, memalign on the bump
  allocator (the dlmalloc versions read chunk headers); `_do_env_set` for `setenv`
  (this build's signature is argc, argv, flag; the earlier model read the wrong
  registers).
- `.bss` zeroed. It overlays `.rel.dyn` in the file (0xe6450..0xfdaa0 in the RG SP
  build); without this the sunxi_flash block descriptor had a garbage size, the GPT
  check failed and the logo path printed "Get bootloader and boot-resource partition
  number fail".
- Virtual clock: __udelay, get_timer_masked, get_ticks; CPU time from executed code at
  one instruction per cycle (the earlier per-block integer division counted none).
  Delays are attributed through udelay, mdelay and the display delay helpers to the
  code that asked for them.
- One-shot timers (add_timer/del_timer): callbacks run at the first block after expiry,
  in interrupt context, no nesting; time in them is reported as `<phase>/irq`.
- SD host and card: register model; the card is as large as its GPT (the CSD and CID
  were in the wrong register order before, which made the card 320 MiB and the GPT
  invalid); 20 MB/s, no per-command latency; a status poll waits for the transfer end
  or the next timer expiry, so panel steps run during long reads as on a device.
- Peripheral reads 0, except PE_DAT (PE6 strap, `--pe6`) and the CCU PLL registers
  (written value with LOCK set; without it each PLL lock wait times out after 5 ms,
  25 ms in all).
- No-ops: sunxi_markid_check (SID reads 0 here; on a device it passes) and
  sunxi_update_boot0_check.
- `smc` returns 0 and is logged (none reached).
- Pre-logo entries run in a `pre` phase because the logo path needs them:
  init_clocks + drv_disp_init (0x2a04; without it disp_ioctl answered "display not
  init yet" and the device switch failed) and bootreason + keybox (0x2a8c).
- Not run: the PMIC init and the rest of init_sequence_r. The battery check therefore
  takes its "no battery" exit (no shutdown, no loop; on a healthy device it does a few
  AXP2202 I2C reads and DT lookups, no delays).
- update_bootargs runs for real (no KNOWN_BOOTARGS shortcut was needed).

## (c) RG SP against RG34XX SP

Nothing in the U-Boot path from the logo to the kernel jump differs in a way that
costs time:

- Code: the two U-Boot builds are the same instruction stream over the whole code
  section (0x20..0x74000), not only below 0x26000: every differing word there is an
  address constant. The data differs only in the build date string (which shifts the
  strings by 3 bytes).
- Environment: identical.
- U-Boot device tree: differs only in /soc/gpadc (enabled, sample rate, no key table),
  /soc/gpio_keys (L3/R3, A0/A1, ADC and AMUX enables) and pmu_battery_cap. U-Boot
  reads none of these nodes (no string reference in the binary); they matter to the
  kernel, which gets this tree.
- Card: bootlogo.bmp is 720x480 on the RG34XX SP (1036800 bytes) against 640x480 on
  the RG SP (921600): +115 KB, +5.8 ms at 20 MB/s, inside the panel-init interrupt
  window, so it does not move anything. The boot image is the same size.
- Trace: the same 606 card commands except the bitmap length; the same delays (285 ms
  panel init with PE6 high, 465 ms with PE6 low), the same timers, spans within 0.3 ms;
  late 342.6 / 342.9 ms, main 662.4 / 662.1 ms, kernel jump at 1042.0 / 1042.1 ms.

So the 1.5 s must come from something the emulation does not see. In order of size
of what each could explain:

1. Before the logo, not in this trace: the measured "before Linux" (4.34 s against
   2.77 s) counts from the hardware counter's origin and so includes boot0. boot0's
   code is identical, but the RG34XX SP's header primary DRAM set (LPDDR3) does not
   match its DRAM and boot0 falls back to the LPDDR4 alternate
   (variant-survey/README.md). A failed training pass before the fallback is the
   largest candidate that the survey already shows to differ. Measuring the time to
   the logo on both devices (camera or the boot0 serial log with debug on) would
   separate it from the U-Boot window.
2. Card latency and speed: the window issues about 300 read commands (281 of them
   single-block GPT and FAT reads around the logo) and reads 8.4 MB. Per-command
   latency is not modelled; at 0.5 ms per command it adds 150 ms, and a card at
   10 MB/s instead of 20 adds 420 ms. A different card in the RG34XX SP would show up
   here.
3. The panel strap: if the RG34XX SP reads PE6 low it takes the `boe` path, +180 ms of
   panel delays, but before the logo is drawn.
4. DRAM type and speed after the fallback: the 265 ms gunzip and the 7 MB copy are
   memory bound; the trace counts instructions only.
5. Hardware waits the models remove: PLL lock (5 ms timeout each, about 25 ms if no
   PLL locked), PMIC I2C (bare 255-iteration polls, no delays).

None of these reaches 1.5 s inside the logo-to-jump window by itself except a much
slower card or DRAM; item 1 is the likeliest.
