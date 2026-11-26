#!/usr/bin/env python3
"""Cross-check the linked native geometry encoder against real vendor boot0."""
import argparse
import hashlib
import json
import re
import struct
from pathlib import Path

import unicorn as u
from unicorn import arm_const as a, arm64_const as x


def verify(directory, boot0_paths):
    blob = (directory / "spl.bin").read_bytes()
    manifest = json.loads((directory / "manifest.json").read_text())
    assert hashlib.sha256(blob).hexdigest() == manifest["spl_sha256"]
    symbols = {name: int(address, 16) for address, name in re.findall(
        r"^([0-9a-f]+) [a-zA-Z] (\w+)$", (directory / "symbols.txt").read_text(), re.M)}
    native = u.Uc(u.UC_ARCH_ARM64, u.UC_MODE_ARM)
    native.mem_map(0x20000, 0x38000)
    native.mem_write(0x20000, blob)
    native.hook_add(u.UC_HOOK_CODE, lambda m, pc, size, data: m.emu_stop() if pc == 0x55ff0 else None)
    banks = [struct.unpack("<32I", native.mem_read(symbols["dram_vendor_candidates"] + i * 128, 128))
             for i in range(len(manifest["candidate_types"]))]

    def encode(cols, rows, ranks, full, size, bank=0):
        native.mem_write(0x53000, bytes((cols, rows, ranks, full)))
        for reg, value in ((x.UC_ARM64_REG_X0, 0x53000), (x.UC_ARM64_REG_X1, size),
                           (x.UC_ARM64_REG_X2, bank), (x.UC_ARM64_REG_SP, 0x52a00),
                           (x.UC_ARM64_REG_X30, 0x55ff0)):
            native.reg_write(reg, value)
        native.emu_start(symbols["baseos_normalize_vendor_params"], 0x58000, count=10000)
        assert native.reg_read(x.UC_ARM64_REG_PC) == 0x55ff0
        return native.reg_read(x.UC_ARM64_REG_X0) & 0xffffffff

    vendors = []
    for path in boot0_paths:
        stock = path.read_bytes()
        assert len(stock) == 65536 and stock[4:12] == b"eGON.BT0"
        m = u.Uc(u.UC_ARCH_ARM, u.UC_MODE_THUMB)
        m.mem_map(0x20000, 0x38000)
        m.mem_write(0x20000, stock)
        m.hook_add(u.UC_HOOK_CODE, lambda m, pc, size, data: m.emu_stop() if pc == 0x57ff0 else None)
        vendors.append((path, stock, m))

    cases = []
    for bank in range(len(banks)):
        for cols in range(8, 12):
            for rows in range(13, 18):
                for ranks in (1, 2):
                    for full in (0, 1):
                        size = (1 << (cols + rows + 3)) * (4 if full else 2) * ranks
                        assert encode(cols, rows, ranks, full, size, bank) == 0
                        words = struct.unpack("<32I", native.mem_read(symbols["trained_vendor_params"], 128))
                        assert all(words[i] == banks[bank][i] for i in range(32) if i not in (6, 7, 30))
                        expected_tpr13 = banks[bank][30] if (banks[bank][30] & 0x801) else (banks[bank][30] | 0x6001)
                        assert words[30] == expected_tpr13
                        assert words[6] == 0x3000 | rows << 4 | cols
                        assert words[7] == (size >> 20 << 16) | ((ranks - 1) << 12) | (1 - full)
                        for path, stock, m in vendors:
                            m.mem_write(0x53000, struct.pack("<32I", *words))
                            m.reg_write(a.UC_ARM_REG_CPSR, 0x13)
                            m.reg_write(a.UC_ARM_REG_R0, 0x53000)
                            m.reg_write(a.UC_ARM_REG_SP, 0x57000)
                            m.reg_write(a.UC_ARM_REG_LR, 0x57ff1)
                            m.emu_start(0x24911, 0x58000, count=1000)
                            assert m.reg_read(a.UC_ARM_REG_PC) == 0x57ff0
                            assert m.reg_read(a.UC_ARM_REG_R0) == size >> 20, (path, words[6:8])
                        cases.append({"bank": bank, "cols": cols, "rows": rows, "ranks": ranks,
                                      "full_width": full, "size_mib": size >> 20})
    refused = [(7, 15, 1, 1, 1 << 30), (10, 12, 1, 1, 1 << 30),
               (10, 15, 3, 1, 1 << 30), (10, 15, 1, 2, 1 << 30),
               (10, 16, 1, 1, 3 << 29)]
    for vector in refused: assert encode(*vector) == 0xffffffff
    assert encode(10, 15, 1, 1, 1 << 30, len(banks)) == 0xffffffff
    report = {"passed": True, "spl_sha256": manifest["spl_sha256"],
              "vendor_boot0_sha256": [hashlib.sha256(stock).hexdigest() for _, stock, _ in vendors],
              "vendor_size_routine": "0x24911", "normalized_cases": len(cases),
              "vendor_instruction_cases": len(cases) * len(vendors), "invalid_cases": len(refused) + 1,
              "training_modeled": True, "cases": cases}
    (directory / "geometry-verification.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({key: value for key, value in report.items() if key != "cases"}, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("boot0", nargs="+", type=Path)
    args = parser.parse_args()
    verify(args.directory, args.boot0)
