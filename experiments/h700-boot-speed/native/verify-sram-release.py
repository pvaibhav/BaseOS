#!/usr/bin/env python3
"""Execute the linked SRAM release and independent original vendor U-Boot code."""
import argparse
import hashlib
import json
import random
import re
import struct
from pathlib import Path

import capstone as c
import unicorn as u
from unicorn import arm_const as a

ROOT = Path(__file__).resolve().parents[3]
BASE, SYSCTRL, RETURN = 0x4a000000, 0x03000000, 0x7000f000
# Original compiled U-Boot helper prefix; stop before its unrelated CCU tail.
VENDOR_PREFIX = bytes.fromhex(
    "4ff040721368bff35f8f03f00043bff35f8f136004321368bff35f8f23f08073bff35f8f1360")


def sha(data):
    return hashlib.sha256(data).hexdigest()


def vendor_from_package(package):
    assert len(package) >= 64
    count = struct.unpack_from("<I", package, 32)[0]
    assert 0 < count <= 32 and 64 + count * 368 <= len(package)
    items = {}
    for index in range(count):
        at = 64 + index * 368
        name = package[at:at + 64].split(b"\0", 1)[0].decode()
        offset, size = struct.unpack_from("<2I", package, at + 64)
        assert name not in items and offset + size <= len(package)
        items[name] = package[offset:offset + size]
    vendor = items["u-boot"]
    offset = vendor.find(VENDOR_PREFIX)
    assert offset >= 0 and vendor.find(VENDOR_PREFIX, offset + 1) < 0, "vendor SRAM prefix is not unique"
    return vendor, BASE + offset


def execute(blob, function, before, native):
    machine = u.Uc(u.UC_ARCH_ARM, u.UC_MODE_ARM if native else u.UC_MODE_THUMB)
    machine.mem_map(BASE, 0x100000)
    machine.mem_write(BASE, blob)
    machine.mem_map(SYSCTRL, 4096)
    machine.mem_map(0x70000000, 0x10000)
    machine.mem_write(SYSCTRL, struct.pack("<2I", *before))
    machine.reg_write(a.UC_ARM_REG_SP, 0x4a0f0000)
    machine.reg_write(a.UC_ARM_REG_LR, RETURN if native else RETURN | 1)
    for register in range(4, 12):
        machine.reg_write(a.UC_ARM_REG_R0 + register, 0xdead0000 + register)
    writes, barriers = [], []

    def write(uc, access, address, size, value, data):
        writes.append((address, size, value))

    machine.hook_add(u.UC_HOOK_MEM_WRITE, write, begin=SYSCTRL, end=SYSCTRL + 4095)
    if native:
        decoder = c.Cs(c.CS_ARCH_ARM, c.CS_MODE_ARM)

        def instruction(uc, pc, size, data):
            ins = next(decoder.disasm(bytes(uc.mem_read(pc, size)), pc))
            if ins.mnemonic == "dsb" and ins.op_str == "sy":
                assert len(writes) == 2, "DSB SY must follow both release writes"
                barriers.append(pc)

        machine.hook_add(u.UC_HOOK_CODE, instruction, begin=function, end=function + 0x100)
    end = RETURN if native else function + len(VENDOR_PREFIX)
    machine.emu_start(function if native else function | 1, end, count=1000)
    assert machine.reg_read(a.UC_ARM_REG_PC) == end, "SRAM release did not reach its exit"
    expected = [(SYSCTRL, 4, before[0] & 0x80000000), (SYSCTRL + 4, 4, before[1] & 0xfeffffff)]
    assert writes == expected, writes
    if native:
        assert machine.reg_read(a.UC_ARM_REG_SP) == 0x4a0f0000
        assert [machine.reg_read(a.UC_ARM_REG_R0 + i) for i in range(4, 12)] == [0xdead0000 + i for i in range(4, 12)]
        assert len(barriers) == 1, "exactly one DSB SY is required after release"
    return writes


def all_prepared(root):
    targets = json.loads((root / "experiments/h700-boot-speed/evidence/compatibility.json").read_text())["targets"]
    profiles = json.loads((root / "manifest/kernel-gzip.json").read_text())["profiles"]
    assert len(targets) == 11 and set(targets) == set(profiles), "prepared vendor coverage changed"
    rows = []
    for target in sorted(targets):
        package = (root / "work" / target / "boot-gzip/boot-package.bin").read_bytes()
        assert sha(package) == targets[target]["package_sha256"], target
        vendor, function = vendor_from_package(package)
        for before in ((0x7fffffff, 0x01000000), (0xffffffff, 0xffffffff), (0x81234567, 0x76543210)):
            execute(vendor, function, before, False)
        rows.append({"target": target, "package_sha256": sha(package), "uboot_sha256": sha(vendor),
                     "prefix_address": hex(function), "prefix_bytes_sha256": sha(VENDOR_PREFIX),
                     "executed_seed_cases": 3, "exact_contract": True})
    return {"passed": True, "models": len(rows), "actual_vendor_executed_cases": len(rows) * 3, "rows": rows}


def verify(directory, package_path):
    manifest = json.loads((directory / "manifest.json").read_text())
    loader, package = (directory / "loader.bin").read_bytes(), package_path.read_bytes()
    assert sha(loader) == manifest["loader_sha256"] and sha(package) == manifest["package_sha256"]
    vendor, vendor_function = vendor_from_package(package)
    symbols = {name: int(address, 16) for address, name in re.findall(
        r"^\s*([0-9a-f]+)\s+[0-9a-f]+\s+[0-9a-f]+\s+\d+\s+(\w+)(?:\s*=.*)?\s*$",
        (directory / "loader.map").read_text(), re.M)}
    function = symbols["release_display_sram"]
    rng = random.Random(0x7005a4a)
    seeds = [(0, 0), (0x7fffffff, 0x01000000), (0xffffffff, 0xffffffff),
             (0x80000000, 0xfeffffff), (0x12345678, 0x87654321)]
    seeds += [(rng.getrandbits(32), rng.getrandbits(32)) for _ in range(27)]
    cases = []
    for before in seeds:
        expected = execute(vendor, vendor_function, before, False)
        assert execute(loader, function, before, True) == expected
        cases.append({"sysctrl_before": [hex(value) for value in before],
                      "sysctrl_after": [hex(value) for _, _, value in expected],
                      "vendor_and_native_writes_exact": True, "native_dsb_sy_after_release": True})
    return {"passed": True, "loader_sha256": sha(loader), "package_sha256": sha(package),
            "vendor_uboot_sha256": sha(vendor), "vendor_function": hex(vendor_function),
            "vendor_prefix_sha256": sha(VENDOR_PREFIX), "oracle": "actual original vendor U-Boot SRAM prefix",
            "native_function": hex(function), "cases": cases, "case_count": len(cases),
            "no_device_access": True, "hardware_qualified": False}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path, nargs="?")
    parser.add_argument("--package", type=Path)
    parser.add_argument("--all-prepared", action="store_true", help="execute the pinned vendor prefix in all11 prepared packages")
    parser.add_argument("--root", type=Path, default=ROOT, help="repository root for --all-prepared")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if bool(args.directory) != bool(args.package) or not (args.directory or args.all_prepared):
        parser.error("supply DIRECTORY with --package and/or --all-prepared")
    report = verify(args.directory, args.package) if args.directory else {"passed": True, "no_device_access": True}
    if args.all_prepared:
        report["prepared_vendor_prefixes"] = all_prepared(args.root)
    rendered = json.dumps(report, indent=2) + "\n"
    if args.output:
        args.output.write_text(rendered)
    print(rendered, end="")
