#!/usr/bin/env python3
"""Execute actual AArch64 SPL startup/SD code with no DRAM mapped."""
import argparse
from collections import Counter, deque
import importlib.util
import json
import re
import struct
import sys
from pathlib import Path

import capstone
import unicorn as u
from unicorn.arm64_const import UC_ARM64_REG_PC, UC_ARM64_REG_X0, UC_ARM64_REG_X30, UC_ARM64_REG_PSTATE

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "fastboot"))
spec = importlib.util.spec_from_file_location("early_sd_model", HERE.parent / "uboot-trace.py")
t = importlib.util.module_from_spec(spec)
spec.loader.exec_module(t)
system_spec = importlib.util.spec_from_file_location("spl_system_regs", HERE / "arm64-system.py")
system_model = importlib.util.module_from_spec(system_spec)
system_spec.loader.exec_module(system_model)


def fnv(data):
    value = 2166136261
    for byte in data: value = ((value ^ byte) * 16777619) & 0xffffffff
    return value


def geometry_model(source):
    """Use the stock trained geometry as explicit input to the training model."""
    words = list(struct.unpack_from("<32I", source, 0x38))
    cols, rows = words[6] & 15, (words[6] >> 4) & 255
    ranks, full_width = 1 + bool(words[7] & 0x1000), not bool(words[7] & 1)
    assert 8 <= cols <= 11 and 13 <= rows <= 17
    size = (1 << (cols + rows + 3)) * (4 if full_width else 2) * ranks
    assert size >> 20 == words[7] >> 16, "stock geometry and size disagree"
    # Check the selector's published block against an independent input model,
    # rather than requiring its output to equal an untrained header verbatim.
    words[6] = 0x3000 | (rows << 4) | cols
    words[7] = ((size >> 20) << 16) | (0x1000 if ranks == 2 else 0) | (0 if full_width else 1)
    return bytes((cols, rows, ranks, full_width)), size, struct.pack("<32I", *words)


