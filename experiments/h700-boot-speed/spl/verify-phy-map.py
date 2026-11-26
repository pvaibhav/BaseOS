#!/usr/bin/env python3
"""Replay actual controller code through all 27 PHY wiring-register writes.

Only delay/completion boundaries are modeled; no DRAM is mapped or trained.
Expected tables come from the pinned upstream source, independently of the
builder's runtime-table transformation. --expect-static demonstrates old builds.
"""
import argparse
import hashlib
import itertools
import json
import re
import struct
import tarfile
from pathlib import Path

import unicorn as u
from unicorn import arm_const, arm64_const

ROOT = Path(__file__).resolve().parents[3]
PIN = "f943a88389c8fd70a6b0d061a88267465e143c7ca6ea2b75837ab20f5b60c739"


def vendor_values(stock, kind, sid):
    """Execute the stock remapper, including its real memcpy and SID decision."""
    machine = u.Uc(u.UC_ARCH_ARM, u.UC_MODE_THUMB)
    machine.mem_map(0x20000, 0x38000)
    machine.mem_write(0x20000, stock)
    machine.mem_map(0x03006000, 0x1000)
    machine.mem_write(0x03006200, struct.pack("<I", sid))
    machine.mem_map(0x04800000, 0x1000)
    parameters = bytearray(stock[0x38:0xb8])
    struct.pack_into("<I", parameters, 4, kind)
    machine.mem_write(0x53000, bytes(parameters))
    machine.reg_write(arm_const.UC_ARM_REG_R0, 0x53000)
    machine.reg_write(arm_const.UC_ARM_REG_SP, 0x57000)
    machine.reg_write(arm_const.UC_ARM_REG_LR, 0x57ff1)
    machine.reg_write(arm_const.UC_ARM_REG_CPSR, 0x13)
    values = []

    def write(m, access, address, size, value, user):
        assert size == 4 and address == 0x048000c0 + len(values) * 4
        values.append(value)

    machine.hook_add(u.UC_HOOK_MEM_WRITE, write, begin=0x048000c0, end=0x04800128)
    machine.hook_add(u.UC_HOOK_CODE, lambda m, *args: m.emu_stop(), begin=0x57ff0, end=0x57ff0)
    machine.emu_start(0x255b1, 0x58000, count=10000)
    assert machine.reg_read(arm_const.UC_ARM_REG_PC) == 0x57ff0
    assert len(values) == 27
    return values


