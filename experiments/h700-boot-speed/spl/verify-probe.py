#!/usr/bin/env python3
"""Execute SRAM entry and actual PIO instructions with a bounded SD model.

DRAM training is a separate hardware experiment; the operation-1 control here
models only its return to verify result persistence and the memory-check loop.
"""
import argparse
import hashlib
import json
import re
import struct
from pathlib import Path

import capstone as cs
import unicorn as uc
from unicorn.arm_const import UC_ARM_REG_CPSR, UC_ARM_REG_PC, UC_ARM_REG_R0


def fnv(data):
    value = 2166136261
    for byte in data: value = ((value ^ byte) * 16777619) & 0xffffffff
    return value


def verify(directory: Path):
    blob = (directory / "probe.bin").read_bytes()
    proof = json.loads((directory / "manifest.json").read_text())
    assert hashlib.sha256(blob).hexdigest() == proof["probe_sha256"]
    symbols = {name: int(at, 16) for at, name in re.findall(
        r"^\s*([0-9a-f]+)\s+[0-9a-f]+\s+[0-9a-f]+\s+\d+\s+(\w+)\s*$",
        (directory / "probe.map").read_text(), re.M)}
    counter_sites = {}
    for at in range(0, len(blob) - 3, 4):
        ins = next(cs.Cs(cs.CS_ARCH_ARM, cs.CS_MODE_ARM).disasm(blob[at:at+4], 0x20000+at), None)
        if ins and ins.mnemonic == "mrrc" and ins.op_str.startswith("p15, #0,") and ins.op_str.endswith("c14"):
            regs = re.findall(r"\br(\d+)\b", ins.op_str)
            counter_sites[ins.address] = tuple(map(int, regs))
    cases = []
    for operation, corrupt, io_error in ((0, False, False), (0, True, False), (0, False, True), (1, False, False)):
        machine = uc.Uc(uc.UC_ARCH_ARM, uc.UC_MODE_ARM)
        for address, size in ((0x20000, 0x8000), (0x03009000, 0x1000), (0x04020000, 0x1000), (0x41000000, 0x4000)):
            machine.mem_map(address, size)
        machine.mem_write(0x20000, blob)
        machine.reg_write(UC_ARM_REG_CPSR, 0x13)
        machine.reg_write(UC_ARM_REG_R0, operation)
        words = [0] * 128
        words[:3] = [0x50534f42, 1, operation]
        words[3] = 10
        card = bytearray(struct.pack("<128I", *words))
        struct.pack_into("<I", card, 508, fnv(card[:508]) ^ int(corrupt))
        writes, transfers, state = [], [], {"timer": 0, "fifo": 0, "writing": False, "reset": False}
        def get(at): return struct.unpack("<I", machine.mem_read(at, 4))[0]
        def put(at, value): machine.mem_write(at, struct.pack("<I", value))
        def mem_read(m, access, address, size, value, user):
            if address == 0x04020200:
                assert not state["writing"] and state["fifo"] < 128
                put(address, struct.unpack_from("<I", card, state["fifo"] * 4)[0])
                state["fifo"] += 1
            elif address == 0x04020000:
                put(address, get(address) & ~2)
            elif address == 0x04020038:
                put(address, 0x100 if io_error else 12)
            elif address == 0x0402003c:
                put(address, 0 if state["fifo"] < 128 or state["writing"] else 4)
        def mem_write(m, access, address, size, value, user):
            assert size == 4 and address % 4 == 0, f"unaligned SRAM-probe store {address:#x}"
            assert (0x20000 <= address < 0x28000 or 0x41000000 <= address < 0x41004000 or
                    0x04020000 <= address < 0x04021000 or 0x03009000 <= address < 0x0300a000)
            if address == 0x04020018:
                assert get(0x0402001c) == 0x56000 and get(0x04020014) == 512
                assert value in (0x80002351, 0x80002758), hex(value)
                state["writing"] = (value & 63) == 24
                state["fifo"] = 0
                transfers.append(value & 63)
            elif address == 0x04020200:
                assert state["writing"] and state["fifo"] < 128
                struct.pack_into("<I", card, 4 * state["fifo"], value)
                state["fifo"] += 1
                if state["fifo"] == 128: writes.append(bytes(card))
            elif address == 0x030090b8:
                assert value == 0x31
                state["reset"] = True
        def code(m, address, size, user):
            assert 0x20000 <= address < 0x20000 + len(blob), f"execution escaped SRAM {address:#x}"
            if address in counter_sites:
                state["timer"] += 240
                low, high = counter_sites[address]
                m.reg_write(UC_ARM_REG_R0 + low, state["timer"])
                m.reg_write(UC_ARM_REG_R0 + high, 0)
                m.reg_write(UC_ARM_REG_PC, address + 4)
            elif address == symbols["sunxi_dram_init"]:
                assert operation == 1
                m.reg_write(UC_ARM_REG_R0, 0x40000000)
                m.reg_write(UC_ARM_REG_PC, m.reg_read(uc.arm_const.UC_ARM_REG_LR))
            elif address == symbols["panic"]:
                m.emu_stop()
        machine.hook_add(uc.UC_HOOK_MEM_READ, mem_read)
        machine.hook_add(uc.UC_HOOK_MEM_WRITE, mem_write)
        machine.hook_add(uc.UC_HOOK_CODE, code)
        machine.emu_start(0x20000, 0x30000, timeout=3000000, count=1000000)
        if corrupt or io_error:
            assert not writes and not state["reset"]
        else:
            assert [struct.unpack_from("<I", data, 12)[0] for data in writes] == [1, 2]
            assert state["reset"] and transfers == [17, 24, 17, 24, 17]
            assert fnv(card[:508]) == struct.unpack_from("<I", card, 508)[0]
            assert struct.unpack_from("<I", card, 36)[0] == (0x40000000 if operation else 0)
        cases.append({"operation": operation, "bad_result_crc": corrupt, "io_error": io_error,
                      "writes": len(writes), "reset": state["reset"]})
    (directory / "verification.json").write_text(json.dumps({"probe_sha256": proof["probe_sha256"],
        "dram_training_modeled": True, "cases": cases}, indent=2) + "\n")
    print(json.dumps(cases, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    verify(parser.parse_args().directory)
