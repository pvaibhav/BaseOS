#!/usr/bin/env python3
"""Trace where the vendor U-Boot spends time from the boot logo to the kernel jump.

Runs each target's vendor U-Boot under Unicorn with a virtual clock, in four
phases:

    init   sunxi_flash_init: the real SD host driver initialises the card model
           (pre-logo on a device; run here so the read path finds real state)
    pre    two more pre-logo entries of init_sequence_r the logo path depends on:
           init_clocks + drv_disp_init (0x2a04) and bootreason + keybox (0x2a8c)
    late   board_late_init: draws the logo (display init, FAT read of the bitmap),
           then the vendor's post-logo steps
    main   main_loop: autoboot runs bootcmd, the card read and bootm up to the
           kernel jump

Time spent in a timer callback (interrupt context, the interrupted code waits)
is reported under "<phase>/irq" and is part of the phase's virtual ms.

Every delay routine and every timer-based wait is replaced by a model that
advances the virtual clock and logs the caller. Reads of hardware registers are
served by a zero-filled map (logged), except the SD host, which is a register
model good enough for the vendor driver's init and read paths (card commands,
PIO responses, IDMA descriptors). The one-shot software timers the vendor code
uses to step the panel on are modelled too: their callbacks run when the clock
passes their expiry, in interrupt fashion, so panel delays count.

Builds on tests/test-fast-boot-emulation.py (routine offsets of the common
code, environment, malloc and console models). Routine offsets beyond the
common region are mapped per build from call sites inside it.

    python3 experiments/h700-boot-speed/uboot-trace.py [--json FILE] [--pe6 high|low] [target ...]

Everything below is a model or a shortcut, with what it hides.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import struct
import sys
from collections import Counter
from pathlib import Path

from unicorn import UC_HOOK_BLOCK, UC_HOOK_CODE, UC_HOOK_INSN_INVALID, UC_HOOK_MEM_READ, UC_HOOK_MEM_WRITE, UcError
from unicorn.arm_const import UC_ARM_REG_CPSR, UC_ARM_REG_LR, UC_ARM_REG_PC, UC_ARM_REG_R0, UC_ARM_REG_SP

REPO = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("harness", REPO / "tests/test-fast-boot-emulation.py")
harness = importlib.util.module_from_spec(spec)
spec.loader.exec_module(harness)
Failed, Machine, UBOOT, FDT, RETURN, STACK = (harness.Failed, harness.Machine, harness.UBOOT, harness.FDT,
                                              harness.RETURN, harness.STACK)
GD = harness.GD

MODELLED = """\
  harness models          env_get/env_set on a dict started from the env partition; malloc as a
                          bump allocator; puts/pr_msg collected; flush_cache and interrupt
                          enable/disable are no-ops; boot_jump_linux stops the run; hang fails it
                          (tests/test-fast-boot-emulation.py, MODELLED there)
  calloc, realloc,        dlmalloc versions read chunk headers the bump allocator does not write;
  memalign                replaced by the bump allocator (aligned; fresh memory reads as zeros)
  _do_env_set             the `setenv` command on the same dict (vendor signature: argc, argv, flag)
  .bss                    zeroed before the run: it overlays .rel.dyn in the file; on a device it is
                          zero after relocation. Nothing pre-logo has written into it except what
                          the init and pre phases write
  virtual clock           __udelay, get_timer_masked and get_ticks run on a virtual 24 MHz counter.
                          Code costs one instruction per cycle at CPU_HZ (bytes executed / 3), a
                          peripheral read 2 ticks. Hides: caches, DRAM speed, real IPC; the CPU
                          share is reported separately (cpu_ms_at_assumed_clock)
  add_timer, del_timer    the sunxi one-shot timers: the callback runs at the first basic block
                          after the clock passes its expiry, on its own stack, with the interrupted
                          code suspended (as the IRQ handler does); no nesting. Hides: IRQ entry
                          and GIC cost (microseconds)
  SD host and card        SMHC0 register model and an SDHC card as large as its GPT; data reads
                          by IDMA from the card image; a transfer takes bytes / 20 MB/s, and a
                          status poll moves the clock to its end (or to the next timer expiry
                          first). Hides: per-command latency of a real card (access time,
                          busy); the number of commands is reported (card_read_commands) so a
                          latency can be applied afterwards. Card writes are accepted, not stored
  peripheral registers    everything else in 0x0-0x10000000 reads 0 (logged), except:
                          PE_DAT 0x0300B0A0 reads PE6 high (the RG SP's lcd_type=old; --pe6 low
                          gives the other panel path); CCU PLL registers read back what was
                          written with LOCK (bit 28) set (hides the lock time, tens of us; without
                          it each PLL waits its 5 ms timeout). PLLs boot0 programs (PERI0) read
                          as 0 | LOCK, so clock code computes other dividers (SMHC0 stays on OSC24M);
                          no wait depends on them. A register read 20000 times by one
                          instruction is flipped and listed in spins_resolved
  sunxi_markid_check      no-op returning 0: it compares the SID chip mark (reads 0 here) with
                          a list and shuts down on a mismatch; on a device it passes, no delays
  sunxi_update_boot0_check no-op returning 0: it wanted to rewrite boot0 here; on a device it does
                          nothing unless an OTA flag is set
  secure monitor calls    `smc` returns 0 in r0 and is logged (smc_calls); none on the traced path
  starting state          U-Boot header work_mode 0 (normal) and storage 1 (SD); mmc device list
                          head initialised; gd+0x130 = 3 (the 1 ms IR timer has fired, so 0x34b8
                          does not wait 200 ms); console level 0 as the DT's debug_mode, so printf
                          prints nothing (formats are logged in printf_formats)
  not run (pre-logo)      the PMIC init (0x25d8) and the rest of init_sequence_r. Effects: the PMU
                          ops are null, so the battery check 0x7b44 takes its "no battery" exit
                          (on a healthy device: a few AXP2202 I2C reads and DT lookups, no delays)
                          and bootreason reads "button" (device: "unknow"); the secure-OS flag is
                          unset, so bootargs lack androidboot.secure_os_exist=0"""

# ---------------------------------------------------------------- vendor code (common region)
UDELAY = 0xD50              # __udelay(us): busy wait on CNTPCT
GET_TIMER_MASKED = 0xD84    # CNTPCT / 24000
GET_TICKS = 0xDF4           # CNTPCT
ADD_TIMER = 0xE14           # add_timer(struct {expires_ms, fn, arg, slot})
DEL_TIMER = 0xED0
BOARD_LATE_INIT = 0x2B14
MAIN_LOOP = 0xEA90
FLASH_INIT_CALL = 0xF1DC    # initr_sunxi_flash: bl sunxi_flash_init (beyond the common region)
ENV_SET_CALL = 0xE644       # env_set: bl _do_env_set after it built argv
FIND_MMC_DEVICE = 0x1AAC0   # its first instruction loads the mmc device list head
CALLOC, REALLOC, MEMALIGN = 0x11B34, 0x11B9C, 0x11E5C   # dlmalloc: they read chunk headers malloc's model lacks
DISPLAY_INIT = 0x2A04       # init_sequence_r entry before the logo: init_clocks, then drv_disp_init
BOOTREASON_INIT = 0x2A8C    # init_sequence_r entry after initr_env: env bootreason from the PMIC, keybox
PRINTF_CALL = 0x20144       # disp_ioctl: bl printf ("%s, display not init yet")
TICK_PRINTF_CALL = 0x1FE10  # drv_disp_init: bl tick_printf ("%s\n")
MARKID_CHECK = 0x2524      # sunxi_markid_check: SID chip mark against a list; shuts down on a mismatch
BOOT0_UPDATE_CHECK = 0x2AA8  # "update boot0 after ota": rewrites boot0 on the card when a flag is set
HEADER_WORK_MODE, HEADER_STORAGE = 0xE0, 0xE4
IR_FLAG = 0x130             # gd field the IR timer callback sets to 3 before the logo
UDELAY_CALL = 0xEDE4        # abortboot: bl udelay(10000) (lib/time.c udelay, beyond the common region)
DISP_DELAY_MS, DISP_DELAY_US = 0x24DD4, 0x24DDE   # display delay helpers (also the panel's sunxi_lcd_delay_ms)
LATE_STEPS = (0x2B1E, 0x2B52)  # board_late_init: the run of BLs, logo first
# Routines timed from entry to return (offset, or call site for routines beyond the common region).
SPANS = {"boot_gui_init": ("call", 0x2A34), "sunxi_bmp_display": ("call", 0x2A6C), "do_fat_fsload": ("at", 0x8392),
         "autoboot_command": ("at", 0xED48), "do_sunxi_flash": ("at", 0xC1E8), "do_bootm": ("at", 0x7E70),
         "update_bootargs": ("at", 0x5734), "gunzip": ("call", 0xF3C2)}
LATE_NAMES = {0x2A2C: "board_bootlogo_display", 0x2AA8: "sunxi_update_boot0_check", 0x3738: "update_fdt_para_for_kernel",
              0x47A8: "env_format_check", 0x2524: "sunxi_markid_check", 0x3850: "update_partinfo",
              0x39B0: "rotpk_env", 0x7B44: "bat_temp_check", 0x34B8: "ir_wait_and_misc", 0x36C8: "update_bootcmd",
              0x5054: "set_snum", 0x5584: "update_serial_feature"}
END_LITERAL = 0x19AE        # board_fdt_blob_setup: ldr r0, =_end (end of .rel.dyn)

PE_DAT = 0x0300B0A0         # port E data: PE6 is the panel strap the panel driver reads (pull-up on)
PE6_HIGH = 0x40             # what the RG SP reads: its kernel command line carries lcd_type=old (PE6 high)
CCU_PLLS = (0x03001000, 0x03001100)   # PLL control registers: bit 28 is LOCK
PLL_LOCK = 1 << 28
SMHC0 = 0x04020000
SMHC_VERSION = 0x50300
PERIPHERALS = (0x00000000, 0x10000000)
CB_STACK = 0x4FFE0000       # stack for timer callbacks (interrupt context)
RETURN2 = 0x4FFE8000

CPU_HZ = 1008_000_000       # assumed for the CPU-time estimate; reported in instructions too
BYTES_PER_INSN = 3
CARD_BYTES_PER_US = 20      # 20 MB/s, the sequential rate the kernel measures
SPIN = 20000                # reads of one register by one instruction before it counts as stuck


def thumb_bl_targets(code: bytes, start: int, end: int) -> list[tuple[int, int]]:
    """(site, target) of every BL/B.W between start and end."""
    out = []
    at = start
    while at < end:
        target = harness.branch_target(code, at)
        if target is not None:
            out.append((at, target))
            at += 4
        else:
            at += 2
    return out


def bss_range(code: bytes) -> tuple[int, int]:
    """Start of .bss and the end of the binary. .bss overlays .rel.dyn (R_ARM_RELATIVE pairs) and
    runs past the end of the file; on a device it reads as zeros once U-Boot has relocated."""
    end = harness.literal(code, END_LITERAL) - UBOOT
    start = end
    while start >= 8 and struct.unpack_from("<II", code, start - 8)[1] == 0x17:
        start -= 8
    harness.require(end - start > 0x1000, "no .rel.dyn before _end")
    return start, len(code)


class Clock:
    def __init__(self):
        self.ticks = 0
        self.delays: Counter = Counter()
        self.delay_calls: Counter = Counter()
        self.phase = "setup"
        self.phase_start = 0
        self.phases: dict[str, dict] = {}
        self.code_bytes = 0                   # bytes of code executed, for the CPU-time share
        self.cpu_ticks = 0
        self.card_ticks: Counter = Counter()

    def execute(self, size: int) -> None:
        """Advance by the time `size` bytes of Thumb code take at the assumed clock, one instruction per cycle."""
        self.code_bytes += size
        due = self.code_bytes * 24_000_000 // (BYTES_PER_INSN * CPU_HZ)
        self.ticks += due - self.cpu_ticks
        self.cpu_ticks = due

    @property
    def us(self) -> float:
        return self.ticks / 24

    def sleep_us(self, us: int, caller: int) -> None:
        self.ticks += us * 24
        self.delays[(self.phase, caller)] += us
        self.delay_calls[(self.phase, caller)] += 1


class SDCard:
    """Responses of one SDHC card for the commands the vendor init and read paths send."""

    def __init__(self, image: bytes):
        self.image = image
        self.app = False
        # The card is as large as its GPT says (backup header in the last sector), so the
        # vendor GPT check (last usable LBA <= card size) passes as on the device.
        harness.require(image[512:520] == b"EFI PART", "card image has no GPT")
        self.sectors = struct.unpack_from("<Q", image, 512 + 32)[0] + 1

    def respond(self, idx: int, arg: int, data_bytes: int) -> tuple[list[int], bytes | None]:
        app, self.app = self.app, idx == 55
        r1 = 0x900
        if idx == 8:
            return [0x1AA, 0, 0, 0], None
        if idx == 41 and app:
            return [0xC0FF8000, 0, 0, 0], None
        # R2 responses are listed in register order, RESP0 = bits 31:0 first; the driver reverses them.
        if idx == 2:
            return [0x78012345, 0x80123456, 0x4E363447, 0x03534453], None
        if idx == 3:
            return [0x00010500, 0, 0, 0], None
        if idx == 9:                                   # CSD v2: 512-byte blocks, C_SIZE from the card size
            size = self.sectors // 1024 - 1
            return [0, (size & 0xFFFF) << 16, (9 << 16) | (size >> 16), (1 << 30) | 0x32], None
        if idx == 51 and app:                          # SCR: SD 2.0, bus widths 1 and 4
            return [r1, 0, 0, 0], bytes([0x02, 0x25, 0, 0, 0, 0, 0, 0])
        if idx == 6 and data_bytes:                    # switch status: group 1 supports high speed
            status = bytearray(64)
            status[13] = 0x02
            status[16] = 0x01
            return [r1, 0, 0, 0], bytes(status)
        if idx == 13 and app and data_bytes:           # SD status
            return [r1, 0, 0, 0], bytes(64)
        if idx in (17, 18):                            # beyond the prepared prefix the card reads as zeros
            return [r1, 0, 0, 0], self.image[arg * 512:arg * 512 + data_bytes].ljust(data_bytes, b"\0")
        return [r1, 0, 0, 0], None


class SDHost:
    """Register model of SMHC0 for the vendor driver (see experiments notes on the driver)."""

    CTRL, BLKSIZ, BYTCNT, CMD, CMDARG, RINTSTS, STATUS, IDMAC, DLBA, IDST, FIFO, VERS = (
        0x00, 0x10, 0x14, 0x18, 0x1C, 0x38, 0x3C, 0x80, 0x84, 0x88, 0x200, 0x300)

    def __init__(self, tracer: "Tracer", card: SDCard):
        self.t, self.card = tracer, card
        self.regs: dict[int, int] = {self.VERS: SMHC_VERSION}
        self.resp = [0, 0, 0, 0]
        self.pio: list[int] = []
        self.commands: list[dict] = []
        self.busy: tuple[int, int, int] | None = None     # (done at tick, RINTSTS bits, IDST bits) of a transfer

    def settle(self) -> None:
        """A DMA read in flight: the status registers show it done once the clock passes its end. A poll
        moves the clock there, or only to the next timer interrupt, so panel steps can run meanwhile."""
        if self.busy is None:
            return
        done, rint, idst = self.busy
        clock = self.t.clock
        if clock.ticks < done:
            due = self.t.next_expiry()
            target = done if due is None else min(done, max(due, clock.ticks))
            clock.card_ticks[clock.phase] += target - clock.ticks
            clock.ticks = target
        if clock.ticks >= done:
            self.regs[self.RINTSTS] = self.regs.get(self.RINTSTS, 0) | rint
            self.regs[self.IDST] = self.regs.get(self.IDST, 0) | idst
            self.busy = None

    def read(self, off: int) -> int:
        if off in (self.RINTSTS, self.IDST, self.STATUS):
            self.settle()
        if off in (0x20, 0x24, 0x28, 0x2C):
            return self.resp[(off - 0x20) // 4]
        if off == self.CMD:
            return self.regs.get(off, 0) & 0x7FFFFFFF
        if off == self.IDMAC:
            return self.regs.get(off, 0) & ~1
        if off == self.FIFO:
            return self.pio.pop(0) if self.pio else 0
        if off == self.STATUS:
            return 0x0 if self.pio else 0x4          # bit 2: FIFO empty
        return self.regs.get(off, 0)

    def write(self, off: int, value: int) -> None:
        if off in (self.RINTSTS, self.IDST):          # write 1 to clear
            self.regs[off] = self.regs.get(off, 0) & ~value
            return
        self.regs[off] = value
        if off == self.CMD and value & 0x80000000 and not value & 0x200000:
            self.command(value)

    def command(self, value: int) -> None:
        idx, arg = value & 0x3F, self.regs.get(self.CMDARG, 0)
        total = self.regs.get(self.BYTCNT, 0) if value & 0x200 else 0
        blocks = total // max(self.regs.get(self.BLKSIZ, 512), 1) if total else 0
        if value & 0x400:                             # a write: accept it, keep the card as it is
            self.resp = [0x900, 0, 0, 0]
            self.regs[self.RINTSTS] = self.regs.get(self.RINTSTS, 0) | (0x4008 if blocks > 1 else 0xC)
            self.regs[self.IDST] = self.regs.get(self.IDST, 0) | 0x101
            self.commands.append({"phase": self.t.clock.phase, "cmd": idx, "arg": arg, "bytes": total,
                                  "at_ms": round(self.t.clock.us / 1000, 2), "write": True})
            return
        self.resp, payload = self.card.respond(idx, arg, total)
        self.regs[self.RINTSTS] = self.regs.get(self.RINTSTS, 0) | 0x4
        entry = {"phase": self.t.clock.phase, "cmd": idx, "arg": arg, "bytes": total,
                 "at_ms": round(self.t.clock.us / 1000, 2)}
        self.commands.append(entry)
        if not total:
            return
        if payload is None or len(payload) != total:
            raise Failed(f"card has no {total} bytes for CMD{idx} arg {arg:#x}")
        if total <= 64:
            self.pio = list(struct.unpack(f"<{total // 4}I", payload))
            self.regs[self.RINTSTS] |= 0x8
            return
        self.dma(payload)
        self.t.card_bytes[self.t.clock.phase] += total
        self.busy = (self.t.clock.ticks + int(total / CARD_BYTES_PER_US * 24), 0x4008 if blocks > 1 else 0x8, 0x102)

    def dma(self, payload: bytes) -> None:
        desc, done = self.regs.get(self.DLBA, 0) << 2, 0
        while desc:
            config, size, buf, nxt = struct.unpack("<4I", self.t.read(desc, 16))
            size &= 0xFFFF
            self.t.uc.mem_write(buf << 2, payload[done:done + size])
            done += size
            if config & 0x4 or not nxt:
                break
            desc = nxt << 2
        if done != len(payload):
            raise Failed(f"DMA descriptors cover {done} of {len(payload)} bytes")


class Tracer(Machine):
    def __init__(self, code: bytes, tree: bytes, environment: dict[str, str], card_image: bytes,
                 pe_data: int = PE6_HIGH):
        self.pe_data = pe_data
        self.hook_error: str | None = None
        self.bss = bss_range(code)
        super().__init__(code[:self.bss[0]] + bytes(len(code) - self.bss[0]), tree, environment,
                         harness.QUIET_CONSOLE)
        self.code = code
        self.clock = Clock()
        self.card_bytes: Counter = Counter()
        self.blocks_bytes: Counter = Counter()
        self.reads: Counter = Counter()               # (pc, address) -> count
        self.unknown: Counter = Counter()
        self.override: dict[int, int] = {}            # address -> value returned (auto-resolved spins)
        self.resolved: list[str] = []
        self.writes: list[tuple[int, int, int]] = []
        self.timers: list[dict] = []
        self.prints: list[str] = []
        self.pll: dict[int, int] = {}
        self.smc_calls: list[str] = []
        self.wrapper_lr: dict[int, int] = {}
        self.open_spans: dict[int, list] = {}            # return address -> [(name, start tick, phase)]
        self.spans: list[dict] = []
        self.in_callback = False
        self.fire = False
        self.sd = SDHost(self, SDCard(card_image))
        self.uc.mem_map(*PERIPHERALS)
        self.uc.hook_add(UC_HOOK_MEM_READ, self.guarded(self.mmio_read), begin=PERIPHERALS[0],
                         end=PERIPHERALS[0] + PERIPHERALS[1] - 1)
        self.uc.hook_add(UC_HOOK_MEM_WRITE, self.guarded(self.mmio_write), begin=PERIPHERALS[0],
                         end=PERIPHERALS[0] + PERIPHERALS[1] - 1)
        self.uc.hook_add(UC_HOOK_BLOCK, self.guarded(self.block))
        self.replace(UDELAY, lambda: self.clock.sleep_us(self.arg(0), self.delay_caller()))
        udelay = self.mapped(UDELAY_CALL)
        self.delay_wrappers = {}                        # return site inside a delay helper -> helper entry
        for entry, end in ((udelay, udelay + 0x20), (DISP_DELAY_MS, DISP_DELAY_US), (DISP_DELAY_US, DISP_DELAY_US + 10)):
            sites = [at + 4 for at, target in thumb_bl_targets(self.code, entry, end) if target in (UDELAY, 0xD74, 0xD78)]
            harness.require(len(sites) == 1, f"delay helper {entry:#x} has no single call of __udelay")
            self.delay_wrappers[sites[0]] = entry
            self.uc.hook_add(UC_HOOK_CODE, self.guarded(lambda *_, e=entry: self.wrapper_lr.__setitem__(e, self.caller())),
                             begin=UBOOT + entry, end=UBOOT + entry)
        self.uc.hook_add(UC_HOOK_INSN_INVALID, self.smc)
        spans = {name: self.mapped(at) if kind == "call" else at for name, (kind, at) in SPANS.items()}
        for site, target in thumb_bl_targets(self.code, *LATE_STEPS):
            spans[f"late {site:#x}: {LATE_NAMES.get(target, f'{target:#x}')}"] = target
        for name, at in spans.items():
            self.uc.hook_add(UC_HOOK_CODE, self.guarded(lambda *_, n=name: self.open_spans.setdefault(
                (self.uc.reg_read(UC_ARM_REG_LR) & ~1), []).append((n, self.clock.ticks, self.clock.phase))),
                begin=UBOOT + at, end=UBOOT + at)
        self.replace(GET_TIMER_MASKED, lambda: int(self.clock.us // 1000) & 0xFFFFFFFF)
        self.replace(GET_TICKS, lambda: self.clock.ticks & 0xFFFFFFFF)
        self.replace(ADD_TIMER, self.add_timer)
        self.replace(DEL_TIMER, self.del_timer)
        self.replace(self.do_env_set_offset(), self.do_env_set)
        self.replace(CALLOC, self.calloc)
        self.replace(REALLOC, self.realloc)
        self.replace(MEMALIGN, self.memalign)
        self.replace(MARKID_CHECK, lambda: 0)          # modelled away: the SID block reads 0 here
        self.replace(BOOT0_UPDATE_CHECK, lambda: 0)   # modelled away: it wanted to rewrite boot0 here
        self.replace(harness.PR_MSG, lambda: self.messages.__setitem__(
            -1, f"{self.text(self.arg(1))} % ({self.arg(2):#x}, {self.arg(3):#x})"))
        for site in (PRINTF_CALL, TICK_PRINTF_CALL):      # gated by the console level; log the format
            self.uc.hook_add(UC_HOOK_CODE, self.guarded(lambda *_: self.prints.append(
                f"{self.clock.phase}: {self.text(self.arg(0))!r} ({self.arg(1):#x})")),
                begin=UBOOT + self.mapped(site), end=UBOOT + self.mapped(site))
        self.uc.mem_write(UBOOT + HEADER_WORK_MODE, struct.pack("<II", 0, 1))
        head = harness.literal(code, FIND_MMC_DEVICE)        # INIT_LIST_HEAD(&mmc_devices), done by initr_mmc
        self.uc.mem_write(head, struct.pack("<II", head, head))
        self.put32(GD + IR_FLAG, 3)

    def guarded(self, hook):
        """Unicorn swallows exceptions raised inside hooks; keep the first one and stop."""
        def wrapper(*args):
            try:
                return hook(*args)
            except Exception as error:      # noqa: BLE001
                import traceback
                if self.hook_error is None:
                    self.hook_error = traceback.format_exc()
                self.uc.emu_stop()
        return wrapper

    def replace(self, at: int, model) -> None:
        super().replace(at, self.guarded(model))

    # -- per-build routine mapping
    def do_env_set_offset(self) -> int:
        """_do_env_set: the routine env_set calls after it built argv (the last BL before its return)."""
        target = harness.branch_target(self.code, ENV_SET_CALL)
        harness.require(target is not None, "env_set does not call _do_env_set where expected")
        return target

    def mapped(self, call_site: int) -> int:
        """Offset of a routine beyond the common region, from a BL to it inside the region."""
        harness.require(call_site < harness.SAME_CODE_LIMIT, f"call site {call_site:#x} is not common code")
        target = harness.branch_target(self.code, call_site)
        harness.require(target is not None, f"no call at {call_site:#x}")
        return target

    def sunxi_flash_init_offset(self) -> int:
        target = harness.branch_target(self.code, FLASH_INIT_CALL)
        harness.require(target is not None and target >= harness.SAME_CODE_LIMIT, "sunxi_flash_init call not found")
        return target

    # -- models
    def do_env_set(self) -> int:
        """_do_env_set(argc, argv, env_flag): this vendor build has no flag argument (env_set at 0xe642
        passes argc 2 or 3 in r0, argv in r1)."""
        argc, argv = self.arg(0), self.arg(1)
        words = [self.text(self.get32(argv + 4 * n)) for n in range(argc)]
        name, value = words[1], " ".join(words[2:])
        self.env_writes.append((name, value, self.caller()))
        if value:
            self.env[name] = value
        else:
            self.env.pop(name, None)
        return 0

    def delay_caller(self) -> int:
        """The code that asked for the delay, looking through udelay, mdelay and the display delay helpers."""
        caller = self.caller()
        while caller in self.delay_wrappers:
            caller = self.wrapper_lr.get(self.delay_wrappers[caller], caller)
        return caller

    def smc(self, uc, _data) -> bool:
        """A secure monitor call (Unicorn has no monitor and treats it as undefined): log it, return 0."""
        pc = uc.reg_read(UC_ARM_REG_PC)
        thumb = uc.reg_read(UC_ARM_REG_CPSR) & 0x20
        if thumb:
            first, second = struct.unpack("<HH", self.read(pc, 4))
            hit = first & 0xFFF0 == 0xF7F0 and second & 0xF000 == 0x8000
        else:
            hit = struct.unpack("<I", self.read(pc, 4))[0] & 0x0FFFFFF0 == 0x01600170
        if not hit:
            return False
        self.smc_calls.append(f"{self.clock.phase}: smc at {pc - UBOOT:#x}, r0 {self.arg(0):#x}, lr {self.caller():#x}")
        uc.reg_write(UC_ARM_REG_R0, 0)
        uc.reg_write(UC_ARM_REG_PC, pc + 4 | (1 if thumb else 0))
        return True

    def allocate(self, size: int, align: int = 16) -> int:
        """The harness's bump allocator with an alignment; never reuses memory, so blocks read as zeros."""
        self.heap = (self.heap + align - 1) & -align
        harness.require(self.heap + size < harness.STRINGS, "model heap exhausted")
        self.uc.reg_write(UC_ARM_REG_R0, size)
        return self.malloc()

    def calloc(self) -> int:
        size = self.arg(0) * self.arg(1)
        address = self.allocate(size)
        self.uc.mem_write(address, bytes(size))
        return address

    def realloc(self) -> int:
        old, size = self.arg(0), self.arg(1)
        address = self.allocate(size)
        if old:
            block = next((b for b in self.blocks if b[0] == old), None)
            harness.require(block is not None, f"realloc of unknown block {old:#x}")
            self.uc.mem_write(address, self.read(old, min(block[1], size)))
        return address

    def memalign(self) -> int:
        return self.allocate(self.arg(1), max(self.arg(0), 16))

    def add_timer(self) -> int:
        at = self.arg(0)
        ms, fn, arg = struct.unpack("<III", self.read(at, 12))
        self.timers.append({"struct": at, "expires": self.clock.ticks + ms * 24000, "fn": fn, "arg": arg,
                            "ms": ms, "phase": self.clock.phase,
                            "caller": "callback" if self.caller() == RETURN2 - UBOOT else self.caller()})
        self.put32(at + 12, 0)
        return 0

    def del_timer(self) -> int:
        at = self.arg(0)
        self.timers = [t for t in self.timers if t["struct"] != at]
        return 0

    def next_expiry(self) -> int | None:
        """When the next timer interrupt is due, if one can be taken now."""
        if not self.timers or self.in_callback:
            return None
        return min(t["expires"] for t in self.timers)

    def block(self, uc, address, size, data) -> None:
        if address in self.open_spans:
            for name, start, phase in self.open_spans.pop(address):
                self.spans.append({"name": name, "phase": phase, "at_ms": round(start / 24000, 1),
                                   "ms": round((self.clock.ticks - start) / 24000, 1)})
        self.blocks_bytes[self.clock.phase] += size
        self.clock.execute(size)
        due = self.next_expiry()
        if due is not None and self.clock.ticks >= due:
            self.fire = True
            uc.emu_stop()

    def mmio_read(self, uc, access, address, size, value, data) -> None:
        pc = uc.reg_read(UC_ARM_REG_PC) - UBOOT
        self.clock.ticks += 2
        if SMHC0 <= address < SMHC0 + 0x400:
            word = self.sd.read(address - SMHC0)
        else:
            key = (pc, address & ~3)
            self.reads[key] += 1
            if address & ~3 in self.override:
                word = self.override[address & ~3]
            elif address & ~3 == PE_DAT:
                word = self.pe_data
            elif CCU_PLLS[0] <= address < CCU_PLLS[1]:
                word = self.pll.get(address & ~3, 0) | PLL_LOCK
            else:
                word = 0
                self.unknown[key] += 1
            if self.reads[key] % SPIN == 0:
                flipped = 0 if word else 0xFFFFFFFF
                self.override[address & ~3] = flipped
                self.resolved.append(f"{self.clock.phase}: pc {pc:#x} spun on {address:#x} reading {word:#x};"
                                     f" now returns {flipped:#x}")
                harness.require(len(self.resolved) < 200, "too many spinning reads")
        uc.mem_write(address & ~3, struct.pack("<I", word))

    def mmio_write(self, uc, access, address, size, value, data) -> None:
        pc = uc.reg_read(UC_ARM_REG_PC) - UBOOT
        if len(self.writes) < 100000:
            self.writes.append((pc, address, value))
        if SMHC0 <= address < SMHC0 + 0x400:
            self.sd.write(address - SMHC0, value)
        elif CCU_PLLS[0] <= address < CCU_PLLS[1] and size == 4:
            self.pll[address] = value

    # -- running
    def run(self, at: int, phase: str, arguments: list[int] = ()) -> int | None:
        """Call the routine at `at`, serving timer callbacks on the way. None: kernel jump reached."""
        self.clock.phase = phase
        self.clock.phase_start = self.clock.ticks
        self.uc.reg_write(UC_ARM_REG_SP, STACK)
        self.uc.reg_write(UC_ARM_REG_LR, RETURN | 1)
        for n, value in enumerate(arguments):
            self.uc.reg_write(UC_ARM_REG_R0 + n, value)
        self.jump = None
        pc = (UBOOT + at) | 1
        while True:
            self.fire = False
            try:
                self.uc.emu_start(pc, RETURN, timeout=600 * 1000000)
            except UcError as error:
                raise Failed(f"{phase}: emulation stopped: {error} at pc {self.uc.reg_read(UC_ARM_REG_PC):#x},"
                             f" lr {self.uc.reg_read(UC_ARM_REG_LR):#x}; console {self.console[-3:]}")
            harness.require(self.hook_error is None, f"{phase}: model error at pc"
                            f" {self.uc.reg_read(UC_ARM_REG_PC):#x}:\n{self.hook_error}")
            harness.require(not self.hung, f"{phase}: U-Boot hung; console {self.console[-3:]}")
            if self.jump is not None:
                result = None
                break
            if self.fire:
                pc = self.uc.reg_read(UC_ARM_REG_PC) | 1
                self.serve_timers()
                continue
            harness.require(self.uc.reg_read(UC_ARM_REG_PC) == RETURN,
                            f"{phase}: did not return: pc {self.uc.reg_read(UC_ARM_REG_PC):#x}")
            result = self.arg(0)
            break
        entry = self.clock.phases.setdefault(phase, {"virtual_ms": 0.0})
        entry["virtual_ms"] = round(entry["virtual_ms"] + (self.clock.ticks - self.clock.phase_start) / 24000, 1)
        return result

    def serve_timers(self) -> None:
        """Run every expired timer callback as the interrupt handler would."""
        while self.timers:
            due = [t for t in self.timers if t["expires"] <= self.clock.ticks]
            if not due:
                return
            timer = min(due, key=lambda t: t["expires"])
            self.timers.remove(timer)
            context = self.uc.context_save()
            self.in_callback = True
            self.uc.reg_write(UC_ARM_REG_SP, CB_STACK)
            self.uc.reg_write(UC_ARM_REG_LR, RETURN2 | 1)
            self.uc.reg_write(UC_ARM_REG_R0, timer["arg"])
            before, phase = self.clock.ticks, self.clock.phase
            self.clock.phase = f"{phase}/irq"            # the interrupted code waits meanwhile
            try:
                self.uc.emu_start(timer["fn"] | 1, RETURN2, timeout=600 * 1000000)
            except UcError as error:
                raise Failed(f"timer callback {timer['fn']:#x} failed: {error} at"
                             f" pc {self.uc.reg_read(UC_ARM_REG_PC):#x}")
            self.clock.phase = phase
            harness.require(self.hook_error is None, f"timer callback {timer['fn']:#x}: model error:\n{self.hook_error}")
            harness.require(self.uc.reg_read(UC_ARM_REG_PC) == RETURN2,
                            f"timer callback {timer['fn']:#x} did not return: pc {self.uc.reg_read(UC_ARM_REG_PC):#x}")
            timer["ran_ms"] = round((self.clock.ticks - before) / 24000, 1)
            timer["ran_at_ms"] = round(before / 24000, 1)
            self.uc.context_restore(context)
            self.in_callback = False
            self.served = getattr(self, "served", []) + [timer]

    # -- report
    def report(self) -> dict:
        def by_phase(counter: Counter, scale: float, digits: int) -> dict:
            out: dict[str, dict] = {}
            for (phase, caller), amount in counter.most_common():
                out.setdefault(phase, {})[f"{caller:#x}"] = round(amount * scale, digits)
            return out
        return {
            "phases": self.clock.phases,
            "delay_ms_by_caller": by_phase(self.clock.delays, 1 / 1000, 2),
            "delay_calls": by_phase(self.clock.delay_calls, 1, 0),
            "timers": [{k: (f"{v:#x}" if k in ("fn", "caller", "struct") and isinstance(v, int) else v)
                        for k, v in t.items()
                        if k != "expires"} for t in getattr(self, "served", [])],
            "timers_pending": [{"fn": f"{t['fn']:#x}", "ms": t["ms"], "armed_in": t["phase"],
                                "due_in_ms": round((t["expires"] - self.clock.ticks) / 24000, 1)} for t in self.timers],
            "card_bytes": dict(self.card_bytes),
            "card_read_commands": dict(Counter(c["phase"] for c in self.sd.commands if c["bytes"] and not c.get("write"))),
            "card_commands": dict(Counter(c["phase"] for c in self.sd.commands)),
            "card_wait_ms": {phase: round(n / 24000, 1) for phase, n in self.clock.card_ticks.items()},
            "delay_ms_total": {phase: round(sum(v for (p, _), v in self.clock.delays.items() if p == phase) / 1000, 1)
                               for phase in dict.fromkeys(p for p, _ in self.clock.delays)},
            "sd_commands": self.sd.commands,
            "instructions_estimate": {phase: n // BYTES_PER_INSN for phase, n in self.blocks_bytes.items()},
            "cpu_ms_at_assumed_clock": {phase: round(n / BYTES_PER_INSN / CPU_HZ * 1000, 1)
                                        for phase, n in self.blocks_bytes.items()},
            "spans": self.spans + [{"name": name, "phase": phase, "at_ms": round(start / 24000, 1),
                                    "ms_until_end": round((self.clock.ticks - start) / 24000, 1)}
                                   for entries in self.open_spans.values() for name, start, phase in entries],
            "spins_resolved": self.resolved,
            "unknown_mmio_reads_top": {f"{pc:#x}@{address:#x}": n for (pc, address), n in self.unknown.most_common(15)},
            "env_writes": [(n, v if len(v) < 60 else v[:57] + "...") for n, v, _ in self.env_writes],
            "console": self.console[-40:],
            "messages": self.messages[-20:],
            "printf_formats": self.prints[-60:],
            "smc_calls": self.smc_calls,
            "bootargs": self.env.get("bootargs"),
        }


def card_image(name: str, derived: dict, partition: tuple[int, int]) -> bytes:
    """The prepared card: boot-prefix.img with the fast boot image in the boot partition."""
    prefix = bytearray((REPO / "work" / name / "boot-prefix.img").read_bytes())
    start, sectors = partition
    image = derived["boot"]
    prefix[start * 512:start * 512 + len(image)] = image
    return bytes(prefix)


def trace(name: str, kernel_gzip, pe_data: int = PE6_HIGH) -> dict:
    out: dict = {"target": name, "pe_dat": f"{pe_data:#x}"}
    try:
        prefix = REPO / "work" / name / "boot-prefix.img"
        environment = harness.stored_environment(prefix)
        derived = kernel_gzip.derive(name, prefix)
        target = harness.Target(name, derived, environment)
        t = Tracer(target.code, target.tree, environment, card_image(name, derived, target.partition), pe_data)
    except Exception as error:      # noqa: BLE001 - recorded, the next target runs
        out["failed"] = f"setup: {type(error).__name__}: {error}"
        return out
    try:
        out["init_result"] = t.run(t.sunxi_flash_init_offset(), "init")
        out["pre_results"] = [t.run(DISPLAY_INIT, "pre"), t.run(BOOTREASON_INIT, "pre")]
        out["late_result"] = t.run(BOARD_LATE_INIT, "late")
        out["main_result"] = t.run(MAIN_LOOP, "main")
        out["jump"] = t.jump
    except Failed as error:
        out["failed"] = str(error)
    out.update(t.report())
    out["summary"] = summary(out)
    return out


def summary(out: dict) -> dict:
    """The numbers the notes table shows."""
    return {
        "completed": out.get("jump") is not None,
        "virtual_ms": {p: v["virtual_ms"] for p, v in out["phases"].items()},
        "delay_ms": out["delay_ms_total"],
        "timers": [(t["ms"], t["ran_ms"]) for t in out["timers"]],
        "card_bytes": out["card_bytes"],
        "card_read_commands": out["card_read_commands"],
        "instructions": out["instructions_estimate"],
        "lcd_type": next((v for n, v in reversed(out["env_writes"]) if n == "lcd_type"), None),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("targets", nargs="*")
    parser.add_argument("--json", type=Path)
    parser.add_argument("--pe6", choices=("high", "low"), default="high",
                        help="panel strap PE6 (high: the RG SP's lcd_type=old)")
    args = parser.parse_args()
    tool = REPO / "tools/kernel_gzip.py"
    sys.path.insert(0, str(tool.parent))
    spec = importlib.util.spec_from_file_location("kernel_gzip_pinned", tool)
    kernel_gzip = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(kernel_gzip)
    targets = args.targets or list(json.loads(kernel_gzip.CATALOG.read_text())["profiles"])
    results = []
    for name in targets:
        result = trace(name, kernel_gzip, PE6_HIGH if args.pe6 == "high" else 0)
        results.append(result)
        print(json.dumps(result, indent=1, default=str))
        print(flush=True)
        print(f"{name}: {json.dumps(result.get('summary', result.get('failed')), default=str)}", file=sys.stderr)
        if args.json:                                   # after every target, so a later failure keeps the rest
            args.json.parent.mkdir(parents=True, exist_ok=True)
            args.json.write_text(json.dumps(results, indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
