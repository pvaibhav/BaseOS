#!/usr/bin/env python3
"""Execute candidate retries, ARM stack unwind and runtime timing dispatch.

Training success and detected geometry are modeled, and DRAM is a small model
that can alias (real size smaller than detected) or drop every write. Actual
upstream rank/width retry, timeout unwind adapter, the layout guard, the memory
check and timing register writes execute from SRAM.
"""
import argparse
import hashlib
import json
import re
import struct
from pathlib import Path

import unicorn as u
from unicorn import arm_const, arm64_const


DRAM_BASE = 0x40000000
PAGE = 0x1000


class Dram:
    """DRAM behind MMIO pages: addresses wrap at the real size; broken drops writes."""

    def __init__(self, uc):
        self.uc, self.words, self.pages = uc, {}, set()
        self.real, self.broken, self.accessed = 1 << 30, False, set()

    def page(self, address):
        page = address & ~(PAGE - 1)
        if page in self.pages: return
        self.pages.add(page)
        def read(uc, offset, size, user):
            address = page + offset
            self.accessed.add(page)
            if self.broken: return 0
            return sum(self.words.get((address + i - DRAM_BASE) % self.real, 0) << (8 * i) for i in range(size))
        def write(uc, offset, size, value, user):
            address = page + offset
            self.accessed.add(page)
            if self.broken: return
            for i in range(size): self.words[(address + i - DRAM_BASE) % self.real] = value >> (8 * i) & 0xff
        self.uc.mmio_map(page, PAGE, read, None, write, None)

    def load(self, address, blob):
        for i, byte in enumerate(blob): self.words[(address + i - DRAM_BASE) % self.real] = byte

    def dump(self, address, size):
        return bytes(self.words.get((address + i - DRAM_BASE) % self.real, 0) for i in range(size))


def layout_size(cols, rows, ranks, full):
    return (1 << (cols + rows + 3)) * (4 if full else 2) * ranks


