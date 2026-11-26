#!/usr/bin/env python3
"""Execute the generated initcall wrapper and the vendor monitor PSCI path."""
import argparse
import hashlib
import importlib.util
import json
import random
import struct
import sys
from pathlib import Path

sys.dont_write_bytecode = True
import capstone
import unicorn as u
from unicorn import arm64_const as a

from build import toc_items

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
spec = importlib.util.spec_from_file_location("monitor_system_regs", HERE.parent / "spl/arm64-system.py")
system_model = importlib.util.module_from_spec(spec)
spec.loader.exec_module(system_model)
xreg = system_model.xreg

TEXT_VA, TEXT_PA = 0xffffff8008080000, 0x40080000
INITCALL_VA, STUB_PA = 0xffffff8008083954, 0x4098d680
PADDING_PA, PADDING_END = 0x4098d640, 0x4098e000
DISPLACED = 0xa9b77bfd
SOURCE_KERNEL_SHA = "1332da28ef5dd078963c98cc4e11de816b8fddc7c7b75dab93b79eda68d01bf0"
SOURCE_MONITOR_SHA = "ba5a3412aed772abd262048cde574555f5cb0638a2180ce414f603702ee50788"
GP14, GP15 = 0x07000138, 0x0700013c


def sha(data):
    return hashlib.sha256(data).hexdigest()


def branch(source, destination):
    delta = destination - source
    assert delta % 4 == 0 and -(1 << 27) <= delta < (1 << 27)
    return struct.pack("<I", 0x14000000 | ((delta >> 2) & 0x3ffffff))


def checked_inputs(directory, inventory):
    manifest = json.loads((directory / "manifest.json").read_text())
    assert manifest["native_diagnostic"] and manifest["kernel_initcall_probe"]
    assert not manifest["durable_install_eligible"]
    binaries = {name: (directory / f"{name}.bin").read_bytes()
                for name in ("kernel", "monitor", "image", "firmware", "loader")}
    for name, data in binaries.items():
        assert sha(data) == manifest[f"{name}_sha256"], f"{name} differs from manifest"
        if f"{name}_bytes" in manifest:
            assert len(data) == manifest[f"{name}_bytes"], f"{name} length differs from manifest"
    package = (ROOT / "work" / manifest["target"] / "boot-gzip/boot-package.bin").read_bytes()
    assert sha(package) == manifest["package_sha256"]
    items = toc_items(package)
    assert sha(items["monitor"]) == SOURCE_MONITOR_SHA
    assert sha((inventory / "env.bin").read_bytes()) == manifest["environment_sha256"]
    regions = {item["name"]: item for item in manifest["firmware_regions"]}
    for name in ("monitor", "loader"):
        region = regions[name]
        assert region["bytes"] == len(binaries[name])
        assert binaries["firmware"][region["offset"]:region["offset"] + region["bytes"]] == binaries[name]

    kernel, monitor = binaries["kernel"], binaries["monitor"]
    assert len(kernel) == 17686536 and len(monitor) == 107216
    hook_pa = TEXT_PA + INITCALL_VA - TEXT_VA
    hook_offset, stub_offset = hook_pa - TEXT_PA, STUB_PA - TEXT_PA
    assert kernel[hook_offset:hook_offset + 4] == branch(hook_pa, STUB_PA)
    stub = (directory / "initcall.bin").read_bytes()
    assert 0 < len(stub) <= 0x180 and len(stub) % 4 == 0
    assert kernel[stub_offset:stub_offset + len(stub)] == stub
    fault = (directory / "kernel-fault.bin").read_bytes()
    assert len(fault) == 0x9c0 and fault[0x40:0x1c0] == bytes(0x180)
    expected_padding = bytearray(fault)
    expected_padding[0x40:0x40 + len(stub)] = stub
    padding_offset = PADDING_PA - TEXT_PA
    assert kernel[padding_offset:PADDING_END - TEXT_PA] == expected_padding
    original_kernel = bytearray(kernel)
    original_kernel[hook_offset:hook_offset + 4] = struct.pack("<I", DISPLACED)
    original_kernel[padding_offset:PADDING_END - TEXT_PA] = bytes(PADDING_END - PADDING_PA)
    assert sha(original_kernel) == manifest["source_kernel_sha256"] == SOURCE_KERNEL_SHA

    assert monitor[0x98dc:0x98e0] == branch(0x480098dc, 0x480000c0)
    monitor_probe = (directory / "monitor-probe.bin").read_bytes()
    assert len(monitor_probe) == 40 and monitor[0xc0:0xf8] == monitor_probe + bytes(16)
    assert monitor[0x159d0:0x159d8] == bytes.fromhex("f04748a9e0039fd6")
    original_monitor = bytearray(monitor)
    original_monitor[0xc0:0xf8] = bytes(56)
    original_monitor[0x98dc:0x98e0] = struct.pack("<I", 0xa9be7bfd)
    assert bytes(original_monitor) == items["monitor"]
    return manifest, binaries, bytes(original_kernel), bytes(original_monitor), items


