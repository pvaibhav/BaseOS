#!/usr/bin/env python3
"""Execute the FDT marker insertion on all eleven real trees and compare every property."""
import argparse
import json
import re
import struct
import sys
from pathlib import Path

import unicorn as u
from unicorn import arm64_const as a

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE.parent))
import fdt

TARGETS = ("rg28xx", "rg34xx", "rg34xxsp", "rg35xxh", "rg35xxplus", "rg35xxpro",
           "rg35xxsp", "rg40xxh", "rg40xxv", "rgcubexx", "rgsp")


def verify(directory):
    symbols = {name: int(address,16) for address,name in re.findall(
        r"^([0-9a-f]+) [a-zA-Z] (\w+)$", (directory / "symbols.txt").read_text(), re.M)}
    blob = (directory / "spl.bin").read_bytes()
    cases = []
    for target in TARGETS:
        package = (ROOT / "work" / target / "boot-gzip/boot-package.bin").read_bytes()
        raw = package[0x11b800:]
        total = fdt.total_size(raw)
        tree = raw[:total]
        m = u.Uc(u.UC_ARCH_ARM64, u.UC_MODE_ARM)
        m.mem_map(0x20000, 0x38000)
        m.mem_map(0x4a200000, 0x40000)
        m.mem_write(0x20000, blob)
        m.mem_write(0x4a200000, tree)
        payload = struct.pack("<4Q", 0x313030764c505342, 24000000, 0x80000000, 8)
        m.mem_write(0x53000, payload)
        m.hook_add(u.UC_HOOK_CODE, lambda machine,*_: machine.emu_stop(), begin=0x55ff0, end=0x55ff0)
        def stores(machine, access, address, size, value, data):
            assert size == 1 and 0x4a200000 <= address < 0x4a200000 + total
        m.hook_add(u.UC_HOOK_MEM_WRITE, stores, begin=0x4a200000, end=0x4a23ffff)
        def call():
            m.reg_write(a.UC_ARM64_REG_SP, 0x52a00)
            m.reg_write(a.UC_ARM64_REG_X30, 0x55ff0)
            m.reg_write(a.UC_ARM64_REG_X0, 0x4a200000)
            m.reg_write(a.UC_ARM64_REG_X1, 0x53000)
            m.emu_start(symbols["baseos_stamp_fdt"], 0x58000, count=5000000)
            return m.reg_read(a.UC_ARM64_REG_X0)
        assert call() == 0
        result = bytes(m.mem_read(0x4a200000, total))
        before = {(path,name): value for path,name,_,value in fdt.walk(tree)}
        after = {(path,name): value for path,name,_,value in fdt.walk(result)}
        assert after == before | {("/chosen", "baseos,spl"): payload}
        assert call() == 0 and bytes(m.mem_read(0x4a200000, total)) == result
        # The same input without free space must fail without changing bytes.
        damaged = bytearray(tree)
        strings, length = struct.unpack_from(">I", tree, 12)[0], struct.unpack_from(">I", tree, 32)[0]
        struct.pack_into(">I", damaged, 4, strings + length)
        m.mem_write(0x4a200000, bytes(damaged))
        assert call() != 0 and bytes(m.mem_read(0x4a200000, total)) == damaged
        cases.append({"target": target, "properties_preserved": len(before), "replay_exact": True,
                      "insufficient_space_no_write": True})
    report = {"spl_sha256": json.loads((directory / "manifest.json").read_text())["spl_sha256"], "cases": cases}
    (directory / "fdt-verification.json").write_text(json.dumps(report, indent=2) + "\n")
    print("FDT property insertion passed on all eleven models")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    verify(parser.parse_args().directory)
