# RG SP vendor U-Boot 2018.05: routines from the boot logo to the kernel jump

Binary: `work/rgsp/boot-gzip/boot-package.bin`, item at file offset 0x800, linked at
0x4A000000. All offsets are relative to the link address (as in
`tests/test-fast-boot-emulation.py`). Static analysis only. The method: literal-load
string xrefs, BL/BLX edges, and recursive-descent function extents that follow
branches and TBB/TBH tables. The scripts are in /tmp/ubt (an.py, fx.py, emit.py).
Machine-readable results: /tmp/uboot-routines-rgsp.json.

## 1. Clock and delay primitives

| offset | name | what it reads |
|---|---|---|
| 0xd50 | `__udelay(us)` | CNTPCT (`mrrc p15,0,r6,r1,c14` at 0xd54 and 0xd64), busy loop until now+us*24 |
| 0xd74 | `__usdelay` alias | `b.w 0xd50` |
| 0xd78 | `__msdelay` / mdelay | `r0*=1000; b.w 0xd50` |
| 0xd84 | `get_timer_masked` | CNTPCT (0xd86); count/24000 = ms (multiply by 0xaec33e1f671529a5, then shift by 78) |
| 0xde4 | alias | `b.w 0xd84` (console timestamps, IR wait) |
| 0xde8 | `get_timer(base)` | `0xd84() - base` |
| 0xdf4 | `get_ticks` | CNTPCT (0xdf4) -> gd->0xa8/0xa4 |
| 0xe0a | `get_tbclk` | returns 1000 |
| 0x69a88 | `udelay(us)` | lib/time.c, chunks of 10,000,000 us -> 0xd50 |
| 0xeac8 | `mdelay(ms)` | loop of udelay(1000), no BL callers |
| 0x24dd4 / 0x24dde | disp_delay_ms / disp_delay_us | -> 0xd78 / 0xd74; lcd source ops [+0]/[+4] |
| 0x2621c | sunxi_lcd_delay_ms (panel) | `bx *(0x4a0f3d48)` -> 0x24dd4 |
| 0xd34 | timer_init | 0x03009000 IRQ_EN=0, IRQ_STA=3 |
| 0xe14 / 0xed0 | add_timer / del_timer | TMRn at 0x03009010+0x10n: CTRL=0xd6, INTV=ms*750, IRQ 0x50/0x51 |
| 0xcb6 / 0xcf6 | timer0 / timer1 IRQ handlers | clear status, one-shot, call callback from 0x4a09d830[slot] |
| 0x1994 | watchdog reset | 0x030090b4/0x030090b8 |

Only four instructions read the generic counter: 0xd54, 0xd64, 0xd86 and 0xdf4.
Only the routines in the table above touch the sunxi timer block at 0x03009000.

Console: `tick_printf` 0x6a750 and `pr_msg` 0x111e8 call 0xde4 to print a
"[%02u.%03u]" timestamp. They do this only when the message passes the gd->0x10c
debug level (the DT debug_mode; 0 in this package).

## 2. Boot flow

`board_init_r` 0xf2fc runs `initcall_run_list` 0x6488c on `init_sequence_r` at
0x9edfc. That table holds 33 entries, 0x9edfc..0x9ee7c, and ends with 0xffffffff at
0x9ee80. The entries in order:

```
0xf11c 0xf22c 0xf2ec 0xf120 0xf2cc 0xf210 0xee0c 0xf202 0xf2d0 0xf2c8
0x25d8 (power/PMIC: -> 0x72dc power_delay, axp)   0x12a30 0xf1f8 0xf2d8
0x2a04 0xf2d4 0xf2dc 0xf2e2 0x12a44 0xf1ee 0x113d0 (stdio) 0x2738 0x15b4
0xf1e4 (enable_interrupts)  0xf26c (fast burn key)
0xf1bc initr_sunxi_flash: 0x2cfc, 0x6c48, 0x6d94 (IR: add_timer 1 ms), 0x299c,
       0x33a0 (key_boot_recovery), b.w 0x400cc sunxi_flash_init (mmc init)
0xf174 initr_env   0x2a8c   0xf15c ethaddr   0xf24c (burn key -> 0x4f50 -> do_burn_from_boot 0xd494)
0x2b14 board_late_init       <-- the logo is drawn here
0xf144 initr_net
0xf13c run_main_loop -> main_loop 0xea90
```

