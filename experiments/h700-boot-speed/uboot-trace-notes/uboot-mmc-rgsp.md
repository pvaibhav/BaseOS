# RG SP vendor U-Boot: SD host driver (SMHC0), specification for a register model

Binary: `work/rgsp/boot-gzip/boot-package.bin`, U-Boot item at file 0x800, linked at
0x4A000000. "Offset X" means address 0x4A000000 + X. Everything is Thumb-2. Found by
static reading only (capstone; string literals found through `ldr rX,[pc,#n]`; BL targets
decoded). Routine offsets are also in `/tmp/uboot-mmc-rgsp.json`.

Conventions in this file:
- `host` = `struct sunxi_mmc_host` for SDC0 = 0x4A0EDBC4 (array stride 0xCC4).
- `mmc` = `struct mmc` (calloc'd, 0x1DC bytes); `*(0x4A0F8500)` points at the boot card's one.
- `cfg` = `struct mmc_config` embedded at host+0xBD0.
- `R` = SMHC0 base 0x04020000 (`host+0x3C`). CCU 0x03001000. PIO 0x0300B000.

## 0. Answers in short

- The read path per `sunxi_flash read` block read is: `0x3FE04` -> `0x403A0` -> `mmc_bread
  0x19880` -> CMD16 (`0x17B54`) + CMD17/18 (`0x17864`) -> `0x1BBF0` -> `0x1B350`. No
  clock, delay or CCU/PIO access happens on that path when nothing fails.
- The kernel read is 2 calls of `block_read` in `do_sunxi_flash` (0x40 blocks, then the rest),
  so 2 x (CMD16 + CMD18). A single CMD18 can carry up to 0xFFFF blocks (cfg b_max), as one
  chained IDMA list of 4 KiB descriptors.
- "Current spd_md ... sdly" (`0x1AEF8`) is NOT the clock routine. It is the eMMC tuning
  retry helper for host type tm4 (SMHC2). On this board SDC0 uses host type **tm1**. The
  clock routine for tm1 is `0x1F094`; it prints nothing.
- U-Boot does not read the `sdmmc@04020000` node at all (no `max-frequency`, `bus-width`,
  `cap-sd-highspeed`, `ctl-spec-caps` string exists in the binary except the kernel-DT
  edit code for eMMC). It reads `/soc/card0_boot_para` (card_line=4, pinctrl-0/1, and
  optional sdc_* properties, none of which are present). f_max = 50 MHz is a constant of
  host type tm1 (`0x1F832`).
- The sample/output delay comes from a constant default table written by `0x1E950`
  (tm1), because `sdc_ex_dly_used` is absent from card0_boot_para. The boot0 tuning
  record in the U-Boot header (0x3E8..0x427, "EXT_PARA0", magic 0x55xxxx01 at 0x424) is
  only read when `sdc_ex_dly_used == 2`. The driver's retry/re-tune and "reinit" logic is
  also gated on `sdc_ex_dly_used == 2`, so on this board a failed read is retried once by
  `mmc_bread` with no re-tuning, then fails.
- Bus speed for an SD card is chosen by the generic `mmc_startup` (`0x18604`): SD_HS
  (50 MHz, spd_md 1, freq_id 2) if the card's CMD6 says high speed is supported and the
  CMD6 switch and ACMD13 work, else SD_LEGACY (25 MHz, spd_md 0, freq_id 1). UHS modes are
  masked out (`bic 0x7C0` at 0x18898). So with the same DT, the per-board difference must
  come from the card (HS support, read latency/throughput, errors) or from what boot0
  left in the CCU (PLL_PERIPH0 at CCU+0x020, which the clock code reads), not from a DT or
  tuning table. A tracer should log: CMD6 responses and the mode chosen at init, the CCU
  0x830 value written, the CMD18 count and block counts, and the per-command poll counts.

## 1. Routines

| Offset | Role | Called |
|---|---|---|
| 0xC1E8 | do_sunxi_flash: `blk_get_dev("sunxi_flash")`, `part_get_info`, `desc->block_read(desc,start,0x40,buf)`, then the rest | per command |
| 0x3FE04 | sunxi_flash blk `block_read(desc,start,cnt,buf)`. Reads work_mode `[0x4A0000E0]` and storage_type `[0x4A0000E4]`; if work_mode==0x11 or storage_type in {1,2} it calls flash ops +0x2C, else +0x14 | per read |
| 0x403A0 | sunxi_flash_mmc_phyread(start,cnt,buf): `mmc=*(0x4A0F8500)`; `mmc->block_dev.block_read(&mmc->block_dev,start,cnt,buf)` (mmc+0xE4, fn at mmc+0x15C) | per read |
| 0x19880 | mmc_bread(bdev,start,cnt,dst) | per read |
| 0x17B54 | mmc_set_blocklen: CMD16 arg=read_bl_len, R1. Skipped if timing 2 or 4 | per mmc_bread |
| 0x17864 | mmc_read_blocks: CMD18 (cnt>1) / CMD17, arg = start if high_capacity (mmc+0x18) else start*bl_len; then CMD12 R1b with mmc+0x1C4=0 | per chunk |
| 0x1BBF0 | ops.send_cmd: calls 0x1B350; retry/re-tune only when cfg sdc_ex_dly_used==2 (section 9) | per command |
| 0x1B350 | do_send_cmd_common(host,mmc,cmd,data): the command and data routine (DMA setup, PIO, waits, response, error recovery) | per command |
| 0x1AE54 | wait helper: polls RINTSTS (+IDST) with get_timer timeout | per command |
| 0x1B0FC | update_clk: CMD=0x80202000, poll CMD bit31 | set_ios, error recovery |
| 0x1B180 | ops.set_ios: clock gate off, update_clk, host->cb4(host,clock), gate on, update_clk, CTYPE, host->cb8 | init only |
| 0x1F094 | tm1 set_mod_clk(host,hz): CCU 0x830, CLKDIV[7:0], DRV_DL bits16/17, NTSR bits31,5:4 | init only |
| 0x1F722 | tm1 set_timing: CTRL bit10 (DDR) under CCU gate toggle | init only |
| 0x1ACE4 | tm1 core init (ops.init -> host->cbc) | init only |
| 0x1EC68 | tm1 bus gate/reset + mod clock gate (host->cc0) | init, error recovery |
| 0x1BD34 | sunxi_mmc_init(0) (from board_mmc_init 0x2724) | once |
| 0x1D968 | resource/DT parse, picks host type, delay tables | once |
| 0x19688 | mmc_init -> 0x18EC8 mmc_start_init -> 0x18E98 -> 0x18604 mmc_startup; then 0x1D48C (no-op for SD) | once (from 0x40DB8 sunxi_flash mmc init) |
| 0x1AEF8 / 0x1AFBC | "Current spd_md/Get next samply point" retry helpers (tm4 / generic) | only via the sdc_ex_dly_used==2 retry path |

Init vs per read: card initialization (CMD0/8/55/41/2/3/9/7, ACMD51, CMD6, ACMD6, CMD6
switch, ACMD13, clock to 25/50 MHz) is done once by `mmc_init` from the sunxi_flash init
at `0x40DB8`, long before `do_sunxi_flash`. `mmc_init` returns at once when
`mmc->has_init` (mmc+0x14) is set, and nothing on the read path calls it unless a read
fails and `sdc_ex_dly_used == 2`.

### State the read path assumes (must exist when the tracer starts)

- `[0x4A0000E0]` work_mode = 0, `[0x4A0000E4]` storage_type = 1 (SD). Written by boot0.
- sunxi_flash desc at 0x4A0F7410 with block_read 0x3FE04 (set by 0x400CC); ops pointer
  `[0x4A0E428C]` = 0x4A0E4294 (normal-boot mmc ops).
- `*(0x4A0F8500)` = mmc; mmc on the list at 0x4A0EDB1C with `block_dev.devnum` (mmc+0xE8)=0.
- mmc: +0x08 cfg (= host+0xBD0), +0x0C version (SD: bit31 set, e.g. 0x80020000),
  +0x10 priv (= host), +0x14 has_init=1, +0x18 high_capacity, +0x20 bus_width=4,
  +0x24 clock, +0x78 read_bl_len=512, +0xE4 block_dev (+0x10 of it = lba, +0x78 of it =
  block_read 0x19881), +0xEF hwpart=0, +0x19C timing (0 or 1), +0x1B8/+0x1BC tuning flags
  (0), +0x1C0 bit0 = print errors, +0x1C4 = 0 (CMD12 not sent).
- host: +0x00 index 0, +0x04 controller version (from R+0x300), +0x14 fatal_err=0,
  +0x20 0x03001830, +0x08/+0x0C 0x0300184C, +0x3C 0x04020000, +0x40 register shadow
  0x4A0F0ED4, +0x44 descriptor buffer (memalign(64,0x40000)), +0x48 host type 1,
  +0xBCC mmc, +0xCAC FIFOTH value 0x200700F8, +0xC2C cfg.b_max 0xFFFF,
  +0xC48 cfg sdc_ex_dly_used (0 here).

## 2. Polling loops

All `get_timer` timeouts use `get_timer(start) = ms(CNTPCT) - start`, ms = CNTPCT/24000
(`0xD84`, `mrrc p15,0,rX,rY,c14`). One loop iteration = one register read + one CNTPCT
read. `udelay` = busy wait on CNTPCT (`0xD50`, 24 ticks per us). Hook 0xDE8/0xD84/0xD50
(or CNTPCT) to control time and count iterations.

### 2.1 Wait helper 0x1AE54 (`wait(host, &mmc->0x1C0, timeout_ms, done_mask, name, dma)`)

```
start = get_timer(0)
loop:
  st = R[0x38] RINTSTS                                 ; 0x1AE72
  if get_timer(start) > timeout_ms  or  st & 0xBFC2:   ; 0x1AE7C..0x1AE8A
      print "mmc %d %s timeout %x status %x"; return -110
  done = st & done_mask
  if dma and strncmp(name,"data",5)==0 and done:
      done = (R[0x88] IDST & 3) != 0                    ; 0x1AECE
  if done: return 0
```
Error mask 0xBFC2 = RE(1) RCE(6) DCE(7) RTO(8) DRTO(9) HTO/DS(10) FU(11) HLE(12) SBE(13)
EBE(15). Not in the mask: CC(2) DTC(3) TXDR(4) RXDR(5) ACD(14). Note the timeout check
does not look at `done`: if the clock passes the limit the wait fails even with the bit set.

| Call site | name | done_mask | timeout | dma flag |
|---|---|---|---|---|
| 0x1BB32 (every command) | "cmd" | 0x4 (CC), set | 1000 ms | 1 if this command uses IDMA, else 0 (only affects "data") |
| 0x1B92C (data commands) | "data" | 0x8 (DTC) if blocks<2, else 0x4000 (ACD, auto-stop done); plus IDST&3 when DMA | 6000 ms (0x1770) | 1 for DMA, 0 for PIO |

### 2.2 Busy wait (0x1BB4C..0x1BB8E), after the response when resp_type has MMC_RSP_BUSY (R1b) or after any data write

`R[0x3C] STATUS & 0x200` (card busy) must become clear. Timeout via get_timer:
2000 ms (0x7D0); 0x1FFFFFFF ms for CMD38, and for CMD6 whose arg bits 23:16 are 0xA5.
Timeout -> -110. Not used by reads (CMD12 is never sent, see 3).

### 2.3 PIO FIFO loop (0x1B8C8..0x1B90E), only if blocks*blksz <= 64 (ACMD51, CMD6, ACMD13 at init)

Per 32-bit word: while `R[0x3C] & 4` (FIFO empty, read) / `& 8` (FIFO full, write):
`if get_timer(start) > max(bytes,2000)` -> -110. Then one read of `R[0x200]`. CMD is
written before the loop; then 2.1 "cmd" (dma=0) and "data" (mask 8, dma=0).

### 2.4 IDMAC soft reset (0x1BA94): `R[0x80] & 1` must clear after writing 1. No timeout.

### 2.5 update_clk 0x1B0FC

```
CLKDIV |= 0x80000000 ; CMD = 0x80202000
while (int)R[0x18] < 0:  if get_timer(start) > 10000: dump regs, return -1
CLKDIV &= ~0x80000000 ; RINTSTS = RINTSTS
```

### 2.6 Error recovery inside 0x1B350 (only after a failed wait)

- `R[0x00] = 7`, then poll `R[0x00] & 7 == 0`, no timeout (0x1B4CA and again 0x1B684).
- Only if mmc+0x1BC (tuning flag) and a 512-byte read: up to 1001 x {`R[0x4C] < 0x200` ?
  read `R[0x200]`, `udelay(1)`} (0x1B488).
- Then full controller reset, CCU reset/gate cycle through host->cc0 (0x1EC68), register
  restore from the shadow at host+0x40, and update_clk (2.5).

### 2.7 Retry wrapper 0x1BBF0 (only when work_mode==0 and cfg sdc_ex_dly_used==2 and not tuning)

`STATUS & 0x200` polled with `udelay(1)` up to 1,000,000 times (0xF4241) after a manual
CMD12. Not reached on this board.

### 2.8 Init-only loops (for completeness)

- sd_send_op_cond 0x17A34: up to 1001 x (CMD55, ACMD41), `udelay(1000)` between, until
  response bit31 set.
- mmc_send_op_cond 0x179D4 (MMC only): get_timer 1000 ms, `udelay(100)`.
- mmc_send_status 0x17ACC: CMD13 up to N times with `udelay(1000)`.
- CMD6 check 0x18960: up to 4 x CMD6(mode 0) while status busy bit set.
- ACMD13 0x18AD4: up to 4 tries.

## 3. Command and data routine 0x1B350, reconstructed

```
do_send_cmd(host, mmc, cmd, data):
  if host->fatal_err: return -1
  if cmd.idx == 12 and mmc->0x1C4 == 0: return 0   # stop never reaches HW: auto-stop is used
  flags = 0x80000000 | (cmd.idx==0 ? 0x8000 : 0)     # START | SEND_INIT for CMD0
  if cmd.idx == 12 and no data and mmc->0x1C4 == 1: flags |= 0x4000   # STOP_ABORT
  if resp & PRESENT: flags |= 0x40 ; if resp & 136: |= 0x80 ; if resp & CRC: |= 0x100
  if data:
      if data.dest & 3: error "dest is not 4 aligned"
      flags |= write ? 0x2600 : 0x2200          # WAIT_PRE_OVER | DATA_EXP (| WRITE)
      if data.blocks > 1: flags |= 0x1000       # auto stop
      R[0x10] BLKSIZ = blocksize ; R[0x14] BYTCNT = blocks*blocksize
  R[0x1C] CMDARG = cmd.arg
  if not data: R[0x18] = idx|flags ; dma = 0 ; goto wait_cmd
  # data timeout
  t = (mmc.clock/1000)*200 ; (x2 for some DDR/HS400 cases, reading R[0x5C])
  ctrl = R[0x00]; if t >= 0x1000000 and timing != 2: ctrl |= 0x800, t = (t+255)>>8
                  else: ctrl &= ~0x800, t = 0xFFFFFF
  R[0x00] = ctrl ; R[0x08] = (R[0x08] & 0xFF) | t<<8
  bytes = blocks*blocksize
  if bytes <= 64: PIO (2.3): R[0x00] |= 0x80000000 ; R[0x18] = idx|flags ; move words ; dma=0
  else:
      R[0x00] &= ~0x80000000
      build descriptors at host->0x44 (section 4); flush_dcache(buf, roundup64(bytes)); flush descs
      R[0x00] |= 0x24                            # DMA reset + DMA enable
      R[0x80] = 1 ; while R[0x80] & 1            # 2.4
      R[0x80] = 0x82                             # IDMAC on, fixed burst
      R[0x8C] = (R[0x8C] & ~3) | (read ? 2 : 1)  # RI or TI interrupt enable
      R[0x84] = desc_base (>>2, see 4)
      R[0x40] = host->0xCAC (0x200700F8)
      R[0x18] = idx|flags ; dma = 1
wait_cmd:
  wait("cmd", CC=0x4, 1000 ms)                   # 2.1
  if data: wait("data", blocks<2 ? 0x8 : 0x4000, 6000 ms, dma)
  if resp & BUSY or (data and write): busy wait (2.2)
  if resp & 136: resp[0..3] = R[0x2C], R[0x28], R[0x24], R[0x20]
  else:          resp[0]    = R[0x20]
  if dma: R[0x88] = R[0x88] (W1C) ; R[0x8C] = 0 ; R[0x80] = 0 ; R[0x00] &= ~0x20
  R[0x38] = 0xFFFFFFFF ; R[0x00] |= 2 (FIFO reset, not polled)
  if dma and read: invalidate_dcache(buf, buf+roundup64(bytes))
  return 0
on any wait failure: host->0xCA0 = RINTSTS & 0xBFC2 masked; print; DMA teardown;
  recovery (2.6); RINTSTS = ~0; CTRL |= 2; return -110 (or -1)
```

CMD register values the model will see (bit31 start always set):

| Command | R[0x18] | Notes |
|---|---|---|
| CMD16 SET_BLOCKLEN 512 | 0x80000150 | every mmc_bread |
| CMD17 READ_SINGLE | 0x80002351 | when the chunk is 1 block |
| CMD18 READ_MULTIPLE | 0x80003352 | auto-stop bit 12; wait ACD (0x4000) |
| update clock | 0x80202000 | no completion interrupt needed; clear bit31 |
| CMD0 | 0x80008000 | init |
| CMD8 (R7) | 0x80000148 | arg 0x1AA |
| CMD55 (R1) | 0x80000177 | |
| ACMD41 (R3) | 0x80000069 | no CRC check |
| CMD2 (R2) | 0x800001C2 | 136-bit |
| CMD3 (R6) | 0x80000143 | |
| CMD9 (R2) | 0x800001C9 | 136-bit |
| CMD7 (R1b) | 0x80000147 | + busy wait |
| ACMD51 SCR, 8 bytes | 0x80002373 | PIO |
| CMD6, 64 bytes | 0x80002346 | PIO; arg 0x00FFFFF1 (check), 0x80FFFFF1 (switch HS), 0x80FFFFF0 (legacy) |
| ACMD6 (R1) | 0x80000146 | arg 2 (4-bit) |
| ACMD13 SSR, 64 bytes | 0x8000234D | PIO |

## 4. IDMA descriptors (built at 0x1B7A8..0x1B858)

Descriptor array at host->0x44 (memalign(64, 0x40000) at init, 16384 slots). Each
descriptor is 16 bytes, memset to 0 first:

| Word | Content |
|---|---|
| 0 config | bit31 OWN always; bit4 CH (chained) always; bit1 DIC on all but the last; bit3 FD on the first; bit2 LD and bit5 ER on the last (DIC cleared there) |
| 1 size | bits 15:0: 0x1000 for all but the last; last = bytes & 0xFFF, or 0x1000 when that is 0 |
| 2 buffer | `dest + i*0x1000` (shifted, see below) |
| 3 next | `&desc[i+1]` (shifted), 0 on the last |

So: single 0x8000003C; first of many 0x8000001A; middle 0x80000012; last 0x80000034.
Count = ceil(bytes/4096). Maximum bytes per descriptor 4096. Per command, mmc_bread caps
at 0xFFFF blocks (33,553,920 bytes, 8192 descriptors).

Address encoding: if host->0x04 (version from R+0x300 at init) is 0x40200, 0x40502,
0x40104, or above 0x502FF, buffer, next and DLBA (R+0x84) hold address >> 2; otherwise
plain byte addresses. Linux uses the >>2 form (idma_des_shift=2) for H616, so the model
should return a version above 0x502FF from R+0x300 (exact value: the boot log line
"SUNXI SDMMC Controller Version:0x%x") and decode `addr << 2`.

Completion as the driver sees it: RINTSTS CC (cmd), then DTC (1 block) or ACD
(multi-block), plus IDST & 3 non-zero (RI = bit1 for reads). It does not read descriptor
OWN bits back and does not check BYTCNT/TBBCNT on success.

Model action on a data command write (CMD bit9, CTRL bit5, IDMAC bit7): walk from
`DLBA<<2`; for each descriptor copy `size` bytes of the card at
`(arg if high_capacity else arg/512)*512 + offset` to `buf<<2`; stop at LD or next==0;
total must equal BYTCNT. Then set RINTSTS |= 0x4 | (blocks>1 ? 0x4000 : 0x8) (real
hardware also sets 0x8 for multi-block; harmless) and IDST |= 0x2 | 0x100.

## 5. Delays

| Where | Call | Argument |
|---|---|---|
| read path (success) | none | |
| 0x1B4A8 error recovery "Read remain data" | udelay 0xD74 | 1 (x up to 1001) |
| 0x1BCC2 retry wrapper busy check | udelay 0xD74 | 1 (x up to 1,000,000) |
| 0x1ACF6/0x1AD12/0x1AD22 tm1 core init | udelay 0x69A88 | 1000 each (3) |
| 0x18F2C mmc_start_init after set_ios | 0x69A88 | 2000 |
| 0x178E6 / 0x17904 mmc_go_idle | 0x69A88 | 1000 before CMD0, 2000 after |
| 0x17A96 sd_send_op_cond | 0x69A88 | 1000 per ACMD41 retry |
| 0x17A0E mmc_send_op_cond | 0x69A88 | 100 |
| 0x17B2A mmc_send_status | 0x69A88 | 1000 |
| 0x1BE5E/0x1BE8A card-pwr-gpios (absent here) | 0x69A88 | 1000 x time_pwroff_ms (200) |
| 0x1CE24 (tuning code, eMMC) | 0x69A88 | 10 |

`0x69A88` is lib `udelay` (chunks of 10 s), `0xD74` jumps to `__udelay`, `0xD78` is mdelay.

## 6. Clock choice

### 6.1 Host type and tables

`0x1D968` reads `sdc_type` from `/soc/card0_boot_para`; absent, so SDC0 gets "tm1"
(`0x1D9D4`) and `0x1F828` installs: f_max 50,000,000, FIFOTH 0x200700F8, host+0x48=1,
cb0=0x1E950, cb4=0x1F094, cb8=0x1F722, cbc=0x1ACE4, cc0=0x1EC68.

Delay tables in host (byte per [spd_md][freq_id], 5 x 8):
- +0x104 odly (from DT `sdc_odly_*`, else 0xFF), +0x12C sdly (same, 0xFF)
- +0x154 default odly, +0x17C default sdly (written by `0x1E950`):

| spd_md, freq_id | default odly (+0x154) | default sdly (+0x17C) |
|---|---|---|
| 0 DS, 0 (<=400 kHz) | 1 | 1 |
| 0 DS, 1 (<=25 MHz) | 1 | 1 |
| 1 HS/SDR25, 1 | 1 | 0 |
| 1 HS/SDR25, 2 (<=52 MHz) | 1 | 0 |
| 2 DDR50, 1 | 0 | 1 |
| 2 DDR50, 2 | 0 | 1 |
| 3 SDR104, 1 | 1 | 0 |
| 3 SDR104, 2 | 1 | 0 |

`sdc_ex_dly_used` decides where real values come from: absent/0 -> "Using default timing
para" (0x1E81A; only copies DDR entries); 1 -> DT `sdc_odly_%d_%d`/`sdc_sdly_%d_%d`;
2 -> boot0's record in the U-Boot header (0x4A0003E8, 0x30 bytes; `[0x4A000424] &
0xFF000001 == 0x55000001`, else "Is not EXT_PARA0_ID or EXT_PARA0_TUNING_SUCCESS_FLAG!").
This board: absent.

spd_md = MMC timing (mmc+0x19C): 0 DS/legacy, 1 HS (SD HS, SDR12/25), 2 DDR50/DDR52,
3 HS200/SDR104/SDR50, 4 HS400 (mode->timing table 0x4A0A3E8C).
freq_id (from the requested Hz in 0x1F0F6): 0 <=400 kHz, 1 <=25 MHz, 2 <=52 MHz,
3 <=100 MHz, 4 <=150 MHz, 5 <=200 MHz.

### 6.2 tm1 set_mod_clk 0x1F094(host, hz)

```
mod = hz * (timing==2 ? 4 : 2)
if mod <= 24 MHz: src = 0 (OSC24M), parent = 24 MHz
else:             src = 1<<24,   parent = clock_get_pll6() * 2,000,000
                  clock_get_pll6 = 24*(N+1)/(b0+1)/(b1+1)/2 from CCU[0x020] (N = bits 15:8)
div = ceil(parent/mod); n = 0; while div > 16: div = (div+1)>>1, n++ ; n>3 -> error
R[0x5C] NTSR |= 0x80000000                       (plain read/write)
CCU[0x830] &= ~bit31 ; CCU[0x830] = (div-1) | n<<8 | src ; CCU[0x830] |= bit31
R[0x04] CLKDIV = (CLKDIV & ~0xFF) | (timing==2 ? 1 : 0)
host+0xFC = timing, host+0x100 = freq_id
odly = tbl104[i] (0xFF -> tbl154[i]); sdly = tbl12C[i] (0xFF -> tbl17C[i]); save at +0x1A8/9
CCU gate off; R[0x140] DRV_DL = (DRV_DL & ~0x30000) | (odly&1)<<16 | (odly&1)<<17 ; gate on
R[0x5C] NTSR = (NTSR & ~0x30) | (sdly&3)<<4
```
SAMP_DL (0x144) and DS_DL (0x148) are not programmed by tm1 (only saved/restored in the
error path).

### 6.3 Sequence at init (once)

sunxi_mmc_init: CCU reset+gate on, version read, set_mod_clk(24 MHz). mmc_start_init:
core init (2.8, 5), bus width 1, clock f_min 400 kHz, CMD0, CMD8, ACMD41 loop, then
mmc_startup: CMD2, CMD3, CMD9, CMD7, ACMD51 (SCR), CMD6 check (card_caps |= SD_HS if
supported), for mode in [SD_HS, SD_LEGACY] (table 0x4A074E14) and width 4 then 1:
ACMD6, CMD6 switch (status byte 16 low nibble must equal the function), clock =
mode2freq (SD_HS 50 MHz, SD_LEGACY 25 MHz, table 0x4A0743E4), ACMD13 (SSR); first that
works wins; on failure it falls back to SD_LEGACY 25 MHz and tries the next. Then
sunxi_switch_to_best_bus (0x1D48C) returns 0 at once because `mmc->version < 0` (SD).

## 7. Registers outside SMHC

| Address | Read/written by | When | Model |
|---|---|---|---|
| CCU 0x03001020 PLL_PERIPH0 | 0x2038 read | each clock change > 24 MHz (init only) | return the value boot0 left; e.g. 0x..216300 gives pll6=1200 -> parent 2.4 GHz in this formula; with b0 or b1 set pll6=600 -> 1.2 GHz. Not certain which the device has: log it |
| CCU 0x03001830 SMHC0_CLK | 0x1F094, 0x1F722, 0x1EC68, 0x1B62E RMW | init, error recovery | plain RAM |
| CCU 0x0300184C SMHC_BGR | 0x1EC68 RMW (bit0 gate, bit16 reset) | init, error recovery | plain RAM |
| PIO 0x0300B348 POW_VAL | 0x1D934 read bit5 (port F) | init | 0 (3.3 V) |
| PIO 0x0300B340 POW_MOD_SEL | 0x1D934 RMW | init | plain RAM |
| PIO port F (0x0300B0B4 cfg, 0x0300B0C8 drv, 0x0300B0D8 pull) | 0x3BA0 gpio setup from card0_boot_para pinctrl (PF0-PF5 func 2, pull 1, drv 3) | init | plain RAM |
| U-Boot header 0x4A0000E0/E4, 0x4A0003E8..0x4A000427 | read | see above | 0 / 1 / zeros |
| CNTPCT (CP15 c14, mrrc) | 0xD84, 0xD50 | every poll and delay | hook |

Card detect: ops.getcd 0x1AE40 returns 1; no GPIO read. No regulator or I2C access on these
paths (`card-pwr-gpios` absent; `sunxi-dis-signal-vol-sw` is not read by U-Boot).
Nothing outside SMHC is touched on the successful read path.

## 8. Register model table (SMHC0, 0x04020000)

Writes not listed: store and return on read.

| Off | Reg | Driver use | Read value to return |
|---|---|---|---|
| 0x00 | CTRL | RMW many times; writes 7 (reset) and polls bits 2:0 only in recovery; bit2 DMA reset, bit5 DMA enable, bit1 FIFO reset, bit10 DDR, bit11 timeout unit, bit31 FIFO via AHB | last written with bits 2:0 cleared |
| 0x04 | CLKDIV | RMW; bit16 card clock on, bit31 mask data0 | last written |
| 0x08 | TMOUT | RMW (keeps bits 7:0) | last written |
| 0x0C | CTYPE | write 1 (4-bit) | last written |
| 0x10/0x14 | BLKSIZ/BYTCNT | write | last written; BYTCNT = total for the copy |
| 0x18 | CMD | write; polled bit31 only in update_clk | written value with bit31 cleared (immediately, or after N polls to count) |
| 0x1C | CMDARG | write | last written |
| 0x20..0x2C | RESP0..3 | read after CC | card response for the last command (R2: RESP0 = bits 31:0 ... RESP3 = 127:96; driver reverses) |
| 0x30 | INTMASK | restore only | last written |
| 0x38 | RINTSTS | polled; W1C (writes 0xFFFFFFFF after each command, writes back in update_clk) | idle 0; after a command 0x4; after data +0x8 (1 block) or +0x4000 (multi, also 0x8 ok); never any of 0xBFC2 unless injecting an error |
| 0x3C | STATUS | PIO: bit2 empty / bit3 full; busy: bit9 | idle 0x00000006 (FIFO empty); during PIO read clear bit2 while words remain; bit9 0 |
| 0x40 | FIFOTH | write 0x200700F8 | last written |
| 0x4C | TBBCNT | recovery only | bytes moved |
| 0x50 | DBGC | write 0xDEB | last written |
| 0x54 | CSDC | write 3 | last written |
| 0x5C | NTSR | RMW (bit31, bits 5:4) | last written |
| 0x78 | HWRST | write 1/0/1 | last written |
| 0x80 | IDMAC | write 1 then poll bit0; write 0x82; write 0 | bit0 reads 0 |
| 0x84 | DLBA | write | last written |
| 0x88 | IDST | read in data wait (bits 1:0) and at teardown; W1C | after a DMA read 0x102 (RI + NIS); 0 after the driver writes it back |
| 0x8C | IDIE | RMW | last written |
| 0x100 | THLD | write 0x2000005 | last written |
| 0x10C | DDR_SBIT_DET | restore only | last written |
| 0x140/0x144/0x148 | DRV_DL/SAMP_DL/DS_DL | RMW 0x140; 0x144/0x148 restore only | last written |
| 0x200 | FIFO | PIO reads, recovery drain | next data word |
| 0x300 | VERS | read once at init into host+0x04 | a value > 0x502FF (see 4) |

Phases of one `block_read` on a working card:

| Step | Driver | Model |
|---|---|---|
| CMD16 | ARG=0x200, CMD=0x80000150 | RINTSTS=0x4, RESP0=0x900 (R1 tran) |
| | poll RINTSTS until 0x4 (n polls) | |
| | read RESP0; RINTSTS=~0; CTRL|=2 | clear RINTSTS |
| CMD18 | BLKSIZ=0x200, BYTCNT=n*512, ARG=lba, TMOUT RMW, CTRL RMW, descriptors, CTRL|=0x24, IDMAC=1 (poll), IDMAC=0x82, IDIE=2, DLBA, FIFOTH, CMD=0x80003352 | on CMD write: copy, RINTSTS=0x4 (then 0x4000|0x8 when "done"), IDST=0x102 |
| | wait "cmd" (CC), wait "data" (ACD and IDST&3) | the poll count of "data" is where transfer time shows |
| | read RESP0; IDST W1C; IDIE=0; IDMAC=0; CTRL&=~0x20; RINTSTS=~0; CTRL|=2; invalidate cache | |

## 9. The read sequence (pseudo-code)

```
do_sunxi_flash("read", 0x45000000, "boot"):
  desc = blk_get_dev("sunxi_flash", 0)                # 0x17734 -> 0x4A0F7410
  part_get_info(desc, "boot", &p)                      # 0x466C
  desc->block_read(desc, p.start, 0x40, buf)           # 0x3FE04
  n = pages(android header) ; desc->block_read(desc, p.start+0x40, n-0x40, buf+0x8000)

0x3FE04 block_read(desc, start, cnt, buf):
  if work_mode == 0x11 or storage_type in (1,2): return ops->phyread(start, cnt, buf)  # 0x403A0
  else return ops->read(start, cnt, buf)                                               # +0xA000

0x403A0: mmc = *(0x4A0F8500); return mmc_bread(&mmc->block_dev, start, cnt, buf)

0x19880 mmc_bread(bd, start, cnt, dst):
  mmc = find_mmc_device(bd->devnum); blk_dselect_hwpart(bd, bd->hwpart)  # no command
  if start+cnt > bd->lba: error
  mmc_set_blocklen(mmc, 512)                           # CMD16 (skipped in DDR/HS400)
  bounce = (dst & 0x3F) ? memalign(64,16 MiB) : NULL   # memset 16 MiB per chunk if used
  retried = 0
  while cnt:
    cur = min(cnt, cfg.b_max=0xFFFF) (and <= 16 MiB if bounce)
    if mmc_read_blocks(mmc, bounce or dst, start, cur) != cur:
       if retried: fail ; retried = 1 ; mmc_reinit(mmc) [no-op here] ; continue
    copy from bounce if used; start += cur; dst += cur*512; cnt -= cur
  return blocks

0x17864 mmc_read_blocks(mmc, dst, start, cnt):
  send_cmd(cnt>1 ? 18 : 17, high_capacity ? start : start*512, R1, data{dst, READ, cnt, 512})
  if cnt>1: mmc->0x1C4 = 0; send_cmd(12, 0, R1b)   # driver returns 0 without touching HW

0x1BBF0 send_cmd(mmc, cmd, data):
  ret = do_send_cmd(host, mmc, cmd, data)            # 0x1B350, section 3
  if work_mode != 0 or (mmc->0x1BC==1 and mmc->0x1B8==0) or cfg.sdc_ex_dly_used != 2:
      return ret
  ...retune / manual stop / busy poll / ops.decide_retry...   # not on this board
```

## 10. Caveats

- PLL_PERIPH0 value and the controller version are runtime values I cannot read statically;
  both are printed or derivable from a device boot log.
- The CMD12 skip depends on mmc+0x1C4 being 0; mmc_read_blocks writes 0 there before
  sending CMD12 (0x178AC), so it holds for every read.
- Real hardware also sets DTC on multi-block reads; the driver ignores it there.
