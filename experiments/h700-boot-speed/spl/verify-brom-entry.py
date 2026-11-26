#!/usr/bin/env python3
"""Execute SPL's real AArch32 eGON/RMR entry up to the modeled core reset."""
import argparse
import hashlib
import json
import struct
from pathlib import Path

import unicorn as u
from unicorn import arm_const as a


def verify(directory):
    manifest = json.loads((directory / "manifest.json").read_text())
    blob = (directory / "spl.bin").read_bytes()
    assert hashlib.sha256(blob).hexdigest() == manifest["spl_sha256"]
    assert blob[4:12] == b"eGON.BT0" and struct.unpack_from("<I", blob, 16)[0] == len(blob)
    checked = bytearray(blob)
    struct.pack_into("<I", checked, 12, 0x5f0a6c39)
    assert sum(struct.unpack(f"<{len(blob)//4}I", checked)) & 0xffffffff == struct.unpack_from("<I", blob, 12)[0]
    cases = []
    for revision in (0, 1):
        m = u.Uc(u.UC_ARCH_ARM, u.UC_MODE_ARM)
        m.mem_map(0x20000, 0x38000)
        m.mem_map(0x03000000, 0x1000)
        m.mem_map(0x08100000, 0x1000)
        m.mem_map(0x09010000, 0x1000)
        m.mem_write(0x20000, blob)
        m.mem_write(0x03000024, struct.pack("<I", revision))
        m.reg_write(a.UC_ARM_REG_CPSR, 0x1d3)
        m.reg_write(a.UC_ARM_REG_SP, 0x18000)
        writes, reset = [], []
        def code(machine, address, size, data):
            word = struct.unpack("<I", machine.mem_read(address, 4))[0]
            if word == 0xe101e300:  # banked SP_irq read, used only for FEL state restoration
                machine.reg_write(a.UC_ARM_REG_LR, 0)
                machine.reg_write(a.UC_ARM_REG_PC, address + 4)
            elif word == 0xee1c0f50:  # RMR read, modeled because Unicorn has no EL3 security state
                machine.reg_write(a.UC_ARM_REG_R0, 0)
                machine.reg_write(a.UC_ARM_REG_PC, address + 4)
            elif word == 0xee0c0f50:
                reset.append(machine.reg_read(a.UC_ARM_REG_R0))
                machine.emu_stop()
        def write(machine, access, address, size, value, data):
            assert 0x20000 <= address < 0x58000 or address in (0x08100040, 0x09010040)
            if address in (0x08100040, 0x09010040): writes.append((address, value))
        m.hook_add(u.UC_HOOK_CODE, code)
        m.hook_add(u.UC_HOOK_MEM_WRITE, write)
        m.emu_start(0x20000, 0x58000, count=10000)
        assert reset == [3] and writes == [(0x08100040 if revision else 0x09010040, 0x20060)]
        cases.append({"revision": revision, "rvbar_writes": writes, "rmr_reset_value": reset[0]})
    report = {"spl_sha256": manifest["spl_sha256"], "rmr_reset_modeled": True,
              "banked_sp_irq_modeled": True, "egon_checksum_valid": True, "cases": cases}
    (directory / "brom-entry-verification.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    verify(parser.parse_args().directory)
