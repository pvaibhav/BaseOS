#!/usr/bin/env python3
"""Stamp the actual ARM entry, then retain it through vendor relocation.

v4's first stamp is board_init_r, after board_init_f and relocation; it is not
boot0's end. The entry veneer uses only r0-r2, which the original ARM startup
overwrites, and leaves SP/LR and r3-r12 untouched. All other v4 stamps remain.
"""
from __future__ import annotations

import importlib.util
import json
import struct
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("stamp_v4", HERE / "build-stamped-v4.py")
v4 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(v4)
v3, v1 = v4.v3, v4.v3.v1
OUT = v1.REPO / "work/h700-loader/stamp-v5"
OUT.mkdir(parents=True, exist_ok=True)
v3.HERE = OUT


def first(at: int, target: int) -> bytes:
    h, ldr, store = v1.h, v1.ldr_lit, v1.str_imm
    literals = at + 96
    code = h(v1.PUSH_R0_R3)
    code += h(ldr(2, at + len(code), literals), v3.ldr_imm(0, 2, 8), v3.ldr_imm(1, 2, 12))
    code += h(ldr(3, at + len(code), literals + 4), store(3, 2, 0))
    code += v1.MOVW_R3_0
    body, _ = v3.stores(3, 2, 16, 136)
    code += body + h(v1.POP_R0_R3)
    code += v1.encode_branch(at + len(code), target, link=False)
    v1.require(len(code) <= 96, "first stub exceeds allocation")
    return code + h(v1.NOP) * ((96 - len(code)) // 2) + struct.pack("<II", v3.SCRATCH, v3.MAGIC)


def arm_branch(at: int, target: int) -> int:
    offset = target - (at + 8)
    v1.require(offset % 4 == 0 and -(1 << 25) <= offset < (1 << 25), "ARM branch out of range")
    return 0xEA000000 | ((offset >> 2) & 0xFFFFFF)


if __name__ == "__main__":
    v3.stub_first = first
    info, _ = v3.build()
    source = OUT / "boot-package-stamped-v3.bin"
    package = bytearray(source.read_bytes())
    uboot = bytearray(package[v1.ITEM:v1.ITEM + v1.ITEM_SIZE])
    at = (int(info["stub_area"][1], 16) + 3) & ~3
    v1.require(at + 24 <= v1.HELP_TEXT + v1.HELP_LEN, "entry stub exceeds reclaimed help")
    # mrrc p15,0,r0,r1,c14; ldr r2,[pc,#8]; str r0,[r2]; str r1,[r2,#4]; b +0x640; literal
    stub = struct.pack("<6I", 0xEC510F0E, 0xE59F2008, 0xE5820000, 0xE5821004,
                       arm_branch(at + 16, 0x640), v3.STAMP_BASE)
    from capstone import Cs, CS_ARCH_ARM, CS_MODE_ARM
    decoded = list(Cs(CS_ARCH_ARM, CS_MODE_ARM).disasm(stub[:20], v1.LINK + at))
    v1.require([i.mnemonic for i in decoded] == ["mrrc", "ldr", "str", "str", "b"], "entry encoding differs")
    v1.require(v1.word(uboot, 0) == arm_branch(0, 0x640), "original ARM entry differs")
    uboot[at:at + len(stub)] = stub
    struct.pack_into("<I", uboot, 0, arm_branch(0, at))
    struct.pack_into("<I", uboot, 12, v1.kg.checksum(uboot, 12))
    package[v1.ITEM:v1.ITEM + v1.ITEM_SIZE] = uboot
    struct.pack_into("<I", package, 20, v1.kg.checksum(package, 20))
    info.update(output="boot-package-stamped-v5.bin", entry_stub=hex(at),
                patched_package_sha256=v1.kg.sha(package), entry_disassembly=[f"{i.mnemonic} {i.op_str}" for i in decoded])
    info["verification"] = v3.verify(bytes(uboot), info)
    (OUT / info["output"]).write_bytes(package)
    source.unlink()
    (OUT / "stamps-v5.json").write_text(json.dumps(info, indent=2) + "\n")
    print(json.dumps({k: info[k] for k in ("patched_package_sha256", "entry_disassembly", "verification")}, indent=2))