def wrapper_proof(kernel, original_kernel):
    rng = random.Random(0x700)
    cases = []
    for slide in (0, 0x2000000):
        hook = INITCALL_VA + slide
        stub = TEXT_VA + STUB_PA - TEXT_PA + slide
        stack, incoming_sp = 0xffffff800e000000, 0xffffff800e008000
        for flags in range(16):
            incoming = [rng.getrandbits(64) for _ in range(31)]
            incoming[0] = 0xffffff8008100000 + 4 * flags + slide
            nzcv = flags << 28
            m = u.Uc(u.UC_ARCH_ARM64, u.UC_MODE_ARM)
            m.ctl_set_cpu_model(a.UC_CPU_ARM64_A53)
            m.mem_map(hook & ~0xfff, 0x1000)
            m.mem_map(stub & ~0xfff, 0x1000)
            m.mem_map(stack, 0x10000)
            m.mem_write(hook, kernel[0x3954:0x3958])
            offset = STUB_PA - TEXT_PA
            m.mem_write(stub, kernel[offset:offset + 0x180])
            for index, value in enumerate(incoming):
                m.reg_write(xreg(index), value)
            m.reg_write(a.UC_ARM64_REG_SP, incoming_sp)
            m.reg_write(a.UC_ARM64_REG_NZCV, nzcv)
            calls = []

            def code(machine, address, size, data):
                if bytes(machine.mem_read(address, 4)) == b"\x03\x00\x00\xd4":
                    assert machine.reg_read(xreg(0)) == 0x84000000
                    assert machine.reg_read(xreg(1)) == incoming[0]
                    assert machine.reg_read(a.UC_ARM64_REG_SP) == incoming_sp - 256
                    calls.append(address)
                    # Include x8..x17: the vendor A64 vector restores stale
                    # secure-stack copies rather than the incoming scratch.
                    for index in range(31):
                        machine.reg_write(xreg(index), rng.getrandbits(64))
                    machine.reg_write(a.UC_ARM64_REG_NZCV, nzcv ^ 0xf0000000)
                    machine.reg_write(a.UC_ARM64_REG_PC, address + 4)

            m.hook_add(u.UC_HOOK_CODE, code)
            m.emu_start(hook, hook + 4, count=1000)
            assert len(calls) == 1, f"expected one PSCI_VERSION call, got {len(calls)}"
            restored = [m.reg_read(xreg(i)) for i in range(31)]
            assert restored == incoming, [(i, hex(incoming[i]), hex(restored[i]))
                                          for i in range(31) if incoming[i] != restored[i]]
            assert m.reg_read(a.UC_ARM64_REG_NZCV) == nzcv, "NZCV changed"
            assert m.reg_read(a.UC_ARM64_REG_SP) == incoming_sp - 0x90, "displaced frame differs"
            assert m.reg_read(a.UC_ARM64_REG_PC) == hook + 4, "wrong resume PC"

            oracle = u.Uc(u.UC_ARCH_ARM64, u.UC_MODE_ARM)
            oracle.mem_map(0x1000, 0x1000)
            oracle.mem_map(stack, 0x10000)
            oracle.mem_write(0x1000, original_kernel[0x3954:0x3958])
            oracle.reg_write(xreg(29), incoming[29])
            oracle.reg_write(xreg(30), incoming[30])
            oracle.reg_write(a.UC_ARM64_REG_SP, incoming_sp)
            oracle.emu_start(0x1000, 0x1004, count=1)
            assert oracle.reg_read(a.UC_ARM64_REG_SP) == m.reg_read(a.UC_ARM64_REG_SP)
            assert bytes(oracle.mem_read(incoming_sp - 0x90, 16)) == bytes(m.mem_read(incoming_sp - 0x90, 16))
            cases.append(dict(slide=hex(slide), nzcv=hex(nzcv), initcall=hex(incoming[0]),
                              smc_pc=hex(calls[0]), resume_pc=hex(hook + 4)))
    return dict(passed=True, cases=len(cases), all_gpr_nzcv_restored=True, original_prologue_effect=True,
                kaslr_slides=["0x0", "0x2000000"], smc_return_modeled=True, details=cases)


