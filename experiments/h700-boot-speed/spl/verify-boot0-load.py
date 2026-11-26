#!/usr/bin/env python3
"""Execute vendor boot0's actual TOC1 loader to recover its memory contract."""
import argparse
import hashlib
import json
import struct
from pathlib import Path

import unicorn as u
from unicorn import arm_const as a


def verify(package_path, boot0_path, output):
    package, boot0 = package_path.read_bytes(), boot0_path.read_bytes()
    m = u.Uc(u.UC_ARCH_ARM, u.UC_MODE_THUMB)
    m.mem_map(0x20000, 0x38000)
    m.mem_map(0x40000000, 0x10000000)
    m.mem_map(0x03000000, 0x1000)
    m.mem_map(0x08100000, 0x1000)
    m.mem_map(0x09010000, 0x1000)
    m.mem_write(0x20000, boot0)
    m.mem_write(0x42e00000, package)
    m.reg_write(a.UC_ARM_REG_CPSR, 0x13)
    m.reg_write(a.UC_ARM_REG_SP, 0x57000)
    m.reg_write(a.UC_ARM_REG_LR, 0x57ff1)
    for index in range(3): m.reg_write(a.UC_ARM_REG_R0 + index, 0x34000 + index * 4)
    reads, returned, reset_args = [], [], []
    def code(machine, address, size, data):
        word = struct.unpack("<I", machine.mem_read(address, 4))[0] if size == 4 else 0
        if word == 0x2f50ee1c:
            machine.reg_write(a.UC_ARM_REG_R0 + 2, 0)
            machine.reg_write(a.UC_ARM_REG_PC, address + 5)
            return
        if word == 0x2f50ee0c:
            reset_args.append([hex(machine.reg_read(a.UC_ARM_REG_R0 + i)) for i in range(4)])
            machine.emu_stop()
            return
        if address == 0x57ff0:
            returned.append(machine.reg_read(a.UC_ARM_REG_R0))
            machine.emu_stop()
        elif address == 0x2ac18:
            sector, count, destination = [machine.reg_read(a.UC_ARM_REG_R0 + i) for i in range(3)]
            start, length = sector * 512, count * 512
            assert 0 <= start < len(package) and start + length <= len(package)
            machine.mem_write(destination, package[start:start + length])
            reads.append({"source_offset": hex(start), "bytes": length, "destination": hex(destination)})
            machine.reg_write(a.UC_ARM_REG_R0, count)
            machine.reg_write(a.UC_ARM_REG_PC, machine.reg_read(a.UC_ARM_REG_LR))
        elif address in (0x22880, 0x22958):
            machine.reg_write(a.UC_ARM_REG_R0, 0)
            machine.reg_write(a.UC_ARM_REG_PC, machine.reg_read(a.UC_ARM_REG_LR))
    m.hook_add(u.UC_HOOK_CODE, code)
    m.emu_start(0x2ac35, 0x58000, count=5000000)
    assert returned == [0]
    entries = struct.unpack("<3I", m.mem_read(0x34000, 12))
    monitor = bytes(m.mem_read(entries[2], 0x100))
    assert entries == (0x4a000000, 0, 0x48000000)
    assert monitor[0x30:0xb0] == boot0[0x38:0xb8]
    assert monitor[0x24:0x2c] == boot0[0xf0:0xf8]
    assert struct.unpack_from("<I", monitor, 0xb0)[0] == 0x4a200000
    for revision in (0, 2):
        m.mem_write(0x03000024, struct.pack("<I", revision))
        m.reg_write(a.UC_ARM_REG_R0, entries[2])
        m.emu_start(0x22189, 0x58000, count=10000)
    assert len(reset_args) == 2
    output.mkdir()
    (output / "monitor-header.bin").write_bytes(monitor)
    (output / "uboot-header.bin").write_bytes(bytes(m.mem_read(entries[0], 0x640)))
    report = {"package_sha256": hashlib.sha256(package).hexdigest(), "boot0_sha256": hashlib.sha256(boot0).hexdigest(),
              "entries": list(map(hex, entries)), "reads": reads, "monitor_dram_offset": "0x30",
              "monitor_board_offset": "0x24", "monitor_dtb_pointer_offset": "0xb0",
              "dtb_destination": "0x4a200000", "dtbo_destination": "0x4a100000"}
    report["monitor_reset_registers_r0_r3"] = reset_args
    (output / "verification.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("package", type=Path)
    parser.add_argument("boot0", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    verify(args.package, args.boot0, args.output)