def verify(directory, static_type=None, sid_values=None):
    manifest = json.loads((directory / "manifest.json").read_text())
    native = manifest.get("native_dram", False)
    blob = (directory / ("spl.bin" if native else "probe.bin")).read_bytes()
    sha = hashlib.sha256(blob).hexdigest()
    assert sha == manifest["spl_sha256" if native else "probe_sha256"]
    if native:
        symbols = {name: int(address, 16) for address, name in re.findall(
            r"^([0-9a-f]+) [a-zA-Z] (\w+)$", (directory / "symbols.txt").read_text(), re.M)}
        config = (directory / "u-boot-2026.10/.config").read_text()
        arch, r0, sp, lr, pc = (u.UC_ARCH_ARM64, arm64_const.UC_ARM64_REG_X0,
            arm64_const.UC_ARM64_REG_SP, arm64_const.UC_ARM64_REG_X30, arm64_const.UC_ARM64_REG_PC)
    else:
        symbols = {name: int(address, 16) for address, name in re.findall(
            r"^\s*([0-9a-f]+)\s+[0-9a-f]+\s+[0-9a-f]+\s+\d+\s+(\w+)\s*$",
            (directory / "probe.map").read_text(), re.M)}
        config = (directory / "shim.h").read_text()
        arch, r0, sp, lr, pc = (u.UC_ARCH_ARM, arm_const.UC_ARM_REG_R0,
            arm_const.UC_ARM_REG_SP, arm_const.UC_ARM_REG_LR, arm_const.UC_ARM_REG_PC)
    variant = int(bool(re.search(r"(?:#define |^)CONFIG_DRAM_SUNXI_PHY_ADDR_MAP_1(?: |=)", config, re.M)))
    archive = ROOT / "work/h700-loader/mainline/u-boot-v2026.10.tar.gz"
    assert hashlib.sha256(archive.read_bytes()).hexdigest() == PIN
    with tarfile.open(archive) as tar:
        upstream = tar.extractfile("u-boot-2026.10/arch/arm/mach-sunxi/dram_sun50i_h616.c").read().decode()
    variants = re.findall(r"static const u8 phy_init\[\] = \{(.*?)\n\};", upstream, re.S)
    assert len(variants) == 2
    tables = {}
    for wiring, body in zip((1, 0), variants):
        values = re.findall(r"0x[0-9a-f]+", body)
        assert len(values) == 81
        for index, kind in enumerate((3, 7, 8)):
            tables[wiring, kind] = [int(value, 16) for value in values[index * 27:(index + 1) * 27]]
    stock = None
    if sid_values:
        assert native and static_type is None
        stock = (directory / "restore-source.bin").read_bytes()
        assert len(stock) == 65536 and hashlib.sha256(stock).hexdigest() == manifest["restore_source_sha256"]
    cases = []
    for kind, sid in itertools.product((7, 3, 8), sid_values or (None,)):
        machine = u.Uc(arch, u.UC_MODE_ARM)
        machine.mem_map(0x20000, 0x38000)
        machine.mem_write(0x20000, blob)
        machine.mem_map(0x03000000, 0x08000000)
        if sid is not None: machine.mem_write(0x03006200, struct.pack("<I", sid))
        machine.mem_write(0x53000, struct.pack("<12I", 672, kind, 0x06060606, 0x0d0d0d0d,
            0x1919, 0x9988eeee, 0, 1, 0x2fb08080, 0x402f4489, 0x10121113, 0x0d0e0e0e))
        machine.mem_write(0x53100, bytes((10, 15, 1, 1)))
        machine.reg_write(r0, 0x53000)
        machine.reg_write(r0 + 1, 0x53100)
        machine.reg_write(sp, 0x52a00)
        machine.reg_write(lr, 0x55ff0)
        if not native: machine.reg_write(arm_const.UC_ARM_REG_CPSR, 0x13)
        stores = []

        def code(m, address, size, user):
            if address in {symbols.get(name) for name in ("mctl_await_completion", "udelay", "__udelay")}:
                m.reg_write(r0, 0)
                m.reg_write(pc, m.reg_read(lr))
            elif address in {symbols.get(name) for name in ("panic", "dram_panic")}:
                raise AssertionError(f"type {kind} panicked before PHY writes at {address:#x}")

        def write(m, access, address, size, value, user):
            assert size == 4 and address == 0x048000c0 + len(stores) * 4
            stores.append(value)
            if len(stores) == 27: m.emu_stop()

        machine.hook_add(u.UC_HOOK_CODE, code)
        machine.hook_add(u.UC_HOOK_MEM_WRITE, write, begin=0x048000c0, end=0x04800128)
        machine.emu_start(symbols["mctl_core_init"], 0x58000, count=100000)
        expected = tables[variant, static_type or kind]
        if sid is not None:
            expected = vendor_values(stock, kind, sid)
            matching = [wiring for wiring in (0, 1) if expected == tables[wiring, kind]]
            assert len(matching) == 1, "vendor special remap is not an upstream map0/map1 table"
        assert stores == expected, (kind, "actual PHY writes", stores, "expected", expected)
        cases.append({"candidate_type": kind, "sid": hex(sid) if sid is not None else None,
                      "vendor_map_variant": matching[0] if sid is not None else None,
                      "phy_register_values": stores})
    return {"passed": True, "binary_sha256": sha, "native_aarch64": native,
            "configured_phy_address_map_variant": variant, "runtime_sid_vendor_oracle": bool(sid_values),
            "vendor_boot0_sha256": hashlib.sha256(stock).hexdigest() if stock else None,
            "upstream_archive_sha256": PIN, "expected_static_type": static_type,
            "dram_mapped": False, "training_executed": False,
            "modeled_boundaries": ["delay", "completion wait"], "cases": cases}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--expect-static", type=int, choices=(3, 7, 8))
    parser.add_argument("--sid", type=lambda value: int(value, 0), action="append",
                        help="compare runtime wiring against actual vendor instructions for this SID")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    result = verify(args.directory, args.expect_static, args.sid)
    rendered = json.dumps(result, indent=2) + "\n"
    if args.report: args.report.write_text(rendered)
    print(rendered)