def monitor_case(monitor, items, boot0, arguments, patched):
    monitor = bytearray(monitor)
    monitor[0x30:0xb0] = boot0[0x38:0xb8]
    monitor[0x24:0x2c] = boot0[0xf0:0xf8]
    struct.pack_into("<I", monitor, 0xb0, 0x4a200000)
    m = u.Uc(u.UC_ARCH_ARM64, u.UC_MODE_ARM)
    m.ctl_set_cpu_model(a.UC_CPU_ARM64_A53)
    for address, size in ((0x20000, 0x38000), (0x01000000, 0x09000000), (0x40000000, 0x10000000)):
        m.mem_map(address, size)
    m.mem_write(0x48000000, bytes(monitor))
    for name, address in (("u-boot", 0x4a000000), ("dtbo", 0x4a100000), ("dtb", 0x4a200000)):
        m.mem_write(address, items[name])
    m.mem_write(0x03021004, struct.pack("<I", 4))
    m.reg_write(a.UC_ARM64_REG_PSTATE, 0x3cd)
    for index, value in enumerate((0x48000000, 0x08100044, 3, 0x09010044)):
        m.reg_write(xreg(index), value)
    system = system_model.SystemRegisters(3)
    md = capstone.Cs(capstone.CS_ARCH_ARM64, capstone.CS_MODE_ARM)
    state = dict(eret=[], rtc=[], instructions=0)

    def code(machine, address, size, data):
        state["instructions"] += 1
        ins = next(md.disasm(bytes(machine.mem_read(address, 4)), address), None)
        assert ins is not None
        if ins.mnemonic == "eret":
            state["eret"].append(dict(elr=hex(system.registers["elr_el3"]),
                                      spsr=hex(system.registers["spsr_el3"])))
            machine.emu_stop()
        elif system.execute(machine, ins):
            return
        elif ins.mnemonic == "mrs" and "cntpct_el0" in ins.op_str:
            index = int(ins.op_str.split(",")[0][1:])
            machine.reg_write(xreg(index), state["instructions"] * 24)
            machine.reg_write(a.UC_ARM64_REG_PC, address + 4)

    def read(machine, access, address, size, value, data):
        if address == 0x05000014:
            machine.mem_write(address, struct.pack("<I", 0x60))

    def write(machine, access, address, size, value, data):
        if address in (GP14, GP15):
            state["rtc"].append((address, value))

    m.hook_add(u.UC_HOOK_CODE, code)
    m.hook_add(u.UC_HOOK_MEM_READ, read, begin=0x05000000, end=0x05000020)
    m.hook_add(u.UC_HOOK_MEM_WRITE, write, begin=GP14, end=GP15 + 3)
    m.emu_start(0x48000000, 0x50000000, count=1000000)
    assert state["eret"] == [dict(elr="0x4a000000", spsr="0x1d3")]
    secure_sp = m.reg_read(a.UC_ARM64_REG_SP)
    saved_scratch = bytes(m.mem_read(secure_sp + 0x40, 0x50))
    expected_gp14, expected_gp15 = 0x14000014, 0x15000015
    m.mem_write(GP14, struct.pack("<2I", expected_gp14, expected_gp15))
    state["rtc"].clear()
    expected_trace, returns = [], []
    for call_index, x1 in enumerate(arguments):
        expected_return = 0xffffff800898d6d4 + call_index * 4
        system.registers.update(esr_el3=0x17 << 26, spsr_el3=0xa00003c5, elr_el3=expected_return)
        m.reg_write(a.UC_ARM64_REG_SP, secure_sp)
        incoming = [0x1111000000000000 + i * 0x101 for i in range(31)]
        incoming[0], incoming[1] = 0x84000000, x1
        for index, value in enumerate(incoming):
            m.reg_write(xreg(index), value)
        m.emu_start(system.registers["vbar_el3"] + 0x400, 0x50000000, count=1000000)
        assert len(state["eret"]) == call_index + 2
        assert state["eret"][-1] == dict(elr=hex(expected_return), spsr="0xa00003c5")
        assert m.reg_read(xreg(0)) == 0x10000
        assert bytes(m.mem_read(secure_sp + 0x40, 0x50)) == saved_scratch
        if patched:
            if x1 >> 63:
                expected_gp14 = x1 & 0xffffffff
                expected_trace.append((GP14, expected_gp14))
            else:
                expected_gp15 = 0x84000000
                expected_trace.append((GP15, expected_gp15))
        assert bytes(m.mem_read(GP14, 8)) == struct.pack("<2I", expected_gp14, expected_gp15)
        returns.append(dict(regs=[m.reg_read(xreg(i)) for i in range(31)],
                            sp=m.reg_read(a.UC_ARM64_REG_SP), system=dict(system.registers)))
    assert state["rtc"] == expected_trace, (state["rtc"], expected_trace)
    return dict(trace=[dict(address=hex(address), value=hex(value)) for address, value in state["rtc"]],
                returns=returns, gp14=hex(expected_gp14), gp15=hex(expected_gp15))