All USB burn, charger/power_delay and key-recovery code runs before the logo, not
after it. The details are under "Pre-logo" below.

## 3. The logo and the tracer start point

`board_late_init` 0x2b14:
```
2b14 push {r3, lr}
2b16 ldr r3,=0x4a000000 ; ldr.w r3,[r3,#0xe0]   ; U-Boot header work_mode
2b1c cbnz r3, 2b52                               ; non-normal: return 0
2b1e bl 0x2a2c   board_bootlogo_display          ; logo
2b22 bl 0x2aa8   <-- tracer start (return address of the logo call)
...
2b4e bl 0x5054
2b52 movs r0,#0 ; pop {r3, pc}
```
At 0x2b22 the code relies on only these values: sp (with the saved {r3, lr} of
board_late_init above it, lr returning into initcall_run_list) and r9 = gd. No
callee-saved register carries a value across the logo call, and the return value
of 0x2a2c is ignored.

`board_bootlogo_display` 0x2a2c does the following:
- If gd->0x138 != 0 it does nothing. That field holds the boot-package logo pointer,
  set at 0x2744; the RG SP package has only the u-boot, monitor, dtbo and dtb items,
  so it is 0.
- It calls `boot_gui_init` 0x393c0, which runs display init 0x39778 and the fb layer
  setup 0x39438.
- It checks /soc/target advert_enable, which is 0 on the RG SP.
- It calls `sunxi_bmp_display("bootlogo.bmp")` 0x3a644. That routine runs
  "fatload sunxi_flash 0:<boot-resource> 40000000 bootlogo.bmp" through
  do_fat_fsload 0x8392 (card reads), then show_bmp_on_fb 0x3a354.

The panel is still being opened after 0x2a2c returns. `boot_gui_init` only runs
step 0 of the LCD open flow, inside `drv_lcd_open_callback` 0x1fa98. Each later step
is armed with `add_timer(delay)` and runs from the timer0/1 IRQ handler 0xcb6/0xcf6,
because interrupts are already on (0xf1e4). The RG SP panel flow 0x27bb4 is:

| step | function | then waits |
|---|---|---|
| 0 | 0x27c58 power on (lcd op +0x4c, +0x58) | 30 ms |
| 1 | 0x262e4 panel init: PE strap read at 0x263c0, port-I bit-bang SPI, sunxi_lcd_delay_ms 15/50/100/(200/100/120) | 50 ms |
| 2 | 0x26230 tcon enable (op +8) | 100 ms |
| 3 | 0x27c18 backlight enable (op +0x44) | 0 |

The step index is at 0x4a0f19e8[sel], the done flag at 0x4a0f19f0[sel], the timer
struct at 0x4a0f19f4. The pending timer slots are at 0x4a09d830 (fn, arg) x2, with
the in-use bitmask at 0x4a0e6450. Nothing on the boot path waits for the done flag:
only disp_ioctl calls 0x1fb34. The panel steps therefore interleave with whatever
code runs after 0x2b22, and `boot_jump_linux` finally disables interrupts in 0xc3e.

## 4. After the logo (board_late_init, then main_loop), in order

