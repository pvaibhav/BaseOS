#!/usr/bin/env python3
"""Execute the actual patched AArch32 BL33 entry and check its RTC checkpoint."""
import argparse
import json
import struct
from pathlib import Path

import unicorn as u
from unicorn import arm_const as a
from unicorn import arm64_const as a64


def monitor_exit(directory):
    cases = []
    for initial, entry, expected in ((0x42530022, 0x4a000000, [0x4a000000]),
                                     (0x42530022, 0x40080000, [0x40080000]),
                                     (0x42530025, 0xffffff8008090124, []),
                                     (0x4a000000, 0xffffff8008090124, [])):
        m = u.Uc(u.UC_ARCH_ARM64, u.UC_MODE_ARM)
        for address, size in ((0x07000000, 4096), (0x48000000, 0x40000), (0x52000, 4096)):
            m.mem_map(address, size)
        m.mem_write(0x48000000, (directory / "monitor-traced.bin").read_bytes())
        m.mem_write(0x0700013c, struct.pack("<I", initial))
        registers = [0xcccc0000 + i for i in range(18)]
        for i, value in enumerate(registers): m.reg_write(a64.UC_ARM64_REG_X0 + i, value)
        m.reg_write(a64.UC_ARM64_REG_SP, 0x52a00)
        m.mem_write(0x52a80, struct.pack("<2Q", *registers[16:]))
        writes, returned = [], []
        def write(machine, access, address, size, value, data):
            assert address == 0x0700013c and size == 4
            writes.append(value)
        def code(machine, address, size, data):
            instruction = struct.unpack("<I", machine.mem_read(address, 4))[0]
            if instruction == 0xd53e4031:
                machine.reg_write(a64.UC_ARM64_REG_X17, entry)
                machine.reg_write(a64.UC_ARM64_REG_PC, address + 4)
            elif instruction == 0xd69f03e0:
                returned.append(True)
                machine.emu_stop()
        m.hook_add(u.UC_HOOK_MEM_WRITE, write)
        m.hook_add(u.UC_HOOK_CODE, code)
        m.emu_start(0x480159d0, 0x48040000, count=100)
        assert returned and writes == expected
        assert [m.reg_read(a64.UC_ARM64_REG_X0 + i) for i in range(18)] == registers
        cases.append({"initial_trace": hex(initial), "return_pc": hex(entry),
                      "writes": list(map(hex, writes)), "register_context_exact": True})
    return cases


def verify(directory):
    m = u.Uc(u.UC_ARCH_ARM, u.UC_MODE_ARM)
    for address, size in ((0x07000000, 4096), (0x49000000, 4096), (0x4a000000, 0x100000)):
        m.mem_map(address, size)
    m.mem_write(0x49000000, (directory / "trace-code.bin").read_bytes())
    m.mem_write(0x4a000000, (directory / "uboot-traced.bin").read_bytes())
    initial = [0x11110000 + i for i in range(4)]
    for i, value in enumerate(initial): m.reg_write(a.UC_ARM_REG_R0 + i, value)
    writes, reached = [], []
    def write(machine, access, address, size, value, data):
        assert (address, size, value) == (0x0700013c, 4, 0x42530025)
        writes.append(value)
    def stop(machine, address, size, data):
        reached.append(address)
        machine.emu_stop()
    m.hook_add(u.UC_HOOK_MEM_WRITE, write)
    m.hook_add(u.UC_HOOK_CODE, stop, begin=0x4a000640, end=0x4a000640)
    m.emu_start(0x4a000000, 0x4a100000, count=100)
    assert writes == [0x42530025] and reached == [0x4a000640]
    assert [m.reg_read(a.UC_ARM_REG_R0 + i) for i in range(4)] == initial
    report = {"spl_sha256": json.loads((directory / "manifest.json").read_text())["spl_sha256"],
              "bl33_trace": writes, "vendor_entry": hex(reached[0]), "r0_r3_preserved": True}
    report.update(monitor_repeat_cases=monitor_exit(directory), monitor_repeat_safe=True)
    (directory / "bl33-trace-verification.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    verify(parser.parse_args().directory)
