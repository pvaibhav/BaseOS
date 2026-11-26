#!/usr/bin/env python3
"""Run actual SPL cache transitions and prove omitted cleanup fails PoC checks."""
import argparse
import hashlib
import importlib.util
import json
import re
import struct
from pathlib import Path

import capstone
import unicorn as u
from unicorn import arm64_const as a

HERE = Path(__file__).resolve().parent


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def verify(directory):
    system_module = module("cache_system", HERE / "arm64-system.py")
    visibility_module = module("cache_visibility", HERE / "cache-visibility.py")
    manifest = json.loads((directory / "manifest.json").read_text())
    blob = (directory / "spl.bin").read_bytes()
    assert hashlib.sha256(blob).hexdigest() == manifest["spl_sha256"]
    symbols = {name: int(address, 16) for address, name in re.findall(
        r"^([0-9a-f]+) [a-zA-Z] (\w+)$", (directory / "symbols.txt").read_text(), re.M)}
    decoder = capstone.Cs(capstone.CS_ARCH_ARM64, capstone.CS_MODE_ARM)
    off = symbols["baseos_native_cache_off"]
    calls = [ins for ins in decoder.disasm(blob[off - 0x20000:off - 0x20000 + 80], off) if ins.mnemonic == "bl"]
    assert len(calls) == 1 and int(calls[0].op_str.removeprefix("#"), 16) == symbols["__asm_flush_dcache_all"]
    results = []
    for omit_clean in (False, True):
        machine = u.Uc(u.UC_ARCH_ARM64, u.UC_MODE_ARM)
        machine.ctl_set_cpu_model(a.UC_CPU_ARM64_A53)
        machine.mem_map(0x20000, 0x38000)
        machine.mem_map(0x40000000, 0x10000000)
        machine.mem_write(0x20000, blob)
        if omit_clean: machine.mem_write(calls[0].address, bytes.fromhex("1f2003d5"))
        # One real CPU store while caching is enabled.
        machine.mem_write(0x57f00, bytes.fromhex("200000f9c0035fd6"))
        system = system_module.SystemRegisters(3)
        visibility = visibility_module.CacheVisibility(machine, manifest["native_loader"]["image_bytes"])
        machine.hook_add(u.UC_HOOK_MEM_WRITE,
                         lambda m, access, address, size, value, data: visibility.store(address, size),
                         begin=0x45000000, end=0x4a3000e7)
        decoded = {}
        def code(m, pc, size, data):
            if pc == 0x55ff0: m.emu_stop(); return
            if pc not in decoded: decoded[pc] = next(decoder.disasm(bytes(m.mem_read(pc, 4)), pc))
            if system.execute(m, decoded[pc]): visibility.operation(*system.accesses[-1])
        machine.hook_add(u.UC_HOOK_CODE, code)
        def invoke(address):
            machine.reg_write(a.UC_ARM64_REG_SP, 0x52a00)
            machine.reg_write(a.UC_ARM64_REG_X30, 0x55ff0)
            machine.emu_start(address, 0x58000, count=100000)
            assert machine.reg_read(a.UC_ARM64_REG_PC) == 0x55ff0
        invoke(symbols["baseos_native_cache_on"])
        assert machine.reg_read(a.UC_ARM64_REG_X0) == 0 and visibility.enabled
        machine.reg_write(a.UC_ARM64_REG_X0, 0x0123456789abcdef)
        machine.reg_write(a.UC_ARM64_REG_X1, 0x45000000)
        invoke(0x57f00)
        assert visibility.dirty == {0x45000000}
        invoke(off)
        rejected = False
        try: visibility.verify()
        except AssertionError: rejected = True
        assert rejected == omit_clean
        assert not system.registers["sctlr_el3"] & 0x1005
        results.append({"cleanup_call_nopped": omit_clean, "poc_rejected": rejected,
                        "complete_clean_sweeps": visibility.complete_cleans})
    report = {"passed": True, "spl_sha256": manifest["spl_sha256"], "cases": results,
              "mutation_site": hex(calls[0].address), "cache_geometry_modeled": True,
              "hardware_coherency_tested": False}
    (directory / "cache-verification.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    verify(parser.parse_args().directory)