| call site | routine | what it does | delays (direct or one level down) |
|---|---|---|---|
| 0x2b22 | 0x2aa8 | boot0 update after OTA ("begin to update boot0 atfer ota", "get_boot_dram_update_flag %d") | none unless a flag is set |
| 0x2b26 | 0x5d88c | fastboot lock state from secure storage ("device_unlock", "fastboot_status_flag", "verifiedbootstate") | none; card reads |
| 0x2b2a | 0x3738 | sunxi_update_fdt_para_for_kernel (mmc DT params, nand/spi disable, drm SMC, "(weak)update dtb dram start" 0x3068) | udelay(20) in the SMC retry path (0x15a34) |
| 0x2b2e | 0x5584 | serial_feature env ("ULI/factory/", "private", fatload) | none on RG SP (no private partition) |
| 0x2b32 | 0x47a8 | env format check | none |
| 0x2b36 | 0x2524 | markid check ("illegal markid") | none |
| 0x2b3a | 0x3850 | root_partition / partitions env | none |
| 0x2b3e | 0x39b0 | rotpk_status env | none |
| 0x2b42 | 0x7b44 | battery and temperature (PMIC over I2C; "waitting for higher temp", "to be shutdown") | 3001 x udelay(1000) only on shutdown paths (0x7c78, 0x7cb6, 0x7cf8) |
| 0x2b46 | 0x34b8 | IR recovery wait plus work_mode misc | **busy loop on get_timer_masked, up to 200 ms while gd->0x130==1**; ends at once once the 1 ms IR timer IRQ has fired |
| 0x2b4a | 0x36c8 | bootcmd setargs_nand->setargs_mmc, plus 0x353c key/bootmode ("%s key high %d, low %d"; key value from header +0x504; "no misc partition is found") | none |
| 0x2b4e | 0x5054 | snum env | none |
| (table) | 0xf144 | initr_net ("No ethernet found.") | none |
| 0xf13e | 0xea90 main_loop | bootstage 0xea8c, cli_init 0x12bcc, bootdelay_process 0xecc4, cli_process_fdt 0x12b4c, autoboot_command 0xed48 | |
| 0xeab6 | 0xed48 autoboot_command | "Hit any key to stop autoboot: %2d "; tstc 0x110e0; gd->0x134 abort flag | bootdelay=0 (env on card): no loop. Otherwise each second runs udelay(10000) (0xede4) until get_timer(start)>=1000 |
| 0xed6a | 0x12aaa run_command_list | -> 0x10e9c -> cli_simple_run_command 0x10d74 -> cmd_process 0x130ec (get_timer around each command) | |
| | `run setargs_mmc boot_normal` | `sunxi_flash read 45000000 boot` (0xc1e8), then `bootm 45000000` (0x7e70) | |
| | do_bootm 0x7e70 | 0x6a38 vboot screen (returns at once when gd->0xc0==0), update_bootargs 0x5734, do_bootm_states 0xf43c | |
| | boot_jump_linux 0x1474 | FDT copied to 0x44000000, "Starting kernel", 0x2bc0, cleanup 0xc7c->0xc3e (irq off, caches) | kernel: `blx r6` at 0x14f8, or SMC through 0xb58 at 0x14e4 when the monitor is present (0x27dc) |

