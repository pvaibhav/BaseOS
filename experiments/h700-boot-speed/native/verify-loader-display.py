#!/usr/bin/env python3
"""Run the linked A32 loader from its entry through the boot-logo display path to the kernel SMC.

Host-only Unicorn replay with explicit peripheral models (CNTPCT, CTR, polls, TCON line
counter, PE6 strap). SRAM C (0x28000..0x57fff) is unmapped when SYSCTRL bit 24 is cleared
and any loader access to it fails the run. Not a panel, timing or coherency qualification.

v151: the SPL's panel record lets the loader skip the panel power/reset/strap steps; any
record or pin mismatch must fall back to the full steps. The waits after the panel stream
starts are deadlines polled between kernel decode slices; each must still hold its minimum.
"""
import argparse
import hashlib
import importlib.util
import json
import re
import struct
import sys
from pathlib import Path

import capstone as c
import unicorn as u
from unicorn import arm_const as a

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE.parent))
sys.dont_write_bytecode = True
import fdt

RECORD, DESC, DISPLAY, BMP, CAP = 0x4a300000, 0x4a300200, 0x4a300300, 0x4b400000, 0x400000
PANEL_RECORD = 0x4a300280
PE_CFG, PE_PULL = 0x0300b090, 0x0300b0ac
WAITS_MS = (30, 15, 50, 100)
SIMULATED_DECODE_STEPS = 40
SRAM_C = (0x28000, 0x58000)
PHASES = ("prepare", "publish", "enable", "backlight")
CFG, DAT, STRAP, SCANLINE = 0x0300b124, 0x0300b130, 0x0300b0a0, 0x065110fc
TICK = 24000
sha = lambda data: hashlib.sha256(data).hexdigest()


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


V = module("loader_verify", HERE / "verify.py")


def fnv(data, skip=None):
    value = 2166136261
    for index, byte in enumerate(data):
        value = ((value ^ (0 if skip and skip[0] <= index < skip[1] else byte)) * 16777619) & 0xffffffff
    return value


