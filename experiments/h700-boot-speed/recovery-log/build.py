#!/usr/bin/env python3
"""Add a checked previous-kernel ring export to an existing recovery control."""
import argparse
import hashlib
import importlib.util
import json
import os
import re
import shutil
import struct
import subprocess
from pathlib import Path

import unicorn as u
from unicorn import arm_const as a

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
BASE = 0x4a000000
HELP = 0x918c2
AT = 0x918c4
SITE = 0x14d0


def sha(data): return hashlib.sha256(data).hexdigest()


def build(source, output):
    assert not output.exists()
    manifest = json.loads((source / "manifest.json").read_text())
    package = bytearray((source / "boot-package.bin").read_bytes())
    assert sha(package) == manifest["patched_package_sha256"]
    assert manifest["target"] == "rg34xxsp"
    spec = importlib.util.spec_from_file_location("log_stamp", HERE.parent / "uboot-stamp/build-stamped.py")
    stamp = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(stamp)
    original = bytes(package[0x800:0x100800])
    assert stamp.harness.branch_target(original, SITE) == 0xc7c
    assert stamp.word(original, 0) == 0xea00018e
    assert original[HELP:HELP + 11] == b"<interface>"
    end = original.index(0, HELP)
    assert end == HELP + 564
    # No loader relocation writes into this command's help-string storage.
    rel_end = stamp.word(original, 0x13d0) - BASE
    rel_start = stamp.word(original, 0x13cc) - BASE
    for offset in range(rel_start, rel_end, 8):
        location = stamp.word(original, offset) - BASE
        assert not HELP <= location < end
    output.mkdir(parents=True)
    for name in ("boot.img", "env-normal.bin", "env-armed_no.bin", "env-armed_yes.bin", "handoff-verification.json"):
        shutil.copyfile(source / name, output / name)
    lld = sorted((Path.home() / ".rustup/toolchains").glob("*/lib/rustlib/*/bin/gcc-ld/ld.lld"))[-1]
    environment = dict(os.environ)
    for parent in lld.parents:
        if (parent / "lib/libLLVM.dylib").is_file():
            environment["DYLD_LIBRARY_PATH"] = str(parent / "lib")
            break
    objects = []
    for name in ("entry-save.S", "entry.S", "capture.c"):
        target = output / (name + ".o")
        subprocess.run(["clang", "--target=armv7a-none-eabi", "-march=armv7-a", "-mthumb",
                        "-mno-unaligned-access", "-mfloat-abi=soft", "-Os", "-ffreestanding", "-fno-builtin",
                        "-fno-stack-protector", "-fno-unwind-tables", "-fno-asynchronous-unwind-tables",
                        "-Wall", "-Wextra", "-Werror", "-c", str(HERE / name), "-o", str(target)], check=True)
        objects.append(str(target))
    script = output / "link.ld"
    script.write_text(f"vendor_cleanup = 0x4a000c7d; vendor_start = 0x4a000640; SECTIONS {{ .text 0x{BASE + AT:x} : {{ *(.text.save) *(.text.entry) *(.text*) *(.rodata*) }} /DISCARD/ : {{ *(.ARM.exidx*) *(.ARM.extab*) }} }}\n")
    subprocess.run([str(lld), "-T", str(script), "-Map=" + str(output / "capture.map"), "--oformat=binary", *objects,
                    "-o", str(output / "capture.bin")], check=True, env=environment)
    code = (output / "capture.bin").read_bytes()
    match = re.search(r"^\s*([0-9a-f]+)\s+[0-9a-f]+\s+[0-9a-f]+\s+\d+\s+capture_entry\s*$",
                      (output / "capture.map").read_text(), re.M)
    assert match
    capture_at = (int(match[1], 16) & ~1) - BASE
    assert AT + len(code) <= end
    patched = bytearray(original)
    patched[HELP] = 0
    patched[AT:AT + len(code)] = code
    patched[SITE:SITE + 4] = stamp.encode_branch(SITE, capture_at, link=True)
    struct.pack_into("<I", patched, 0, 0xea000000 | ((AT - 8) >> 2))
    struct.pack_into("<I", patched, 12, stamp.kg.checksum(patched, 12))
    allowed = set(range(4)) | {12, 13, 14, 15, HELP} | set(range(AT, AT + len(code))) | set(range(SITE, SITE + 4))
    assert {i for i, pair in enumerate(zip(original, patched)) if pair[0] != pair[1]} <= allowed
    package[0x800:0x100800] = patched
    struct.pack_into("<I", package, 20, stamp.kg.checksum(package, 20))
    cases = []
    tree = (ROOT / "work/h700-loader/working-fast-kernel.dtb").read_bytes()
    ring = bytes((index * 37 + 19) & 255 for index in range(16384))
    for relocation in (0, 0x3000000):
        for valid in (False, True):
            machine = u.Uc(u.UC_ARCH_ARM, u.UC_MODE_ARM)
            machine.mem_map(0x40000000, 0x10000000)
            machine.mem_map(0x07000000, 4096)
            machine.mem_write(BASE + relocation, bytes(patched))
            machine.mem_write(0x44000000, tree if valid else bytes(len(tree)))
            machine.mem_write(0x4116a7f0, ring)
            for address, value in ((0x41167a3c, 32), (0x41167a58, 1024), (0x0700013c, 0x9400000a)):
                machine.mem_write(address, struct.pack("<I", value))
            registers = [0xcccc0000 + i for i in range(13)]
            for index, value in enumerate(registers): machine.reg_write(a.UC_ARM_REG_R0 + index, value)
            machine.reg_write(a.UC_ARM_REG_SP, 0x4fff0000)
            machine.reg_write(a.UC_ARM_REG_CPSR, 0xa00001d3)
            machine.reg_write(a.UC_ARM_REG_LR, 0xeeee0001)
            stage_hook = machine.hook_add(u.UC_HOOK_CODE, lambda m, pc, size, data: m.emu_stop(),
                             begin=BASE + relocation + 0x640, end=BASE + relocation + 0x640)
            machine.emu_start(BASE + relocation, 0x50000000, count=100000)
            assert machine.reg_read(a.UC_ARM_REG_PC) == BASE + relocation + 0x640
            assert [machine.reg_read(a.UC_ARM_REG_R0 + i) for i in range(3, 13)] == registers[3:]
            assert machine.reg_read(a.UC_ARM_REG_SP) == 0x4fff0000
            assert machine.reg_read(a.UC_ARM_REG_LR) == 0xeeee0001
            assert machine.reg_read(a.UC_ARM_REG_CPSR) & 0xf8000000 == 0xa0000000
            machine.hook_del(stage_hook)
            machine.mem_write(0x4116a7f0, b"\xff" * len(ring))
            for index, value in enumerate(registers): machine.reg_write(a.UC_ARM_REG_R0 + index, value)
            machine.hook_add(u.UC_HOOK_CODE, lambda m, pc, size, data: m.emu_stop(),
                             begin=BASE + relocation + 0xc7c, end=BASE + relocation + 0xc7c)
            machine.emu_start((BASE + relocation + SITE) | 1, 0x50000000, count=100000)
            assert machine.reg_read(a.UC_ARM_REG_PC) == BASE + relocation + 0xc7c
            assert [machine.reg_read(a.UC_ARM_REG_R0 + i) for i in range(13)] == registers
            assert machine.reg_read(a.UC_ARM_REG_SP) == 0x4fff0000
            assert machine.reg_read(a.UC_ARM_REG_LR) == (BASE + relocation + SITE + 4) | 1
            assert machine.reg_read(a.UC_ARM_REG_CPSR) & 0xf8000000 == 0xa0000000
            if valid:
                total = struct.unpack_from(">I", machine.mem_read(0x44000000, 8), 4)[0]
                offset = (len(tree) + 15) & ~15
                assert total == offset + 64 + len(ring)
                assert bytes(machine.mem_read(0x44000000 + offset, 8)) == b"BSKLOG01"
                assert bytes(machine.mem_read(0x44000000 + offset + 64, len(ring))) == ring
                final = bytearray(machine.mem_read(0x44000000, len(tree)))
                final[4:8] = tree[4:8]
                assert final == tree
            else:
                assert bytes(machine.mem_read(0x44000000, len(tree))) == bytes(len(tree))
            cases.append({"relocation": hex(relocation), "valid_fdt": valid, "registers_flags_exact": True,
                          "survives_source_overwrite": True})
    manifest.update(patched_package_sha256=sha(package), recovery_log_capture=True,
                    recovery_log_parent_package_sha256=sha((source / "boot-package.bin").read_bytes()))
    manifest["verification"]["recovery_log"] = cases
    (output / "boot-package.bin").write_bytes(package)
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    proof = dict(manifest)
    proof.pop("boot_image_sha256")
    (output / "package-proof.json").write_text(json.dumps(proof, indent=2) + "\n")
    print(json.dumps({"passed": True, "capture_bytes": len(code), "package_sha256": sha(package), "cases": cases}, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    build(args.source, args.output)