class SparseCard(t.SDCard):
    def __init__(self, source, training=False, restore_primary=False, package=None, handoff=False):
        prefix = bytearray(1024)
        prefix[512:520] = b"EFI PART"
        struct.pack_into("<Q", prefix, 512 + 32, 0x3fffff)
        super().__init__(bytes(prefix))
        journal = bytearray(512)
        struct.pack_into("<4I", journal, 0, 0x50534f42, 1, 2, 10)
        struct.pack_into("<I", journal, 44 * 4, int(training))
        struct.pack_into("<I", journal, 45 * 4, 16 if restore_primary else 0)
        struct.pack_into("<I", journal, 47 * 4, int(handoff))
        struct.pack_into("<I", journal, 508, fnv(journal[:508]))
        self.blocks = {0x56000: bytes(journal)}
        self.blocks.update({0x56001 + i: source[i*512:(i+1)*512] for i in range(128)})
        if package:
            self.blocks.update({32800 + i: package[i*512:(i+1)*512] for i in range(len(package)//512)})

    def respond(self, index, arg, total):
        if index in (17, 18):
            return [0x900, 0, 0, 0], b"".join(self.blocks.get(arg+i, bytes(512)) for i in range(total//512))
        return super().respond(index, arg, total)


class PioHost(t.SDHost):
    def __init__(self, tracer, card, restore_primary=False):
        super().__init__(tracer, card)
        self.output, self.target, self.expected = bytearray(), 0, 0
        self.writes = []
        self.destination = 16 if restore_primary else 0x56200

    def read(self, off):
        if off == self.FIFO: return self.pio.popleft() if self.pio else 0
        if off == self.CTRL: return self.regs.get(off, 0) & ~7
        if off == self.STATUS: return (min(len(self.pio), 32) << 17) | (0 if self.pio else 4)
        return super().read(off)

    def write(self, off, value):
        if off == self.FIFO:
            assert self.expected and len(self.output) < self.expected
            self.output.extend(struct.pack("<I", value))
            if len(self.output) == self.expected:
                self.card.blocks.update({self.target+i: bytes(self.output[i*512:(i+1)*512])
                                         for i in range(self.expected//512)})
                self.writes.append((self.target, self.expected))
            return
        super().write(off, value)

    def command(self, value):
        index, arg = value & 63, self.regs.get(self.CMDARG, 0)
        total = self.regs.get(self.BYTCNT, 0) if value & 512 else 0
        self.commands.append({"cmd": index, "arg": arg, "bytes": total})
        self.regs[self.RINTSTS] = 0x400c if total > 512 else 12
        if value & 0x400:
            assert (arg == 0x56000 and total == 512) or (arg == self.destination and total == 65536), (index, arg, total)
            self.target, self.expected, self.output = arg, total, bytearray()
            self.resp = [0x900, 0, 0, 0]
        else:
            self.resp, payload = self.card.respond(index, arg, total)
            if total:
                assert payload is not None and len(payload) == total
                self.pio = deque(struct.unpack(f"<{total//4}I", payload))


def verify(directory, level=1, training=False, restore_primary=False, handoff=False, native_loader=None):
    manifest = json.loads((directory / "manifest.json").read_text())
    assert bool(native_loader) == bool(manifest.get("native_loader")), "supply matching --native-loader"
    blob = (directory / "spl.bin").read_bytes()
    machine = u.Uc(u.UC_ARCH_ARM64, u.UC_MODE_ARM)
    machine.ctl_set_cpu_model(u.arm64_const.UC_CPU_ARM64_A53)
    machine.mem_map(0x20000, 0x38000)
    machine.mem_map(0x03000000, 0x08000000)
    machine.mem_write(0x20000, blob)
    machine.reg_write(UC_ARM64_REG_PSTATE, 0x3c0 | {1: 5, 3: 13}[level])
    system = system_model.SystemRegisters(level)
    dram = manifest.get("native_dram", False)
    source = (directory / "restore-source.bin").read_bytes()
    modeled_geometry, modeled_ram_bytes, expected_parameters = geometry_model(source) if dram else (None, None, None)
    symbols = {}
    if dram:
        machine.mem_map(0x41000000, 0x4000)
        symbols = {name: int(address,16) for address,name in re.findall(
            r"^([0-9a-f]+) [a-zA-Z] (\w+)$", (directory / "symbols.txt").read_text(), re.M)}
    package = None
    if handoff:
        assert level == 3 and restore_primary
        package = (directory / "handoff-package.bin").read_bytes()
        import hashlib
        assert hashlib.sha256(package).hexdigest() == manifest["handoff_package_sha256"]
        for address, size in ((0x42e00000, 0x140000), (0x48000000, 0x40000), (0x4a000000, 0x300000)):
            machine.mem_map(address, size)
        if manifest.get("rtc_handoff_trace"): machine.mem_map(0x49000000, 0x1000)
        if native_loader:
            machine.mem_map(0x45000000, 0x1000000)
            machine.mem_map(0x4a300000, 0x200000)
    pmic_boundary = None
    if handoff and native_loader and manifest["native_loader"].get("native_record_version") == 2:
        begin = symbols["pmic_bus_read"]
        body = blob[begin - 0x20000:begin - 0x20000 + 24]
        decoder = capstone.Cs(capstone.CS_ARCH_ARM64, capstone.CS_MODE_ARM)
        instructions = [(i.mnemonic,i.op_str) for i in decoder.disasm(body,begin)]
        assert instructions == [("mov","x3, x1"),("mov","w4, #1"),("and","w1, w0, #0xff"),
                                ("mov","w2, w4"),("mov","w0, #0x34"),
                                ("b",f"#{symbols['i2c_read']:#x}")], "PMIC read primitive contract changed"
        import hashlib
        pmic_boundary = {"function":hex(begin),"instruction_bytes_sha256":hashlib.sha256(body).hexdigest(),
                         "instructions":instructions,"chip_address":0x34,"register_mask":0xff,
                         "address_bytes":1,"data_bytes":1,"buffer_from_argument":1,
                         "modeled_status":0,"fixture":"reg20=0, regf0=0, no PMIC writes"}
    clock = t.Clock()
    class Tracer:
        def next_expiry(self): return None
    tracer = Tracer()
    tracer.clock = clock
    card = SparseCard(source, training, restore_primary, package, handoff)
    visibility = None
    if native_loader:
        import hashlib
        native = manifest["native_loader"]
        assert native == json.loads((native_loader / "manifest.json").read_text())
        for name, offset in (("firmware", 40), ("image", 48)):
            payload = (native_loader / f"{name}.bin").read_bytes()
            assert hashlib.sha256(payload).hexdigest() == native[name + "_sha256"]
            start = native["boot_partition_lba"] + offset * 2048
            card.blocks.update({start + i: payload[i*512:(i+1)*512] for i in range(len(payload)//512)})
        if handoff:
            visibility_spec = importlib.util.spec_from_file_location("spl_cache_visibility", HERE / "cache-visibility.py")
            visibility_module = importlib.util.module_from_spec(visibility_spec)
            visibility_spec.loader.exec_module(visibility_module)
            visibility = visibility_module.CacheVisibility(machine, native["image_bytes"])
            machine.hook_add(u.UC_HOOK_MEM_WRITE,
                lambda m, access, address, size, value, data: visibility.store(address, size),
                begin=0x45000000, end=0x4a3000e7)
    host = PioHost(tracer, card, restore_primary)
    errors, reset, console, counts, dram_calls, jumps, voltages = [], [], [], Counter(), [], [], []
    pmic_bootreason_reads = []
    pmic_bootreason_argument_words = []
    def guard(callback):
        def inner(*args):
            try: callback(*args)
            except Exception as error:
                errors.append(repr(error))
                machine.emu_stop()
        return inner
    def get(at): return struct.unpack("<I", machine.mem_read(at, 4))[0]
    def put(at, value): machine.mem_write(at, struct.pack("<I", value))
    def read(m, access, address, size, value, data):
        counts[address] += 1
        if 0x04020000 <= address < 0x04021000:
            put(address, host.read(address - 0x04020000))
        elif address == 0x05000014:
            put(address, 0x60)
        elif address in (0x03001000, 0x03001010, 0x03001020):
            put(address, get(address) | (1 << 28))
    def write(m, access, address, size, value, data):
        if 0x04020000 <= address < 0x04021000: host.write(address - 0x04020000, value)
        elif address == 0x05000000 and size in (1, 4): console.append(chr(value & 255))
        elif address == 0x030090b8 and value == 0x31:
            reset.append(value)
            m.emu_stop()
    special = {}
    privileged = {}
    md = capstone.Cs(capstone.CS_ARCH_ARM64, capstone.CS_MODE_ARM)
    for offset in range(0, len(blob)-3, 4):
        ins = next(md.disasm(blob[offset:offset+4], 0x20000+offset), None)
        if ins and ins.mnemonic == "mrs" and any(reg in ins.op_str for reg in ("cntpct_el0", "cntfrq_el0")):
            register, counter_source = ins.op_str.split(", ")
            special[ins.address] = (int(register[1:]), counter_source)
        elif ins and ins.mnemonic in ("mrs", "msr", "tlbi", "dc", "ic"):
            privileged[ins.address] = ins
    def code(m, address, size, data):
        clock.ticks += 24
        if address in privileged and system.execute(m, privileged[address]):
            instruction = privileged[address]
            if visibility: visibility.operation(*system.accesses[-1])
            if native_loader and instruction.mnemonic == "msr" and instruction.op_str.startswith("sctlr_el3,"):
                control = system.registers["sctlr_el3"]
                if control & 1:
                    assert system.registers["mair_el3"] == 0xff00 and system.registers["tcr_el3"] == 0x80803519
                    assert system.registers["ttbr0_el3"] == 0x4a400000
                    assert struct.unpack("<2Q", m.mem_read(0x4a400000, 16)) == (0x4a401003, 0x4a402003)
                    low = struct.unpack("<512Q", m.mem_read(0x4a401000, 4096))
                    ram = struct.unpack("<512Q", m.mem_read(0x4a402000, 4096))
                    assert low[0] == 0x705
                    assert all(low[i] == (i << 21) | (1 << 54) | 0x401 for i in range(1, 512))
                    assert all(ram[i] == ((0x40000000 + (i << 21)) | 0x705 if i < 128 else 0) for i in range(512))
            if handoff and instruction.mnemonic == "msr" and instruction.op_str.startswith("rmr_el3,"):
                assert system.registers["rmr_el3"] == 3
                assert get(0x09010040) == 0x48000000 and get(0x09010044) == 0
                assert m.reg_read(UC_ARM64_REG_X0) == 0x48000000 and m.reg_read(UC_ARM64_REG_X0 + 1) == 0x08100044
                jumps.append(0x48000000)
                if native_loader:
                    assert not system.registers["sctlr_el3"] & 0x1005
                    assert sum(1 for operation, _, _ in system.accesses if operation == "dc") >= 128
                m.emu_stop()
            return
        if handoff and address == 0x48000000:
            jumps.append(address)
            assert m.reg_read(UC_ARM64_REG_X0) == 0x48000000
            m.emu_stop()
            return
        def returned(value):
            m.reg_write(UC_ARM64_REG_X0, value)
            m.reg_write(UC_ARM64_REG_PC, m.reg_read(UC_ARM64_REG_X30))
        if handoff and native_loader and address == symbols.get("pmic_bus_read"):
            raw_register = m.reg_read(UC_ARM64_REG_X0)
            pmic_bootreason_argument_words.append(raw_register)
            register = raw_register & 0xff  # actual pmic_bus_read masks W0 to one byte
            assert register in (0x20, 0, 0xf0), "unexpected bootreason PMIC read"
            m.mem_write(m.reg_read(UC_ARM64_REG_X0 + 1), bytes(1))
            pmic_bootreason_reads.append(register)
            returned(0); return
        if handoff and native_loader and address == symbols.get("pmic_bus_write"):
            raise AssertionError("unknown-reason/zero-f0 fixture must not write PMIC")
        if (training or handoff) and address == symbols["sunxi_dram_init"]:
            assert get(0x030090b8) == 0xb1, "native DRAM entered without the early watchdog"
            assert struct.unpack_from("<I", card.blocks[0x56000], 12)[0] == (30 if handoff else 22)
            dram_calls.append(address)
        if (training or handoff) and address == symbols["mctl_core_init"]: returned(1); return
        if (training or handoff) and address == symbols["mctl_auto_detect_dram_size"]:
            m.mem_write(m.reg_read(UC_ARM64_REG_X0 + 1), modeled_geometry)
            returned(0); return
        if (training or handoff) and address == symbols["mctl_check_memory"]: returned(1); return
        if handoff and address in [symbols.get(name) for name in ("axp_init", "axp_set_dcdc2", "axp_set_dcdc3")]:
            if address != symbols["axp_init"]: voltages.append(m.reg_read(UC_ARM64_REG_X0))
            returned(0); return
        if address in special:
            reg, source = special[address]
            m.reg_write(UC_ARM64_REG_X0 + reg, clock.ticks if source == "cntpct_el0" else 24000000)
            m.reg_write(UC_ARM64_REG_PC, address + 4)
    machine.hook_add(u.UC_HOOK_MEM_READ, guard(read), begin=0x03000000, end=0x0affffff)
    machine.hook_add(u.UC_HOOK_MEM_WRITE, guard(write), begin=0x03000000, end=0x0affffff)
    machine.hook_add(u.UC_HOOK_CODE, guard(code))
    try:
        machine.emu_start(0x20060, 0x58000, timeout=600000000 if native_loader else 60000000 if handoff else 30000000,
                          count=300000000 if native_loader else 50000000 if handoff else 10000000)
    except u.UcError as error:
        errors.append(f"{error} at {machine.reg_read(UC_ARM64_REG_PC):#x}")
    journal = card.blocks[0x56000]
    report = {"spl_sha256": manifest["spl_sha256"], "no_dram_mapped": not dram,
              "native_dram_model": dram, "training_modeled": training, "sram_before_dram": True, "entry_el": level,
              "geometry_modeled": list(modeled_geometry) if dram else None,
              "ram_bytes_modeled": modeled_ram_bytes,
              "restore_primary": restore_primary,
              "el3_registers_modeled": level == 3, "system_register_accesses": system.accesses,
              "monitor_handoff": bool(jumps), "pmic_voltage_writes_modeled_mv": voltages,
              "pmic_bootreason_register_reads": pmic_bootreason_reads,
              "pmic_bootreason_argument_words": pmic_bootreason_argument_words,
              "pmic_read_boundary": pmic_boundary,
              "pmic_bootreason_fixture": "reg20=0, regf0=0; successful reads, no clear required",
              "monitor_reset_modeled": handoff and bool(jumps),
              "reset": bool(reset), "stage": struct.unpack_from("<I", journal, 12)[0],
              "writes": host.writes, "commands": host.commands, "errors": errors,
              "console": "".join(console), "pc": hex(machine.reg_read(UC_ARM64_REG_PC)),
              "most_polled": [(hex(at), n) for at,n in counts.most_common(5)]}
    if not handoff: (directory / "verification.json").write_text(json.dumps(report, indent=2) + "\n")
    suffix = "-restore" if restore_primary else ""
    mode = "handoff" if handoff else "dram" if training else "control"
    (directory / f"verification-el{level}-{mode}{suffix}.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({key: report[key] for key in ("no_dram_mapped", "reset", "stage", "errors", "pc", "most_polled")}, indent=2))
    assert not errors and (bool(jumps) if handoff else reset) and report["stage"] == (34 if handoff else 2), report["console"]
    assert struct.unpack_from("<I", journal, 35 * 4)[0] == level * 4
    assert len(dram_calls) == int(training or handoff)
    assert b"".join(card.blocks[host.destination+i] for i in range(128)) == source
    assert fnv(journal[:508]) == struct.unpack_from("<I", journal, 508)[0]
    if handoff:
        if visibility:
            report["cache_visibility"] = visibility.verify()
            (directory / "verification-el3-handoff-restore.json").write_text(json.dumps(report, indent=2) + "\n")
        assert manifest["candidate_types"][0] == struct.unpack_from("<I", source, 0x3c)[0]
        assert bytes(machine.mem_read(0x48000030, 128)) == expected_parameters
        assert bytes(machine.mem_read(0x48000024, 8)) == source[0xf0:0xf8]
        assert get(0x480000b0) == 0x4a200000 and get(0x4a0004f8) == modeled_ram_bytes >> 20 and get(0x4a0000e4) == 1
        assert voltages == [940, 1100, {3: 1500, 7: 1200, 8: 1100}[manifest["candidate_types"][0]]]
        tree_bytes = bytes(machine.mem_read(0x4a200000, 0x23200))
        if native_loader:
            assert b"BSPLv001" not in tree_bytes
            record_version = native.get("native_record_version", 1)
            record = bytes(machine.mem_read(0x4a300000, native.get("native_record_bytes", 232)))
            assert struct.unpack_from("<2I", record, 4) == (record_version, len(record))
            assert struct.unpack_from("<Q", record, 16)[0] == modeled_ram_bytes
            assert record[52:180] == bytes(machine.mem_read(0x48000030, 128))
            assert record[200:232] == bytes.fromhex(native["generation"])
            if record_version == 2:
                assert struct.unpack_from("<2I", record, 232) == (5, 0)
                assert pmic_bootreason_reads == [0x20, 0xf0]
            clean_record = bytearray(record)
            clean_record[196:200] = bytes(4)
            assert fnv(clean_record) == struct.unpack_from("<I", record, 196)[0]
            assert bytes(machine.mem_read(0x45000000, native["image_bytes"])) == (native_loader / "image.bin").read_bytes()
            (directory / "native-record.bin").write_bytes(record)
            (directory / "monitor-native.bin").write_bytes(bytes(machine.mem_read(0x48000000, 0x40000)))
            (directory / "loader-native.bin").write_bytes(bytes(machine.mem_read(0x4a000000, native["loader_bytes"])))
        else: assert b"BSPLv001" in tree_bytes
        if manifest.get("rtc_handoff_trace"):
            assert get(0x0700013c) == 0x42530022
            for name, address, length in (("trace-code", 0x49000000, 0x100),
                                           ("monitor-traced", 0x48000000, 0x40000),
                                           ("uboot-traced", 0x4a000000, 0x100000)):
                (directory / (name + ".bin")).write_bytes(bytes(machine.mem_read(address, length)))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--el", type=int, choices=(1, 3), default=1)
    parser.add_argument("--train", action="store_true")
    parser.add_argument("--restore-primary", action="store_true")
    parser.add_argument("--handoff", action="store_true")
    parser.add_argument("--native-loader", type=Path)
    args = parser.parse_args()
    verify(args.directory, args.el, args.train, args.restore_primary, args.handoff, args.native_loader)
