#!/usr/bin/env python3
"""Run the vendor Image's real early page-table construction, before MMU-on."""
import argparse
import hashlib
import json
import struct
from pathlib import Path

import capstone
import unicorn as u
from unicorn import arm64_const as a


def xreg(index):
    return a.UC_ARM64_REG_X0 + index if index < 29 else (a.UC_ARM64_REG_X29, a.UC_ARM64_REG_X30)[index - 29]


def verify(directory):
    manifest = json.loads((directory / "manifest.json").read_text())
    kernel = (directory / "kernel.bin").read_bytes()
    assert hashlib.sha256(kernel).hexdigest() == manifest["kernel_sha256"]
    machine = u.Uc(u.UC_ARCH_ARM64, u.UC_MODE_ARM)
    machine.mem_map(0x40000000, 0x10000000)
    machine.mem_write(0x40080000, kernel)
    machine.mem_write(0x411e8000, b"\xa5" * 0x6000)
    machine.reg_write(a.UC_ARM64_REG_X0, 0x44000000)
    registers = {"currentel": 4, "sctlr_el1": 0x30c00800,
                 "ctr_el0": 0x84448004,
                 "id_aa64mmfr0_el1": 0x1122, "id_aa64mmfr1_el1": 0,
                 "id_aa64dfr0_el1": 0, "midr_el1": 0x410fd034}
    decoder = capstone.Cs(capstone.CS_ARCH_ARM64, capstone.CS_MODE_ARM)
    count = [0]

    def code(m, address, size, data):
        count[0] += 1
        if address == 0x4098d22c:
            m.emu_stop()
            return
        instruction = next(decoder.disasm(bytes(m.mem_read(address, 4)), address))
        operation = instruction.mnemonic
        operands = instruction.op_str.split(", ")
        if operation == "mrs":
            target, name = operands
            assert name in registers, name
            m.reg_write(xreg(int(target[1:])), registers[name])
        elif operation == "msr":
            name, source = operands
            value = int(source[1:], 0) if source.startswith("#") else (
                0 if source == "xzr" else m.reg_read(xreg(int(source[1:]))))
            registers[name] = value
        elif operation not in ("dc", "ic", "tlbi"):
            return
        m.reg_write(a.UC_ARM64_REG_PC, address + 4)

    machine.hook_add(u.UC_HOOK_CODE, code)
    machine.emu_start(0x40080000, 0x50000000, count=100000)
    assert machine.reg_read(a.UC_ARM64_REG_PC) == 0x4098d22c
    mappings = []
    for address in (0x4098d640, 0x4098d800, 0x4098dfff):
        table = registers["ttbr0_el1"] & ~4095
        for shift in (30, 21, 12):
            descriptor = struct.unpack("<Q", machine.mem_read(table + ((address >> shift) & 511) * 8, 8))[0]
            assert descriptor & 1
            if descriptor & 3 == 3 and shift != 12:
                table = descriptor & 0x0000fffffffff000
                continue
            physical = (descriptor & 0x0000fffffffff000 & ~((1 << shift) - 1)) | (address & ((1 << shift) - 1))
            attribute = (registers["mair_el1"] >> (((descriptor >> 2) & 7) * 8)) & 255
            assert physical == address and not descriptor & (3 << 53) and attribute == 255
            mappings.append({"address": hex(address), "physical": hex(physical),
                             "descriptor": hex(descriptor), "normal_wbwa": True, "executable": True})
            break
        else:
            raise AssertionError("no leaf descriptor")
    report = {"passed": True, "kernel_sha256": manifest["kernel_sha256"],
              "instructions": count[0], "stopped_before_mmu_enable": True,
              "modeled_system_registers": registers, "mappings": mappings,
              "hardware_mmu_execution": False}
    (directory / "kernel-idmap-verification.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    verify(parser.parse_args().directory)