Pre-logo items that were asked about. They are listed with their delays so the
tracer can ignore them:
- `do_burn_from_boot` 0xd494 ("%s usb : have no handshake", "no usb exist", "usb init
  fail"). It waits up to 800 ms for SOF and 3000 ms for a handshake, using
  get_timer. It is gated on /soc/target burn_key==1, which is 0 on the RG SP. It
  then calls SUNXI_UPDATE_NEXT_ACTION 0x2c30.
- `0x72dc` (from 0x25d8): /soc/power_delay, "%s need to wait stable!", with a
  udelay(1000) loop at 0x7540.
- `key_boot_recovery` 0x33a0: udelay(1000) loops at 0x3400 and 0x3456 while the key
  is held (key_press_time 2000).
- `ir_recovery_setup` 0x6d94: add_timer(1 ms), callback 0x6c58 sets gd->0x130=3.
- `usb_update_main_loop` 0x5c040: "Device will shutdown in 3 Secends..." then
  mdelay(3000). It has no direct callers.
- The strings "sunxi_usb_dev_register", "sunxi_usb_main_loop", "burn key end" and
  "twi@0x07081400" are not in this binary as plain strings. The TWI path exists only
  in the DT alias.

## 5. Card read path

```
do_sunxi_flash 0xc1e8
 -> blk desc 'sunxi_flash' (0x4a0f7410) ->block_read = 0x3fe04
    -> storage type 1/2 or work_mode 0x11: 0x3fdf8 -> ops[+0x2c] = 0x403a0 phyread
       otherwise:                          0x3fdbc -> ops[+0x14] = 0x402fc read (+0xa000)
       (ops at *0x4a0e428c = 0x4a0e4294)
    -> mmc->block_dev.block_read [mmc+0x15c] = mmc_bread 0x19880
       -> mmc_set_blocklen 0x17b54 (CMD16), mmc_read_blocks 0x17864 (CMD17/18 + CMD12)
       -> mmc_send_cmd 0x1785c -> cfg->ops->send_cmd (ops table 0x75c54)
          -> sunxi_mmc_send_cmd 0x1bbf0 (retry wrapper)
             -> sunxi_mmc_do_send_cmd_common 0x1b350
                -> mmc_rint_wait 0x1ae54, mmc_update_clk 0x1b0fc
```
MMC ops table at 0x75c54: send_cmd 0x1bbf0, set_ios 0x1b180, init 0x1ac78, getcd
0x1ae40, decide_retry 0x1afbc, and 0x1ae44. Host init is 0x1bd34 and mmc_init is
0x19688; both run pre-logo from sunxi_flash_init 0x400cc.

SMHC registers (base at [host+0x3c], 0x04020000 for mmc0) and the polls on them:
- GCTRL +0x00: write 7, then poll until (v & 7) == 0 (0x1b4ca, 0x1b684). Bit 31 is
  set for FIFO access through AHB.
- CLKCR +0x04: bit 31 is set and cleared around the clock update.
- BYTCNT +0x14, CMD +0x18, CMDARG +0x1c.
- mmc_update_clk polls CMD +0x18 bit 31 (0x1b12a), timeout 10000 ms.
- RINTSTS +0x38: mmc_rint_wait 0x1ae70 polls for the done mask or error mask 0xbfc2
  until get_timer > timeout (data timeout 6000 ms). Write -1 to clear.
- STATUS +0x3c: the CPU FIFO loop at 0x1b8e2 tests bit 3 (empty, read) or bit 2
  (full, write); the R1b busy loop at 0x1bb72 tests bit 9.
- +0x4c: the "Read remain data" loop at 0x1b48c runs while the value is below 0x200,
  with udelay_alias(1), up to 1001 times.
- IDMAC +0x80 bit 0 reset poll (0x1ba94); IDST +0x88 & 3 is checked in rint_wait.
- FIFO +0x200.

## 6. I2C (R_TWI 0x07081400) and the PMIC

- The adapter struct is at 0xe6350: init 0x3d2e4, probe 0x3ced0, read 0x3d140, write
  0x3d0a8, recover 0x3ced4, speed 400000, slave 0x34, hwadapnr 6.
- 0x3d2e4 computes the base as (6+0x1c1ff)<<10 = 0x07081400 and stores it in
  0x4a0e4104[6]. It handles gating/reset at 0x0701019c with mdelay(1), then runs the
  9-pulse recovery (udelay 500/1000).
- Transfer helpers: twi_start 0x3d058, send_addr 0x3ce20, send_data 0x3ce6c,
  twi_stop 0x3cfd8, twi_restart 0x3d1da.
- Registers: DATA +0x08, CNTR +0x0c (bit 3 INT_FLAG, bit 5 START, bit 4 STOP), STAT
  +0x10 (0x08/0x10/0x18/0x28/0x40/0x50/0x58/0xf8), +0x18 and +0x1c.
- Every poll is a bare 255-iteration counter on CNTR bit 3, with no timer. A model
  that never raises INT_FLAG gets errors, not time.
- Generic API: i2c_read 0x3cd5c, i2c_write 0x3cd8e, i2c_get_adapter 0x3cc00,
  i2c_set_bus_num 0x3ccac.
- PMIC wrappers: pmic_bus_read 0x1f76 (i2c_read(chip,reg,1,buf,1)) and
  pmic_bus_write 0x1f8a. AXP2202 read-modify-write helpers are 0x3b85a, 0x3b8e8 and
  0x3b920; the PMU/BMU dispatch stubs are 0x3b5d0..0x3b7ec (e.g. 0x3b7b8 battery
  exist, 0x3b750 temperature).

## 7. PIO data registers

Readers of the PE data register 0x0300B0A0 other than the strap read at 0x263c0:
- `gpio_port_data_read` 0x3a38, the vendor gpio library: data reg =
  0x0300afec + bank*0x24, with banks numbered from 1, so PE gives 0x0300b0a0. Its
  only caller is `gpio_read_one_pin_value` 0x3abc, used only by key_boot_recovery
  0x33a0 (pre-logo). The RG SP recovery key is not on port E.
- Read-modify-write of data regs at 0x0300b010+bank*0x24: gpio_write_one_pin_value
  0x3b1c, gpio_set 0x3ba0 and gpio_request_set 0x3e64. Their callers are mmc host
  init, disp gpio helpers 0x209d8/0x209fc/0x20a38, boot_init_gpio 0x299c and
  0x6ea0c. They read PE data only if a configured pin is on port E.
- The panel driver bit-bangs port I (cfg 0x0300b124, data 0x0300b130) in
  0x27e3c/0x27fcc, called from 0x262e4 in IRQ context.
- 0x445c8 (nand dump) reads only PC cfg registers. 0x72dc and 0x1d934 touch
  0x0300b340/0x0300b348 (power mode select).
