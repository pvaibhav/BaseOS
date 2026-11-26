#!/usr/bin/env python3
"""Execute the vendor monitor's real first entry and inspect its BL33 return."""
import argparse
import hashlib
import importlib.util
import json
import struct
from pathlib import Path

import capstone
import unicorn as u
from unicorn import arm64_const as a
HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("monitor_system_regs", HERE / "arm64-system.py")
system_model = importlib.util.module_from_spec(spec)
spec.loader.exec_module(system_model)


def verify(package_path, boot0_path, output, traced=None, rtc_state=None, native_loader=None):
    package, boot0 = package_path.read_bytes(), boot0_path.read_bytes()
    items = {}
    for index in range(struct.unpack_from("<I", package, 32)[0]):
        at = 64 + 368 * index
        name = package[at:at + 64].split(b"\0", 1)[0].decode()
        offset, size = struct.unpack_from("<2I", package, at + 64)
        items[name] = package[offset:offset + size]
    native_manifest = json.loads((native_loader / "manifest.json").read_text()) if native_loader else None
    monitor = bytearray((native_loader / "monitor.bin").read_bytes() if native_manifest else items["monitor"])
    if native_manifest:
        assert hashlib.sha256(monitor).hexdigest() == native_manifest["monitor_sha256"]
        if not native_manifest.get("monitor_psci_probe"):
            assert monitor == items["monitor"], "clean monitor differs from source"
    monitor[0x30:0xb0] = boot0[0x38:0xb8]
    monitor[0x24:0x2c] = boot0[0xf0:0xf8]
    struct.pack_into("<I", monitor, 0xb0, 0x4a200000)
    m = u.Uc(u.UC_ARCH_ARM64, u.UC_MODE_ARM)
    m.ctl_set_cpu_model(a.UC_CPU_ARM64_A53)
    for address, size in ((0x20000, 0x38000), (0x01000000, 0x09000000), (0x40000000, 0x10000000)):
        m.mem_map(address, size)
    m.mem_write(0x48000000, (traced / "monitor-traced.bin").read_bytes() if traced else bytes(monitor))
    m.mem_write(0x4a000000, (traced / "uboot-traced.bin").read_bytes() if traced else items["u-boot"])
    if native_loader:
        assert not traced
        native_manifest = json.loads((native_loader / "manifest.json").read_text())
        blob = bytearray((native_loader / "loader.bin").read_bytes())
        assert hashlib.sha256(blob).hexdigest() == native_manifest["loader_sha256"]
        blob[0x30:0xb0] = boot0[0x38:0xb8]
        struct.pack_into("<I", blob, 0x4f8, struct.unpack_from("<I", boot0, 0x54)[0] >> 16)
        struct.pack_into("<I", blob, 0xe4, 1)
        blob[0x4e8:0x4eb] = bytes((0, 1, 0))
        m.mem_write(0x4a000000, bytes(blob))
        arm = capstone.Cs(capstone.CS_ARCH_ARM, capstone.CS_MODE_ARM)
        smcs = [ins.address for ins in arm.disasm(bytes(blob[0x1000:0x1200]), 0x4a001000) if ins.mnemonic == "smc"]
        assert len(smcs) == 1
        native_smc_return = smcs[0] + 4
        m.mem_write(0x4a300000, b"\xa5" * 0x200000)
    if traced: m.mem_write(0x49000000, (traced / "trace-code.bin").read_bytes())
    m.mem_write(0x4a100000, items["dtbo"])
    m.mem_write(0x4a200000, items["dtb"])
    m.mem_write(0x03021004, struct.pack("<I", 4))  # 160 GIC interrupts
    rtc_seed = json.loads(rtc_state.read_text())["rtc_general_purpose"] if rtc_state else {}
    for name, value in rtc_seed.items():
        m.mem_write(0x07000100 + 4 * int(name[2:]), struct.pack("<I", value))
    if traced: m.mem_write(0x0700013c, struct.pack("<I", 0x42530022))
    m.reg_write(a.UC_ARM64_REG_PSTATE, 0x3cd)
    m.reg_write(a.UC_ARM64_REG_X0, 0x48000000)
    m.reg_write(a.UC_ARM64_REG_X1, 0x08100044)
    m.reg_write(a.UC_ARM64_REG_X2, 3)
    m.reg_write(a.UC_ARM64_REG_X3, 0x09010044)
    system = system_model.SystemRegisters(3)
    # Disassemble per instruction below too: the header contains data and can
    # stop a linear disassembler before reaching executable code.
    md = capstone.Cs(capstone.CS_ARCH_ARM64, capstone.CS_MODE_ARM)
    state = {"instructions": 0, "eret": [], "errors": []}
    native_kernel_entry = []
    console = []
    trace = []
    reserved_header_reads = []
    mmio_writes = {}
    rtc_reads = []
    native_region_accesses = []
    def code(machine, address, size, data):
        if native_loader and address == 0x40080000:
            native_kernel_entry.append([machine.reg_read(a.UC_ARM64_REG_X0 + i) for i in range(4)])
            machine.emu_stop()
            return
        state["instructions"] += 1
        raw = bytes(machine.mem_read(address, 4))
        ins = next(md.disasm(raw, address), None)
        if not ins: return
        if ins.mnemonic == "eret":
            # SPSR_EL3: op0=3, op1=6, CRn=4, CRm=0, op2=0.
            spsr = system.registers.get("spsr_el3", 0)
            state["eret"].append({"entry": hex(system.registers.get("elr_el3", 0)), "spsr": hex(spsr),
                                   "x0": hex(machine.reg_read(a.UC_ARM64_REG_X0)),
                                   "x1": hex(machine.reg_read(a.UC_ARM64_REG_X1)),
                                   "x2": hex(machine.reg_read(a.UC_ARM64_REG_X2)),
                                   "x3": hex(machine.reg_read(a.UC_ARM64_REG_X3))})
            machine.emu_stop()
        elif system.execute(machine, ins): return
        elif ins.mnemonic == "mrs" and "cntpct_el0" in ins.op_str:
            register = int(ins.op_str.split(",")[0][1:])
            machine.reg_write(a.UC_ARM64_REG_X0 + register, state["instructions"] * 24)
            machine.reg_write(a.UC_ARM64_REG_PC, address + 4)
    def read(machine, access, address, size, value, data):
        if address == 0x05000014: machine.mem_write(address, struct.pack("<I", 0x60))
        if 0x480000c0 <= address < 0x480000f8: reserved_header_reads.append(hex(address))
        if 0x07000100 <= address < 0x07000140: rtc_reads.append(hex(address))
    def write(machine, access, address, size, value, data):
        if address == 0x05000000: console.append(chr(value & 255))
        if address == 0x0700013c: trace.append(value)
        if not 0x03020000 <= address < 0x03040000 and address != 0x05000000:
            mmio_writes[hex(address)] = hex(value)
    code_hook = m.hook_add(u.UC_HOOK_CODE, code)
    m.hook_add(u.UC_HOOK_MEM_READ, read, begin=0x05000000, end=0x480000f8)
    m.hook_add(u.UC_HOOK_MEM_WRITE, write, begin=0x01000000, end=0x0a000000)
    if native_loader:
        region_hook = m.hook_add(u.UC_HOOK_MEM_READ | u.UC_HOOK_MEM_WRITE,
                   lambda machine, access, address, size, value, data: native_region_accesses.append(hex(address)),
                   begin=0x4a300000, end=0x4a4fffff)
    try:
        m.emu_start(0x48000000, 0x50000000, count=1000000, timeout=10000000)
    except Exception as error:
        state["errors"].append(str(error))
    assert not state["errors"] and state["eret"][-1]["entry"] == "0x4a000000" and state["eret"][-1]["spsr"] == "0x1d3"
    if native_loader:
        # Inject the architectural exception caused by the separately executed
        # A32 SMC. Actual vendor vector, dispatch, context restore and ERET run.
        loader_proof = json.loads((native_loader / "verification.json").read_text())
        assert loader_proof["passed"] and loader_proof["loader_sha256"] == native_manifest["loader_sha256"]
        system.registers.update(esr_el3=0x13 << 26, spsr_el3=0x1d3, elr_el3=native_smc_return,
                                sctlr_el1=loader_proof["final_a32_sctlr"])
        target = native_manifest.get("kernel_smc_entry", 0x40080000)
        for index, value in enumerate((0x8000ff04, target, 0x44000000, 1)):
            m.reg_write(a.UC_ARM64_REG_X0 + index, value)
        vector = system.registers["vbar_el3"] + 0x600
        try:
            m.emu_start(vector, 0x50000000, count=1000000, timeout=10000000)
        except Exception as error:
            state["errors"].append(str(error))
        if "kernel_smc_entry" in native_manifest:
            assert not state["errors"] and state["eret"][-1]["entry"] == hex(target)
            assert not native_region_accesses
            assert bytes(m.mem_read(0x4a300000, 0x200000)) == b"\xa5" * 0x200000
            m.hook_del(region_hook)
            m.hook_del(code_hook)
            if native_manifest.get("entry_checksum_witness"):
                m.mem_write(0x40080000, (native_loader / "kernel.bin").read_bytes())
                m.mem_write(0x44000000, (native_loader / "verified-kernel.dtb").read_bytes())
                m.mem_write(0x4a300100, (native_loader / "kernel-entry-check.bin").read_bytes())
            m.hook_add(u.UC_HOOK_CODE, code, begin=0x40080000, end=0x40080000)
            secure_sp = m.reg_read(a.UC_ARM64_REG_SP)
            m.emu_start(target, 0x50000000, count=250000000)
            assert native_kernel_entry == [[0x44000000, 0, 0, 0]]
            state["native_kernel_entry_r0_r3"] = list(map(hex, native_kernel_entry[0]))
            if native_manifest.get("monitor_psci_probe"):
                # Model a new lower-A64 SMC exception, including banked EL3 SP.
                eret_count, trace_count = len(state["eret"]), len(trace)
                system.registers.update(esr_el3=0x17 << 26, spsr_el3=0x3c5,
                                        elr_el3=0xffffff8008090124)
                m.reg_write(a.UC_ARM64_REG_SP, secure_sp)
                saved_scratch = list(struct.unpack("<2Q", m.mem_read(secure_sp + 0x80, 16)))
                for index in range(8):
                    m.reg_write(a.UC_ARM64_REG_X0 + index, 0x84000000 if index == 0 else 0)
                m.reg_write(a.UC_ARM64_REG_X16, 0x1616161616161616)
                m.reg_write(a.UC_ARM64_REG_X17, 0x1717171717171717)
                query_hook = m.hook_add(u.UC_HOOK_CODE, code)
                m.emu_start(system.registers["vbar_el3"] + 0x400, 0x50000000, count=1000000)
                m.hook_del(query_hook)
                assert not state["errors"] and len(state["eret"]) == eret_count + 1
                assert state["eret"][-1]["entry"] == "0xffffff8008090124"
                assert m.reg_read(a.UC_ARM64_REG_X0) == 0x10000
                restored = [m.reg_read(a.UC_ARM64_REG_X16), m.reg_read(a.UC_ARM64_REG_X17)]
                # A64's vector deliberately skips re-saving x8..x17; compare
                # with its saved context, not the incoming caller scratch.
                assert restored == saved_scratch, (list(map(hex, restored)), list(map(hex, saved_scratch)))
                expected_trace = [0x84000000] if native_manifest.get("kernel_initcall_probe") else [0x84000000, 0x94000000]
                assert trace[trace_count:] == expected_trace
                state["psci_query_probe"] = {"passed": True, "smc_id": "0x84000000", "result": "0x10000",
                                              "rtc_trace": list(map(hex, trace[trace_count:])),
                                              "return": state["eret"][-1], "saved_x16_x17_preserved": True}
                del state["eret"][eret_count:]
                del trace[trace_count:]
        state["native_kernel_smc_exception_modeled"] = True
        state["native_loader_sha256"] = native_manifest["loader_sha256"]
        state["native_region_accesses"] = native_region_accesses
        assert not native_region_accesses
        if "kernel_smc_entry" not in native_manifest:
            assert bytes(m.mem_read(0x4a300000, 0x200000)) == b"\xa5" * 0x200000
    state.update(pc=hex(m.reg_read(a.UC_ARM64_REG_PC)), console="".join(console),
                 el3_registers_modeled=True, system_register_accesses=system.accesses,
                 package_sha256=hashlib.sha256(package).hexdigest(), boot0_sha256=hashlib.sha256(boot0).hexdigest())
    state["rtc_trace"] = trace
    translations = {}
    for address in (0x0700013c, 0x480000c0, 0x480000dc, 0x480000f8, 0x4801a300, 0x49000000):
        table = system.registers.get("ttbr0_el3", 0) & ~63
        path = []
        for shift in (30, 21, 12):
            descriptor = struct.unpack("<Q", m.mem_read(table + ((address >> shift) & 511) * 8, 8))[0]
            path.append(hex(descriptor))
            if descriptor & 3 != 3 or shift == 12: break
            table = descriptor & 0x0000fffffffff000
        translations[hex(address)] = path
    state["el3_translation_descriptors"] = translations
    state["reserved_header_data_reads"] = reserved_header_reads
    state["mmio_writes_except_gic_uart"] = mmio_writes
    state.update(rtc_seed=rtc_seed, rtc_reads=rtc_reads)
    if traced: state["traced_spl_sha256"] = json.loads((traced / "manifest.json").read_text())["spl_sha256"]
    output.write_text(json.dumps(state, indent=2) + "\n")
    print(json.dumps(state, indent=2))
    assert not state["errors"]
    if native_loader:
        assert state["eret"][-1] == {"entry": hex(native_manifest.get("kernel_smc_entry", 0x40080000)), "spsr": "0x3c5", "x0": "0x44000000",
                                     "x1": "0x0", "x2": "0x0", "x3": "0x0"}, state["eret"][-1]
    if traced:
        assert trace == [0x4a000000]
        for address in ("0x480000c0", "0x480000dc"):
            assert int(translations[address][-1], 16) & (1 << 54) == 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("package", type=Path)
    parser.add_argument("boot0", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--traced", type=Path)
    parser.add_argument("--rtc-state", type=Path)
    parser.add_argument("--native-loader", type=Path)
    args = parser.parse_args()
    verify(args.package, args.boot0, args.output, args.traced, args.rtc_state, args.native_loader)