def monitor_proof(monitor, original_monitor, items, boot0):
    cases = []
    for arguments in ((0,), (0xffffff8008100010,), (0xffffff800898d680,), (0xffffff8008100010, 0)):
        baseline = monitor_case(original_monitor, items, boot0, arguments, False)
        candidate = monitor_case(monitor, items, boot0, arguments, True)
        assert baseline["returns"] == candidate["returns"], "monitor hook changed returned context"
        cases.append(dict(x1=list(map(hex, arguments)), rtc_trace=candidate["trace"],
                          result="0x10000", gp14=candidate["gp14"], gp15=candidate["gp15"],
                          baseline_return_context_identical=True))
    return dict(passed=True, original_eret=True, actual_vendor_path=True, cases=cases,
                initcall_survives_ordinary_psci=True,
                exception_entry_and_system_registers_modeled=True)


def verify(directory, inventory=None):
    manifest = json.loads((directory / "manifest.json").read_text())
    inventory = inventory or ROOT / "work/h700-loader" / (manifest["target"] + "-inventory-v1")
    manifest, binaries, original_kernel, original_monitor, items = checked_inputs(directory, inventory)
    boot0 = (inventory / "boot0.bin").read_bytes()
    result = dict(passed=True, generation=manifest["generation"],
                  kernel_sha256=manifest["kernel_sha256"], monitor_sha256=manifest["monitor_sha256"],
                  loader_sha256=manifest["loader_sha256"], image_sha256=manifest["image_sha256"],
                  firmware_sha256=manifest["firmware_sha256"], boot0_sha256=sha(boot0),
                  wrapper=wrapper_proof(binaries["kernel"], original_kernel),
                  monitor=monitor_proof(binaries["monitor"], original_monitor, items, boot0))
    (directory / "initcall-verification.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({key: result[key] for key in ("passed", "generation", "kernel_sha256", "monitor_sha256")}, indent=2))
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--inventory", type=Path)
    args = parser.parse_args()
    verify(args.directory.resolve(), args.inventory.resolve() if args.inventory else None)
