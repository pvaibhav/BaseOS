#!/usr/bin/env python3
"""v2 of the stamped package: the last stub also copies the stamp block into the device tree.

Same as build-stamped.py (imported), except stub 5: after taking its own stamp it
copies the 14 words at 0x400000F8 (magic, pad, six slots) to the tail of the device
tree U-Boot is about to hand to the kernel: ft_addr + ft_len - 64, with ft_addr and
ft_len read from the bootm images struct (r5 inside boot_jump_linux at 0x1482).
The copy to 0x44000000 at 0x14B0 carries the tail along, the kernel reserves the
blob and exposes it at /sys/firmware/fdt. Then it verifies both builds under the
harness, stopping at 0x14C2 instead of the routine entry so the stub runs.
"""
from __future__ import annotations

import importlib.util
import json
import struct
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("v1", HERE / "build-stamped.py")
v1 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(v1)
harness, kg, require, word = v1.harness, v1.kg, v1.require, v1.word
h, ldr_lit, str_imm, encode_branch = v1.h, v1.ldr_lit, v1.str_imm, v1.encode_branch
SCRATCH = v1.STAMP_BASE - 8


def ldr_imm(rt: int, rn: int, imm: int) -> int:
    require(imm % 4 == 0 and imm < 128, "ldr offset")
    return 0x6800 | (imm // 4) << 6 | rn << 3 | rt


def stub5(at: int, target: int) -> bytes:
    lit_slot, lit_scratch = at + 88, at + 92
    code = h(v1.PUSH_R0_R3) + v1.MRRC_CNTPCT_R0_R1
    code += h(ldr_lit(2, at + len(code), lit_slot), str_imm(0, 2, 0), str_imm(1, 2, 4))
    code += h(ldr_imm(2, 5, 0x6C), ldr_imm(1, 5, 0x70),       # r2 = ft_addr, r1 = ft_len
              0x1852,                                         # adds r2, r2, r1
              0x3A40)                                         # subs r2, #64
    code += h(ldr_lit(0, at + len(code), lit_scratch))
    for k in range(0, 56, 4):
        code += h(ldr_imm(1, 0, k), str_imm(1, 2, k))
    code += h(v1.POP_R0_R3)
    code += encode_branch(at + len(code), target, link=False)
    code += h(v1.NOP) * ((88 - len(code)) // 2)
    require(len(code) == 88, f"stub 5 layout {len(code)}")
    return code + struct.pack("<II", v1.STAMP_BASE + 40, SCRATCH)


original_stub = v1.stub


def stub(at: int, slot: int, target: int) -> bytes:
    return stub5(at, target) if slot == 5 else original_stub(at, slot, target)


v1.stub = stub
v1.OUTPUT = HERE / "boot-package-stamped-v2.bin"


def run(uboot: bytes, derived: dict, environment: dict) -> dict:
    derived = dict(derived, uboot=uboot)
    target = harness.Target("rg34xxsp", derived, environment)
    harness.BOOT_JUMP_LINUX = 0x14C2          # stop after stub 5 and the FDT copy, before "Starting kernel"
    out = harness.boot(target, derived["boot"])
    m = out["machine"]
    ft_addr, ft_len = m.get32(m.images + 0x6C), m.get32(m.images + 0x70)
    tree = m.read(ft_addr, ft_len)
    return dict(ft_addr=ft_addr, ft_len=ft_len, total=harness.fdt.total_size(tree), tree=tree,
                used=max(struct.unpack_from(">I", tree, 8)[0] + struct.unpack_from(">I", tree, 36)[0],
                         struct.unpack_from(">I", tree, 12)[0] + struct.unpack_from(">I", tree, 32)[0]),
                console=m.console[-3:])


def main() -> None:
    info = v1.build()
    package = v1.OUTPUT.read_bytes()
    prefix = v1.REPO / "work/rg34xxsp/boot-prefix.img"
    environment = harness.stored_environment(prefix)
    derived = kg.derive("rg34xxsp", prefix)
    patched = run(package[v1.ITEM:v1.ITEM + v1.ITEM_SIZE], derived, environment)
    plain = run(derived["uboot"], derived, environment)
    require(plain["ft_len"] == patched["ft_len"] and plain["ft_addr"] == patched["ft_addr"], "ft fields differ")
    n = patched["ft_len"]
    require(plain["tree"][:n - 64] == patched["tree"][:n - 64], "tree differs before the stamp block")
    words = struct.unpack("<14I", patched["tree"][n - 64:n - 8])
    stamps = [words[2 + 2 * i] | words[3 + 2 * i] << 32 for i in range(6)]
    # The harness runs only the two boot commands, so stubs 0 and 1 (board_late_init,
    # main_loop) never run here: magic and slots 0-1 stay zero; a device fills them.
    require(words[0] == 0 and words[1] == 0, f"unexpected words before the slots: {words[:2]}")
    require(stamps[0] == stamps[1] == 0 and 0 < stamps[2] < stamps[3] < stamps[4] < stamps[5],
            f"stamps not in order: {stamps}")
    require(patched["used"] <= n - 64, f"strings/struct blocks end at {patched['used']}, tail at {n - 64}")
    require(n <= patched["total"], f"ft_len {n} exceeds totalsize {patched['total']}")
    report = dict(ft_addr=hex(patched["ft_addr"]), ft_len=n, totalsize=patched["total"], blocks_end=patched["used"],
                  tail_offset=n - 64, stamps=stamps, patched_package_sha256=info["patched_package_sha256"],
                  changed_bytes=info["changed_bytes"], console=patched["console"])
    (HERE / "stamps-v2.json").write_text(json.dumps(dict(info, verification=report), indent=2) + "\n")
    print(json.dumps(report, indent=1))


if __name__ == "__main__":
    main()