class Run:
    def __init__(self, native, model, streams, record, bmp, descriptor, serial):
        self.native, self.model, self.streams = native, model, streams
        self.manifest = json.loads((native / "manifest.json").read_text())
        self.code = (native / "loader.bin").read_bytes()
        self.image = (native / "image.bin").read_bytes()
        self.kernel = (native / "kernel.bin").read_bytes()
        self.sym = V.symbols(native)
        self.record, self.bmp, self.descriptor, self.serial = record, bmp, descriptor, serial
        decoder = c.Cs(c.CS_ARCH_ARM, c.CS_MODE_ARM)
        self.special = {}
        text_end = self.sym["__bss_start"] - 0x4a000000
        for offset in range(0x1000, min(text_end, len(self.code)) - 3, 4):
            ins = next(decoder.disasm(self.code[offset:offset + 4], 0x4a000000 + offset), None)
            if ins and ins.mnemonic in ("mrc", "mcr", "mrrc", "smc", "dsb", "wfi"):
                self.special[ins.address] = ins
        self.tables = {}
        for phase in PHASES:
            ops = model[phase]
            address = self.sym["baseos_display_" + phase]
            kinds = {"update": 0, "write": 1, "framebuffer": 2, "delay_us": 3, "poll": 4, "barrier": 5}
            expected = b"".join(struct.pack("<5I", kinds[op["kind"]], op["address"], op["clear_mask"], op["set_bits"],
                                            op["timeout_us"]) for op in ops)
            assert self.code[address - 0x4a000000:address - 0x4a000000 + len(expected)] == expected, phase
            self.tables[address] = phase

    def run(self, name, high=True, line=64, fault=None, unpack=False, panel=None, pio=None, record=None, skip=False):
        """panel: SPL panel record bytes (None = none published); pio: live PIO values it left;
        skip: the loader must use the record and leave power, reset and strap alone."""
        m = u.Uc(u.UC_ARCH_ARM, u.UC_MODE_ARM)
        m.mem_map(0x20000, SRAM_C[1] - 0x20000)
        m.mem_map(0x01000000, 0x01400000)
        m.mem_map(0x03000000, 0x00100000)
        m.mem_map(0x06500000, 0x00020000)
        m.mem_map(0x07000000, 0x1000)
        m.mem_map(0x40000000, 0x10000000)
        record = record or self.record
        ram = struct.unpack_from("<Q", record, 16)[0]
        PIXELS = 0x40000000 + ram - self.manifest["boot_logo"]["framebuffer_from_ram_end"]
        base = PIXELS & ~4095
        m.mem_map(base, (PIXELS + CAP - base + 4095) & ~4095)
        m.mem_write(0x44000000, b"\xa5" * 0x100000)
        m.mem_write(0x4a000000, self.code)
        m.mem_write(0x45000000, self.image)
        record = record or self.record
        m.mem_write(RECORD, record)
        m.mem_write(PANEL_RECORD, panel or bytes(96))
        bmp = bytearray(self.bmp)
        descriptor = self.descriptor
        if fault == "no-logo":
            descriptor = bytes(16)
        if fault == "bad-bmp":
            bmp[0] ^= 1
            descriptor = struct.pack("<4I", 0x31474f4c, len(bmp), fnv(bmp), 0)
        m.mem_write(DESC, descriptor)
        m.mem_write(BMP, bytes(bmp))
        m.mem_write(PIXELS, b"\xa5" * CAP)
        m.mem_write(DISPLAY, bytes(80))
        sid = (int(self.serial[:3], 16) << 20, int(self.serial[3:11], 16), int(self.serial[11:], 16))
        for offset, value in zip((0x204, 0x208, 0x20c), sid):
            m.mem_write(0x03006000 + offset, struct.pack("<I", value))
        initial = {0x03000000: 0xf1234567, 0x03000004: 0x89abcdef, CFG: 0x22345678, DAT: 0xa5a5e7a5,
                   PE_CFG: 0x77777777, PE_PULL: 0, 0x030017bc: 0xa5a55a5a, 0x030f0030: 0xc369a59a}
        initial.update(pio or {})
        for address, value in initial.items():
            m.mem_write(address, struct.pack("<I", value))
        width, height = self.manifest["boot_logo"]["width"], self.manifest["boot_logo"]["height"]
        pixel_end = PIXELS + width * height * 4
        loaded = struct.unpack_from("<Q", record, 40)[0]
        state = dict(clock=loaded + TICK, seq=0, released=None, active=None, queue=[], clean=[], clean_start=None,
                     last_pixel=0, last_dsb=0, first_display_write=None, scanlines=[], frame=None, frames=[],
                     timeline=[], gpio=[], wait=None, sysctrl=[], sram_c=[], calls=[], wfi=False, result=None,
                     phases=[], unexpected=[], publish=None, deferred=None, last_read=None, pio_other=[],
                     lz4_steps=0, lz4_before_lit=0, lit=False, deferred_checked=[])
        cp = {(0, 1, 0, 0): 0x30c00820, (1, 0, 0, 1): 0x02000023, (1, 0, 0, 0): 2 | (3 << 3) | (7 << 13),
              (0, 0, 0, 1): (line.bit_length() - 3) << 16}
        polls = [op for phase in PHASES for op in self.model[phase] if op["kind"] == "poll"]
        failed_poll = polls[0] if fault == "poll-timeout" and polls else None
        S = state

        def step():
            S["seq"] += 1
            return S["seq"]

        def get(address):
            return struct.unpack("<I", m.mem_read(address, 4))[0]

        def sram_access(uc, access, address, size, value, data):
            S["sram_c"].append([hex(address), access])

        def due():
            # A deferred vendor wait must have fully elapsed before the next display access.
            if S["deferred"]:
                since, microseconds = S["deferred"]
                assert S["last_read"] is not None and S["last_read"] - since >= microseconds * 24, \
                    ("deferred wait cut short", microseconds, since, S["last_read"])
                S["deferred_checked"].append(microseconds)
                S["deferred"] = None

        def mmio_read(uc, access, address, size, value, data):
            step()
            if 0x01000000 <= address < 0x02400000 or 0x06510000 <= address < 0x06512000 or address in (CFG, DAT):
                due()
            old = get(address)
            if address == SCANLINE:
                count = 31 if fault == "scanline-stuck" else 31 + len(S["scanlines"])
                old = old & ~0x0fff0000 | (count & 0xfff) << 16
                S["scanlines"].append(count)
            elif address == STRAP:
                old = old & ~0x40 | (0x40 if high else 0)
            elif 0x03001000 <= address < 0x03001100:
                old |= 0x90000000
            for op in polls:
                if address == op["address"] and address not in (0x030017bc, 0x030f0030):
                    old = old & ~op["clear_mask"] | op["set_bits"]
                if op is failed_poll and address == op["address"]:
                    bit = op["clear_mask"] & -op["clear_mask"]
                    old = old & ~op["clear_mask"] | (op["set_bits"] ^ bit)
            uc.mem_write(address, struct.pack("<I", old))

        def mmio_write(uc, access, address, size, value, data):
            seq = step()
            if address not in (0x03000000, 0x03000004):
                due()
            assert size == 4, ("MMIO width", hex(address), size)
            old = get(address)
            if S.get("replayed") == (address, value):
                del S["replayed"]  # the store interrupted by the unmap pause executes again
                return
            if address in (0x03000000, 0x03000004):
                mask = 0x7fffffff if address == 0x03000000 else 0x01000000
                assert value == old & ~mask, ("SYSCTRL release changed unrelated bits", hex(address), hex(value))
                S["sysctrl"].append([seq, hex(address), hex(value)])
                if address == 0x03000004 and S["released"] is None:
                    S["released"] = seq
                    S["dsb_before_release"] = S["last_dsb"]
                    # Unmapping inside a hook crashes the JIT; stop and unmap between runs.
                    S["pause"] = True
                    S["replayed"] = (address, value)
                    uc.emu_stop()
                return
            if 0x07000000 <= address < 0x07001000 or 0x04020000 <= address < 0x04021000 or 0x03009000 <= address < 0x0300a000:
                raise AssertionError(("RTC/SD/watchdog write", hex(address)))
            if S["active"]:
                assert S["queue"], ("unexpected write in phase", S["active"], hex(address))
                op = S["queue"].pop(0)
                expected = (old & ~op["clear_mask"]) | op["set_bits"] if op["kind"] == "update" else \
                    op["set_bits"] + (PIXELS if op["kind"] == "framebuffer" else 0)
                assert address == op["address"] and value == expected, (S["active"], hex(address), hex(value), hex(expected))
                if S["first_display_write"] is None:
                    S["first_display_write"] = seq
                    assert S["released"] is not None, "display write before SRAM C release"
                return
            assert address in (CFG, DAT, PE_CFG, PE_PULL), ("unowned MMIO write", hex(address), hex(value))
            if address != DAT:
                S["pio_other"].append(dict(address=address, old=old, value=value))
            if address == DAT:
                assert (old ^ value) & ~0xc700 == 0, "panel DAT write changed unrelated bits"
                S["gpio"].append(dict(ticks=S["clock"], old=old, value=value))
                if S["frame"] is None and not value & 0x100:
                    S["frame"] = []
                if S["frame"] is not None and not old & 0x200 and value & 0x200 and not value & 0x100:
                    S["frame"].append(int(bool(value & 0x400)))
                if S["frame"] is not None and value & 0x100:
                    if S["frame"]:
                        assert len(S["frame"]) == 9, "partial SPI9 frame"
                        byte = sum(bit << (7 - i) for i, bit in enumerate(S["frame"][1:]))
                        S["timeline"].append(["data" if S["frame"][0] else "command", byte])
                    S["frame"] = None

        def pixel_write(uc, access, address, size, value, data):
            seq = step()
            assert S["clean_start"] is None, ("pixel write after cache clean", hex(address))
            assert address + size <= pixel_end, "pixel write outside raster"
            S["last_pixel"] = seq

        def record_write(uc, access, address, size, value, data):
            if address == DISPLAY and size == 4 and value == 0x31505344:
                assert [p["phase"] for p in S["phases"]] == list(PHASES), "ready published before backlight"
                assert len(set(S["scanlines"])) >= 2, "ready published without TCON progress"
                S["publish"] = step()

        def code(uc, address, size, data):
            seq = step()
            if address == self.sym["apply"]:
                due()
                phase = self.tables[uc.reg_read(a.UC_ARM_REG_R0)]
                if phase == "backlight":
                    S["lit"] = True
                assert uc.reg_read(a.UC_ARM_REG_R1) == len(self.model[phase])
                if phase == "backlight":
                    assert len(set(S["scanlines"])) >= 2, "backlight before scanline progress"
                S["active"] = phase
                S["queue"] = [op for op in self.model[phase] if op["kind"] in ("update", "write", "framebuffer")]
                S["phases"].append(dict(phase=phase, return_address=uc.reg_read(a.UC_ARM_REG_LR)))
                watch(uc.reg_read(a.UC_ARM_REG_LR))
                return
            if address == self.sym["wait_us"]:
                S["timeline"].append(["wait_us", uc.reg_read(a.UC_ARM_REG_R0)])
                return
            if address == self.sym["defer_us"]:
                due()  # a stream delay at the very end runs straight into the 50 ms wait
                S["timeline"].append(["wait_us", uc.reg_read(a.UC_ARM_REG_R0)])
                S["deferred"] = (S["clock"], uc.reg_read(a.UC_ARM_REG_R0))
                return
            if S["active"] and address == S["phases"][-1]["return_address"]:
                result = uc.reg_read(a.UC_ARM_REG_R0)
                S["phases"][-1]["returned"] = result
                if result == 0:
                    assert not S["queue"], ("apply skipped semantic writes", S["active"])
                S["active"] = None
            if address == self.sym["baseos_prepare_kernel"]:
                S["prepare_return"] = uc.reg_read(a.UC_ARM_REG_LR)
                watch(S["prepare_return"])
                return
            if address == S.get("prepare_return"):
                S["result"] = uc.reg_read(a.UC_ARM_REG_R0)
                return
            if address == self.sym["baseos_lz4_step"]:
                S["lz4_steps"] += 1
                S["lz4_before_lit"] += not S["lit"]
                if not unpack:
                    # Model decode slices: the real begin() set dst/limit; finish after a fixed count.
                    state = uc.reg_read(a.UC_ARM_REG_R0)
                    dst, out, limit = struct.unpack("<3I", uc.mem_read(state + 12, 12))
                    done = S["lz4_steps"] >= SIMULATED_DECODE_STEPS
                    if done:
                        assert limit - dst == len(self.kernel)
                        uc.mem_write(dst, self.kernel)
                        uc.mem_write(state + 16, struct.pack("<I", limit))
                    uc.reg_write(a.UC_ARM_REG_R0, 0 if done else 1)
                    uc.reg_write(a.UC_ARM_REG_PC, uc.reg_read(a.UC_ARM_REG_LR))
                return
            ins = self.special.get(address)
            if not ins:
                return
            if ins.mnemonic == "dsb":
                S["last_dsb"] = seq
                return
            if ins.mnemonic == "wfi":
                S["wfi"] = True
                uc.emu_stop()
                return
            if ins.mnemonic == "smc":
                S["calls"].append([uc.reg_read(a.UC_ARM_REG_R0 + i) for i in range(4)])
                uc.emu_stop()
                return
            if ins.mnemonic == "mrrc":
                uc.reg_write(a.UC_ARM_REG_R0, S["clock"] & 0xffffffff)
                uc.reg_write(a.UC_ARM_REG_R1, S["clock"] >> 32)
                S["last_read"] = S["clock"]
                S["clock"] += TICK
            else:
                match = re.fullmatch(r"p15, #(\d+), (\w+), c(\d+), c(\d+), #(\d+)", ins.op_str)
                assert match, ins.op_str
                op1, register, crn, crm, op2 = match.groups()
                key = tuple(map(int, (op1, crn, crm, op2)))
                reg = {"sb": a.UC_ARM_REG_R9, "sl": a.UC_ARM_REG_R10, "fp": a.UC_ARM_REG_R11,
                       "ip": a.UC_ARM_REG_R12}.get(register) or getattr(a, "UC_ARM_REG_" + register.upper())
                if ins.mnemonic == "mrc":
                    assert key in cp, ("unmodeled CP15 read", key)
                    uc.reg_write(reg, cp[key])
                elif key == (0, 7, 10, 1):
                    if S["clean_start"] is None:
                        S["clean_start"] = seq
                        assert S["last_pixel"] < seq
                    S["clean"].append(uc.reg_read(reg))
                else:
                    value = uc.reg_read(reg)
                    if key == (0, 1, 0, 0) and value & 1:
                        table = struct.unpack("<4096I", uc.mem_read(0x4a0f8000, 16384))
                        for index, entry in enumerate(table):
                            attributes = 0x1c0e if index == 0 or 0x400 <= index < 0x400 + (ram >> 20) else 0x10c16
                            assert entry == (index << 20) | attributes, ("MMU attributes", hex(index << 20), hex(entry))
                        assert table[PIXELS >> 20] & 0xfffff == 0x1c0e
                    cp[key] = value
            uc.reg_write(a.UC_ARM_REG_PC, address + 4)

        m.hook_add(u.UC_HOOK_MEM_READ | u.UC_HOOK_MEM_WRITE, sram_access, begin=0x20000, end=SRAM_C[1] - 1)
        m.hook_add(u.UC_HOOK_MEM_READ, mmio_read, begin=0x01000000, end=0x07ffffff)
        m.hook_add(u.UC_HOOK_MEM_WRITE, mmio_write, begin=0x01000000, end=0x07ffffff)
        m.hook_add(u.UC_HOOK_MEM_WRITE, pixel_write, begin=PIXELS, end=PIXELS + CAP - 1)
        m.hook_add(u.UC_HOOK_MEM_WRITE, record_write, begin=DISPLAY, end=DISPLAY + 79)
        watched = set()

        def watch(address):
            if address not in watched:
                watched.add(address)
                m.hook_add(u.UC_HOOK_CODE, code, begin=address, end=address)

        for address in set(self.special) | {self.sym[n] for n in ("apply", "wait_us", "defer_us", "baseos_prepare_kernel",
                                                                 "baseos_lz4_step")}:
            watch(address)
        m.reg_write(a.UC_ARM_REG_CPSR, 0x1d3)
        start = 0x4a000000
        while True:
            m.emu_start(start, 0x50000000, count=3000000000 if unpack else 600000000)
            if not S.pop("pause", False):
                break
            m.mem_unmap(SRAM_C[0], SRAM_C[1] - SRAM_C[0])
            S["unmapped"] = True
            start = m.reg_read(a.UC_ARM_REG_PC)
        assert fault == "bad-bmp" or S.get("unmapped") and get(0x03000004) == 0x88abcdef and get(0x03000000) == 0x80000000, "SYSCTRL state"
        return self.check(name, m, S, high, line, fault, bytes(bmp), descriptor, width, height, pixel_end, panel, unpack, skip)

    def check(self, name, m, S, high, line, fault, bmp, descriptor, width, height, pixel_end, spl_panel, unpack, fast):
        PIXELS = pixel_end - width * height * 4
        record = bytes(m.mem_read(DISPLAY, 80))
        assert not S["sram_c"], ("loader touched SRAM C", S["sram_c"][:4])
        rule, contract = self.manifest["panel_rule"], self.manifest["panel_contract"]
        row = dict(name=name, pe6="high" if high else "low", ctr_line_bytes=line, fault=fault,
                   prepare_kernel_result=S["result"], sram_c_accesses=0, sysctrl_writes=S["sysctrl"],
                   spl_panel_record=bool(spl_panel), decode_steps=S["lz4_steps"], real_decode=unpack)
        # The loader may skip the panel steps only for a valid record whose pins still match.
        touched = [g for g in S["gpio"] if (g["old"] ^ g["value"]) & 0xc000] + \
                  [w for w in S["pio_other"] if w["address"] in (PE_CFG, PE_PULL) or (w["old"] ^ w["value"]) & 0xff000000]
        row["panel_steps_in_loader"] = bool(touched) or any(e == ["wait_us", 30000] for e in S["timeline"])
        if fast:
            assert spl_panel and not touched, ("loader redid SPL panel steps", name, touched[:3])
        if fault and fault != "no-logo":
            assert S["result"] == 14 and S["wfi"] and not S["calls"], ("display failure must stop in wfi", S["result"])
            assert record == bytes(80) and S["publish"] is None
            row.update(fail_closed=True, wfi=True)
            return row
        panel = int(contract["high" if high else "low"] == "boe") if rule == 2 else rule
        assert S["result"] == 0 and S["calls"] == [[0x8000ff04, 0x40080000, 0x44000000, 1]], (S["result"], S["calls"])
        assert bytes(m.mem_read(0x40080000, len(self.kernel))) == self.kernel
        assert [p["phase"] for p in S["phases"]] == list(PHASES) and all(p["returned"] == 0 for p in S["phases"])
        assert S["clean"] == list(range(PIXELS, pixel_end, line)), "DCCMVAC coverage differs"
        assert S["clean_start"] < S["dsb_before_release"] and S["released"] < S["first_display_write"], "clean/DSB/release order"
        assert [w[1] for w in S["sysctrl"]] == ["0x3000000", "0x3000004"] * 2, S["sysctrl"]
        pixels = bytes(m.mem_read(PIXELS, pixel_end - PIXELS))
        assert pixels == (bytes(width * height * 4) if fault == "no-logo" else V.logo_pixels(bmp, width, height)), "pixels differ from independent BMP conversion"
        assert bytes(m.mem_read(pixel_end, PIXELS + CAP - pixel_end)) == b"\xa5" * (PIXELS + CAP - pixel_end)
        if not fast:
            moments = [g["ticks"] for g in S["gpio"][:4]]
            for i, delay in enumerate((30000, 15000, 50000)):
                assert moments[i + 1] - moments[i] >= delay * 24, "panel power/reset delay short"
        stream = self.streams[panel]
        expected = [["wait_us", op["set_bits"]] for op in self.model["prepare"] if op["kind"] == "delay_us"]
        if not fast:
            expected += [["wait_us", v] for v in (30000, 15000, 50000, 100000)]
        expected += [["wait_us", (op & 0x7fff) * 1000] if op & 0x8000 else ["data" if op & 0x100 else "command", op & 255]
                     for op in stream]
        expected += [["wait_us", 50000]]
        for phase in ("publish", "enable"):
            expected += [["wait_us", op["set_bits"]] for op in self.model[phase] if op["kind"] == "delay_us"]
        expected += [["wait_us", 100000]]
        expected += [["wait_us", op["set_bits"]] for op in self.model["backlight"] if op["kind"] == "delay_us"]
        assert S["timeline"] == expected, "SPI9 frames/delays differ from vendor stream and phase contract"
        magic, version, size, digest, chosen, logo_bytes, logo_fnv, pixel_fnv = struct.unpack_from("<8I", record)
        shown = struct.unpack_from("<Q", record, 64)[0]
        assert (magic, version, size, chosen, logo_bytes, logo_fnv, pixel_fnv) == (
            0x31505344, 1, 80, panel, 0 if fault == "no-logo" else len(bmp), 0 if fault == "no-logo" else fnv(bmp), fnv(pixels))
        assert digest == fnv(record, (12, 16)) and record[32:64] == bytes.fromhex(self.manifest["generation"])
        assert record[72:] == bytes(8)
        tree = bytes(m.mem_read(0x44000000, 0x100000))
        props = {(p, n): v for p, n, _, v in fdt.walk(tree)}
        display = "/soc@03000000/disp@01000000"
        handoff = self.manifest["early_display"]["handoff"]
        for key, value in handoff.items():
            assert props[display, key] == struct.pack(">I", value), key
        assert props["/chosen", "baseos,boot-display"] == record
        columns, stored_rows = struct.unpack_from("<ii", bmp, 18)
        rows = abs(stored_rows)
        left, top = (width - columns) // 2, (height - rows) // 2
        fields = (PIXELS, width, height, 32, width * 4, left, top, left + columns, top + rows)
        if fault == "no-logo":
            assert (display, "boot_fb0") not in props
        else:
            assert props[display, "boot_fb0"] == ",".join(f"{v:x}" for v in fields).encode() + b"\0"
        assert not any(base < pixel_end and PIXELS < base + size for base, size in V.fdt_reservations(tree))
        assert f"disp_reserve={width * height * 4},0x{PIXELS:x}".encode() in props["/chosen", "bootargs"].split()
        stamps = struct.unpack(">7Q", props["/chosen", "baseos,native-timing"])
        # The logo is lit while the kernel decodes: after the image load, before the FDT is finished.
        assert list(stamps) == sorted(stamps) and stamps[2] <= shown <= stamps[5], (stamps, shown)
        assert S["lz4_before_lit"] >= 1 and stamps[3] <= shown, "kernel decode did not overlap the display waits"
        assert S["deferred"] is None and len(S["deferred_checked"]) == \
            sum(1 for op in stream if op & 0x8000) + 2, ("deferred waits not all honoured", S["deferred_checked"])
        command = props["/chosen", "bootargs"].rstrip(b"\0").split()
        assert [t for t in command if t.startswith(b"lcd_type=")] == [b"lcd_type=" + (b"boe" if panel else b"old")]
        row.update(decode_steps_before_backlight=S["lz4_before_lit"], deferred_waits_us=S["deferred_checked"],
                   panel_steps_skipped=fast)
        row.update(panel="boe" if panel else "old", passed=True, kernel_entry=True, record_sha256=sha(record),
                   shown_ticks=shown, native_timing=list(stamps), cvac_lines=len(S["clean"]),
                   spi_frames=sum(1 for e in S["timeline"] if e[0] != "wait_us"),
                   distinct_scanlines=len(set(S["scanlines"])), fdt_sha256=sha(tree), pixels_sha256=sha(pixels))
        row["_artifacts"] = dict(record=record, tree=tree, pixels=pixels, bmp=bmp)
        return row


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("native", type=Path)
    parser.add_argument("--spl-proof", type=Path, help="SPL logo proof dir with valid-native-record.bin, bmp/descriptor")
    parser.add_argument("--inventory", type=Path, help="without --spl-proof: boot0 DRAM words for a modeled record")
    parser.add_argument("--bmp", type=Path, help="without --spl-proof: BMP (default work/<target>/bootlogo.bmp)")
    parser.add_argument("--serial", default="1c00103dc2840671ed3")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--real-unpack", action="store_true", help="decompress the kernel in the first valid case")
    args = parser.parse_args()
    native = args.native.resolve()
    assert not args.output.exists(), "use a fresh output directory"
    manifest = json.loads((native / "manifest.json").read_text())
    target = manifest["target"]
    engine = json.loads((HERE / "display-engine.json").read_text())["models"][target]
    panel_init = json.loads((HERE / "panel-init.json").read_text())
    names = panel_init["models"][target]
    fallback = names["old"] or names["boe"]
    streams = [panel_init["streams"][names["old"] or fallback], panel_init["streams"][names["boe"] or fallback]]
    if args.spl_proof:
        record = (args.spl_proof / "valid-native-record.bin").read_bytes()
        bmp = (args.spl_proof / "valid-bmp-from-spl.bin").read_bytes()
        descriptor = (args.spl_proof / "valid-descriptor-from-spl.bin").read_bytes()
        source = "SPL emulation outputs"
    else:
        bmp = (args.bmp or ROOT / "work" / target / "bootlogo.bmp").read_bytes()
        descriptor = struct.pack("<4I", 0x31474f4c, len(bmp), fnv(bmp), 0)
        words = list(struct.unpack_from("<32I", (args.inventory / "boot0.bin").read_bytes(), 0x38))
        record = bytearray(struct.pack("<4I4QI32I8s3I32s2I", 0x314e5342, 2, 240, manifest["model_id"],
                                       (words[7] >> 16) << 20, 12000000, 12100000, 23000000, words[1], *words, bytes(8),
                                       manifest["image_bytes"], manifest["image_fnv"], 0,
                                       bytes.fromhex(manifest["generation"]), 5, 0))
        struct.pack_into("<I", record, 196, fnv(record))
        record = bytes(record)
        source = "modeled native record from inventory boot0 and " + ("given BMP" if args.bmp else "work/<target>/bootlogo.bmp")
    assert len(record) == 240 and struct.unpack_from("<I", descriptor)[0] == 0x31474f4c
    runner = Run(native, engine, streams, record, bmp, descriptor, args.serial)
    rule, contract = manifest["panel_rule"], manifest["panel_contract"]

    def strap_panel(high):
        return int(contract["high" if high else "low"] == "boe") if rule == 2 else rule

    def panel_record(panel, ticks, version=1, generation=None, rule_field=None):
        raw = bytearray(struct.pack("<6I5Q32s", 0x314c4e50, version, 96, 0, panel,
                                    rule if rule_field is None else rule_field, *ticks,
                                    generation or bytes.fromhex(manifest["generation"])))
        struct.pack_into("<I", raw, 12, fnv(raw, (12, 16)))
        return bytes(raw)

    if args.spl_proof:
        panels = {}
        for high, prefix in ((False, "valid"), (True, "valid-pe6-high")):
            pio = {int(k, 16): v for k, v in json.loads((args.spl_proof / (prefix + "-pio-state.json")).read_text()).items()}
            panels[high] = ((args.spl_proof / (prefix + "-native-panel.bin")).read_bytes(), pio,
                            (args.spl_proof / (prefix + "-native-record.bin")).read_bytes())
            assert struct.unpack_from("<I", panels[high][0], 16)[0] == strap_panel(high)
        panel_source = "SPL emulation panel records and final PIO state"
    else:
        started = struct.unpack_from("<Q", record, 24)[0]
        ticks, at = [], started + 500000
        for wait in (0,) + WAITS_MS:
            at += wait * TICK
            ticks.append(at)  # exact minimum gaps: the boundary must be accepted
        panels = {}
        for high in (False, True):
            pio = {CFG: 0x11345678, DAT: 0xa5a5e7a5,
                   PE_CFG: 0x76777777 if rule == 2 else 0x77777777,
                   PE_PULL: 0x1000 if rule == 2 and manifest["panel_pull_up"] else 0}
            panels[high] = (panel_record(strap_panel(high), ticks), pio, record)
        panel_source = "modeled panel record at the exact minimum waits"

    def fast(name, high, **kw):
        good, pio, own = panels[high]
        return runner.run(name, high=high, panel=good, pio=pio, record=own, skip=True, **kw)

    def refused(name, panel=None, pio=None):
        good, base_pio, own = panels[False]
        row = runner.run(name, high=False, panel=panel or good, pio={**base_pio, **(pio or {})}, record=own)
        assert row["panel_steps_in_loader"], ("refused record still skipped the panel steps", name)
        row["refused_panel_record"] = True
        return row

    good = panels[False][0]
    ticks = list(struct.unpack_from("<5Q", good, 24))
    started = struct.unpack_from("<Q", panels[False][2], 24)[0]
    loaded = struct.unpack_from("<Q", panels[False][2], 40)[0]
    panel = struct.unpack_from("<I", good, 16)[0]
    short = list(ticks)
    short[3] = short[2] + WAITS_MS[2] * TICK - 1
    short[4] = max(short[4], short[3] + WAITS_MS[3] * TICK)
    damaged = bytearray(good)
    damaged[12] ^= 1
    base_dat, base_cfg = panels[False][1][DAT], panels[False][1][CFG]
    cases = [fast("spl-panel-low-64", False, unpack=args.real_unpack),
             fast("spl-panel-high-64", True),
             fast("spl-panel-high-16", True, line=16),
             runner.run("fallback-high-64", high=True),
             runner.run("fallback-low-64", high=False),
             refused("refuse-wait-short", panel=panel_record(panel, short)),
             refused("refuse-checksum", panel=bytes(damaged)),
             refused("refuse-version", panel=panel_record(panel, ticks, version=2)),
             refused("refuse-generation", panel=panel_record(panel, ticks, generation=bytes(32))),
             refused("refuse-rule", panel=panel_record(panel, ticks, rule_field=(rule + 1) % 3)),
             refused("refuse-before-spl-start", panel=panel_record(panel, [started - 1] + ticks[1:])),
             refused("refuse-not-finished", panel=panel_record(panel, ticks[:4] + [loaded + (1 << 40)])),
             refused("refuse-power-off", pio={DAT: base_dat & ~0x8000}),
             refused("refuse-in-reset", pio={DAT: base_dat & ~0x4000}),
             refused("refuse-pins-not-output", pio={CFG: base_cfg | 0x77000000})]
    if rule == 2:
        cases.append(refused("refuse-strap-not-input", pio={PE_CFG: panels[False][1][PE_CFG] | 0x0f000000}))
        if manifest["panel_pull_up"]:
            cases.append(refused("refuse-strap-pull", pio={PE_PULL: panels[False][1][PE_PULL] & ~0x3000}))
        if strap_panel(False) != strap_panel(True):
            cases.append(refused("refuse-strap-differs", panel=panel_record(1 - panel, ticks)))
    cases += [fast("no-logo-cold-spl-panel", False, fault="no-logo"),
              runner.run("no-logo-cold", fault="no-logo"),
              fast("malformed-bmp-cold-spl-panel", False, fault="bad-bmp"),
              fast("scanline-stuck", False, fault="scanline-stuck")]
    if any(op["kind"] == "poll" for phase in PHASES for op in engine[phase]):
        cases.append(fast("poll-timeout", False, fault="poll-timeout"))
    args.output.mkdir(parents=True)
    first = cases[0].pop("_artifacts")
    for row in cases:
        row.pop("_artifacts", None)
        row["passed"] = True
    (args.output / "verified-early-logo-kernel.dtb").write_bytes(first["tree"])
    (args.output / "early-display-record.bin").write_bytes(first["record"])
    (args.output / "early-logo-pixels.bin").write_bytes(first["pixels"])
    (args.output / "early-logo-source.bmp").write_bytes(first["bmp"])
    report = dict(passed=True, target=target, generation=manifest["generation"], loader_sha256=manifest["loader_sha256"],
                  verifier_sha256=sha(Path(__file__).read_bytes()), input_source=source, case_count=len(cases),
                  cases=cases, sram_c_untouched_after_release=True, real_kernel_unpack=args.real_unpack,
                  panel_record_source=panel_source,
                  spl_panel_skip_cases=sum(1 for row in cases if row.get("spl_panel_record") and not row.get("panel_steps_in_loader")),
                  refused_panel_record_cases=sum(1 for row in cases if row.get("refused_panel_record")),
                  decode_overlaps_display_waits=all(row.get("decode_steps_before_backlight", 1) >= 1 for row in cases),
                  scope="actual linked A32 loader from 0x4a000000 to the kernel SMC; modeled CNTPCT/CTR/CP15, polls, "
                        "TCON line counter and PE6 strap; SRAM C unmapped at release; decode slices modeled except "
                        "in the first case",
                  limitations=["No electrical/panel timing, PLL lock or coherency proof; DCCMVAC is a recorded event.",
                               "Counter-stall loop bound not executed (100M-iteration guard)."],
                  device_access=False, hardware_qualified=False)
    (args.output / "verification.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(dict(passed=True, target=target, cases=len(cases), output=str(args.output))))


if __name__ == "__main__":
    main()