def verify(directory):
    manifest = json.loads((directory / "manifest.json").read_text())
    native = manifest.get("native_dram", False)
    blob = (directory / ("spl.bin" if native else "probe.bin")).read_bytes()
    sha_key = "spl_sha256" if native else "probe_sha256"
    assert (native or manifest["autodetect"]) and hashlib.sha256(blob).hexdigest() == manifest[sha_key]
    if native:
        symbols = {name: int(address,16) for address,name in re.findall(
            r"^([0-9a-f]+) [a-zA-Z] (\w+)$", (directory / "symbols.txt").read_text(), re.M)}
        arch, R0, PC, SP, LR = u.UC_ARCH_ARM64, arm64_const.UC_ARM64_REG_X0, arm64_const.UC_ARM64_REG_PC, arm64_const.UC_ARM64_REG_SP, arm64_const.UC_ARM64_REG_X30
    else:
        symbols = {name: int(address, 16) for address, name in re.findall(
        r"^\s*([0-9a-f]+)\s+[0-9a-f]+\s+[0-9a-f]+\s+\d+\s+(\w+)\s*$",
        (directory / "probe.map").read_text(), re.M)}
        arch, R0, PC, SP, LR = u.UC_ARCH_ARM, arm_const.UC_ARM_REG_R0, arm_const.UC_ARM_REG_PC, arm_const.UC_ARM_REG_SP, arm_const.UC_ARM_REG_LR
    cases = []
    return_address, stack, params = (0x55ff0, 0x52a00, 0x53000) if native else (0x27ff0, 0x27800, 0x27000)

    def machine():
        m = u.Uc(arch, u.UC_MODE_ARM)
        m.mem_map(0x20000, 0x38000 if native else 0x8000)
        m.mem_map(0x03000000, 0x1000)
        m.mem_map(0x07010000, 0x1000)
        m.mem_map(0x047fb000, 0x2000)
        m.mem_write(0x20000, blob)
        if not native: m.reg_write(arm_const.UC_ARM_REG_CPSR, 0x13)
        m.reg_write(SP, stack)
        m.reg_write(LR, return_address)
        if native and "baseos_dram_power" in symbols:
            m.mem_write(symbols["baseos_dram_power"], struct.pack("<I", 1))
        m.dram = Dram(m)
        for size in (512 << 20, 1 << 30, 2 << 30):
            m.dram.page(DRAM_BASE)
            m.dram.page(DRAM_BASE + size // 2)
        return m

    def read_word(m, address): return struct.unpack("<I", m.mem_read(address, 4))[0]

    for working_type in sorted(set(manifest["candidate_types"])) + [0]:
        for timeout in (False, True):
            m = machine()
            attempted, outcome, voltages = [], [], []
            def return_value(value):
                m.reg_write(R0, value)
                m.reg_write(PC, m.reg_read(LR))
            def code(uc, address, size, user):
                if address == symbols["mctl_core_init"]:
                    param = uc.reg_read(R0)
                    kind = read_word(uc, param + 4)
                    attempted.append(kind)
                    if timeout and kind != working_type:
                        # The same non-local exit is used by upstream's timeout panic.
                        uc.reg_write(PC, symbols["dram_panic"])
                    else: return_value(int(kind == working_type))
                elif address == symbols["mctl_auto_detect_dram_size"]:
                    uc.mem_write(uc.reg_read(R0 + 1), bytes((10, 15, 1, 1)))
                    return_value(0)
                elif address == symbols["mctl_calc_size"]: return_value(0x40000000)
                elif address == symbols["baseos_set_master_priority"]: return_value(0)
                elif native and address == symbols.get("axp_set_dcdc3"):
                    voltages.append(uc.reg_read(R0))
                    return_value(0)
                elif native and address == symbols.get("udelay"): return_value(0)
                elif address in (symbols["panic"], return_address):
                    outcome.append("failed-all" if address == symbols["panic"] else "selected")
                    uc.emu_stop()
            m.hook_add(u.UC_HOOK_CODE, code)
            m.emu_start(symbols["sunxi_dram_init"], 0x58000 if native else 0x28000, count=100000)
            assert outcome == (["selected"] if working_type else ["failed-all"]), attempted
            tries = read_word(m, symbols["dram_probe_attempts"])
            expected = manifest["candidate_types"].index(working_type) + 1 if working_type else len(manifest["candidate_types"])
            assert tries == expected
            assert read_word(m, symbols["dram_probe_selected_type"]) == working_type
            if working_type:
                assert m.reg_read(SP) == stack and m.reg_read(R0) == 0x40000000
                if native and "trained_vendor_params" in symbols:
                    words = struct.unpack("<32I", m.mem_read(symbols["trained_vendor_params"], 128))
                    assert words[1] == working_type and words[6:8] == (0x30fa, 0x04000000)
            if native and "baseos_dram_power" in symbols:
                assert voltages == [{3: 1500, 7: 1200, 8: 1100}[kind] for kind in manifest["candidate_types"][:tries]]
            cases.append({"working_type": working_type, "timeout_exit": timeout, "attempts": tries,
                          "core_calls": attempted, "outcome": outcome[0], "modeled_voltage_writes_mv": voltages})

    # Layout guard and memory check: every candidate trains; geometry and DRAM
    # behaviour are scripted per candidate. A rejected layout must never reach
    # mctl_calc_size, and the selection must fall through to the next bank.
    count = len(manifest["candidate_types"])
    good = (10, 15, 1, 1)  # 1 GiB, the RG28XX/RG40XX H layout 0x30fa/0x04000000
    scenarios = {
        "nothing-aliased-32bit": [dict(layout=(11, 17, 1, 1)), dict(layout=good)],
        "nothing-aliased-16bit": [dict(layout=(11, 17, 1, 0)), dict(layout=good)],
        "4GiB-outside-window": [dict(layout=(10, 17, 1, 1)), dict(layout=good)],
        "2GiB-accepted": [dict(layout=(10, 16, 1, 1), real=2 << 30)],
        "2GiB-two-ranks-accepted": [dict(layout=(10, 15, 2, 1), real=2 << 30)],
        "size-too-large-aliases": [dict(layout=good, real=512 << 20), dict(layout=good)],
        "data-path-broken": [dict(layout=good, broken=True), dict(layout=good)],
        "every-bank-nothing-aliased": [dict(layout=(11, 17, 1, 1))] * count,
    }
    guard_cases = []
    for name, script in scenarios.items():
        if len(script) > count: continue
        m = machine()
        sizes, outcome, current = [], [], {"index": -1}
        saved = bytes(range(16)) + bytes(range(0xf0, 0x100))
        def apply(index):
            step = script[index] if index < len(script) else dict(layout=good)
            m.dram.real = step.get("real", 1 << 30)
            m.dram.broken = step.get("broken", False)
            return step
        def code(uc, address, size, user):
            if address == symbols["mctl_core_init"]:
                index = (uc.reg_read(R0) - symbols["dram_candidates"]) // 48
                if index != current["index"]:
                    current["index"] = index
                    apply(index)
                    m.dram.load(DRAM_BASE, saved)
                return_value(1)
            elif address == symbols["mctl_auto_detect_dram_size"]:
                step = apply(current["index"])
                cols, rows, ranks, full = step["layout"]
                uc.mem_write(uc.reg_read(R0 + 1), bytes((cols, rows, ranks, full)))
                return_value(0)
            elif address == symbols["mctl_calc_size"]:
                cols, rows, ranks, full = uc.mem_read(uc.reg_read(R0), 4)
                sizes.append((current["index"], layout_size(cols, rows, ranks, full)))
                return_value(layout_size(cols, rows, ranks, full))
            elif address == symbols["baseos_set_master_priority"]: return_value(0)
            elif native and address == symbols.get("axp_set_dcdc3"): return_value(0)
            elif native and address == symbols.get("udelay"): return_value(0)
            elif address in (symbols["panic"], return_address):
                outcome.append("failed-all" if address == symbols["panic"] else "selected")
                uc.emu_stop()
        def return_value(value):
            m.reg_write(R0, value)
            m.reg_write(PC, m.reg_read(LR))
        m.hook_add(u.UC_HOOK_CODE, code)
        m.emu_start(symbols["sunxi_dram_init"], 0x58000 if native else 0x28000, count=200000)
        expect = next((i for i, step in enumerate(script) if layout_size(*step["layout"]) <= 2 << 30
                       and not step.get("broken") and step.get("real", 1 << 30) >= layout_size(*step["layout"])), None)
        if expect is None and len(script) < count: expect = len(script)
        assert outcome == (["selected"] if expect is not None else ["failed-all"]), (name, outcome)
        # A layout outside the window is refused before mctl_calc_size probes it.
        probed = [index for index, _ in sizes]
        assert all(layout_size(*script[i]["layout"]) <= 2 << 30 for i in probed if i < len(script)), (name, sizes)
        tries = read_word(m, symbols["dram_probe_attempts"])
        if expect is not None:
            assert tries == expect + 1, (name, tries)
            selected_size = layout_size(*(script[expect]["layout"] if expect < len(script) else good))
            assert m.reg_read(R0) == selected_size and m.reg_read(SP) == stack, name
            # The memory check puts back what it touched.
            assert m.dram.dump(DRAM_BASE, 32) == saved, name
            if native and "trained_vendor_params" in symbols:
                words = struct.unpack("<32I", m.mem_read(symbols["trained_vendor_params"], 128))
                cols, rows, ranks, full = script[expect]["layout"] if expect < len(script) else good
                assert words[6] == (3 << 12 | rows << 4 | cols), (name, hex(words[6]))
                assert words[7] == (selected_size >> 20 << 16 | (0x1000 if ranks == 2 else 0) | (0 if full else 1)), name
        else:
            assert tries == count and not sizes, (name, tries, sizes)
        guard_cases.append({"case": name, "outcome": outcome[0], "attempts": tries,
                            "selected": expect, "size_probes": [[i, hex(size)] for i, size in sizes]})

    # Execute the real timing register writes, including the mixed-clock RG28XX case.
    timings = []
    for kind in (3, 7, 8):
        for clock in (672, 792):
            m = machine()
            m.mem_write(params, struct.pack("<12I", clock, kind, *([0] * 10)))
            m.reg_write(R0, params)
            visited = []
            def trace(uc, address, size, user):
                for label in ("ddr3", "lpddr3", "lpddr4"):
                    if address == symbols["timing_" + label]: visited.append(label)
                if address == return_address: uc.emu_stop()
            m.hook_add(u.UC_HOOK_CODE, trace)
            m.emu_start(symbols["mctl_set_timing_params"], 0x58000 if native else 0x28000, count=10000)
            assert visited == [{3: "ddr3", 7: "lpddr3", 8: "lpddr4"}[kind]]
            refresh = read_word(m, 0x047fb000 + 0x64)
            # The high half is refresh interval; each upstream timing uses its own division.
            nanoseconds, divisor = {3: (7800, 32), 7: (7800, 64), 8: (3904, 32)}[kind]
            expected = ((nanoseconds * (clock // 2) + 999) // 1000) // divisor
            assert refresh >> 16 == expected, (kind, clock, hex(refresh), expected)
            timings.append({"type": kind, "clock_mhz": clock, "rfshtmg": hex(refresh)})
    report = {sha_key: manifest[sha_key], "native_aarch64": native, "training_modeled": True,
              "geometry_modeled": True, "retry_cases": cases, "guard_cases": guard_cases,
              "timing_cases": timings}
    (directory / "selection-verification.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    verify(parser.parse_args().directory)
