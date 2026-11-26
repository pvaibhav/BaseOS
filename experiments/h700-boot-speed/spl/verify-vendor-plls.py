#!/usr/bin/env python3
"""Compare the compiled native PLL power loop with boot0's actual instructions."""
import argparse
import hashlib
import json
import random
import struct
from pathlib import Path

import capstone as cs
import unicorn as u
from unicorn import arm_const as a, arm64_const as z

ROOT = Path(__file__).resolve().parents[3]
SRAM, CCU = 0x20000, 0x03001000
VENDOR_START, VENDOR_END = 0x2242e, 0x224be
OFFSETS = (0x28, 0x30, 0x40, 0x48, 0x50, 0x58, 0x60, 0xe0, 0x78)
SEED = 0x700c510


def sha(data):
    return hashlib.sha256(data).hexdigest()


def native_region(blob, symbol_text):
    symbols = {fields[2]: int(fields[0], 16) for line in symbol_text.splitlines()
               if len(fields := line.split()) == 3}
    start = symbols["baseos_vendor_handoff"]
    md = cs.Cs(cs.CS_ARCH_ARM64, cs.CS_MODE_ARM)
    md.detail = True
    instructions = list(md.disasm(blob[start - SRAM:start - SRAM + 0x600], start))
    calls = [i for i, ins in enumerate(instructions)
             if ins.mnemonic == "bl" and ins.operands[0].imm == symbols["sunxi_board_init"]]
    assert len(calls) == 1, "cannot locate the completed DRAM initialization"
    after = instructions[calls[0] + 1:]
    bootreason = symbols.get("baseos_native_bootreason")
    if bootreason is not None:
        first_call = next(i for i, ins in enumerate(after) if ins.mnemonic == "bl")
        assert after[first_call].operands[0].imm == bootreason, "unexpected call before ABI2 PLL power loop"
        failure = after[first_call + 1]
        assert failure.mnemonic == "cbnz" and failure.op_str.startswith("w0, "), "PMIC read failure is not checked"
        after = after[first_call + 2:]
    helper = symbols.get("baseos_vendor_pll_power")
    if helper is not None:
        first_call = next(ins for ins in after if ins.mnemonic == "bl")
        assert first_call.operands[0].imm == helper, "PLL helper is not called after DRAM initialization"
        body = list(md.disasm(blob[helper - SRAM:helper - SRAM + 0x200], helper))
        end = next(ins.address + 4 for ins in body if ins.mnemonic == "ret")
        return helper, end, "outlined"
    for ins in after[:64]:
        assert ins.mnemonic != "bl", "compiled native PLL compatibility loop is absent"
        if ins.mnemonic.startswith("b.") and after[0].address <= ins.operands[0].imm < ins.address:
            return after[0].address, ins.address + 4, "inlined"
    raise AssertionError("cannot locate the compiled native PLL compatibility loop")


def execute(blob, native, start, end, seed):
    machine = u.Uc(u.UC_ARCH_ARM64 if native else u.UC_ARCH_ARM,
                   u.UC_MODE_ARM if native else u.UC_MODE_THUMB)
    machine.mem_map(SRAM, 0x38000)
    machine.mem_map(CCU, 4096)
    machine.mem_write(SRAM, blob)
    machine.mem_write(CCU, seed)
    pc = z.UC_ARM64_REG_PC if native else a.UC_ARM_REG_PC
    machine.reg_write(z.UC_ARM64_REG_SP if native else a.UC_ARM_REG_SP, 0x57000)
    machine.reg_write(z.UC_ARM64_REG_LR if native else a.UC_ARM_REG_LR,
                      0x57ff0 if native else 0x57ff1)
    if not native:
        machine.reg_write(a.UC_ARM_REG_CPSR, 0x13)
    writes, delays = [], []

    def write(uc, access, address, size, value, data):
        if CCU <= address < CCU + 4096:
            writes.append((address, size, value))
        else:
            assert SRAM <= address < 0x58000, f"unexpected write at {address:#x}"

    def delay(uc, address, size, data):
        delays.append(uc.reg_read(a.UC_ARM_REG_R0))
        uc.reg_write(a.UC_ARM_REG_PC, uc.reg_read(a.UC_ARM_REG_LR))

    machine.hook_add(u.UC_HOOK_MEM_WRITE, write)
    if not native:
        machine.hook_add(u.UC_HOOK_CODE, delay, begin=0x22064, end=0x22064)
    stop = 0x57ff0 if native and bytes(machine.mem_read(end - 4, 4)) == b"\xc0\x03\x5f\xd6" else end
    machine.emu_start(start if native else start | 1, stop, count=2000)
    assert machine.reg_read(pc) == stop, "PLL block did not reach its exit"
    assert [entry[:2] for entry in writes] == [(CCU + off, 4) for off in OFFSETS]
    assert delays == ([] if native else [10] * 9)
    return writes, bytes(machine.mem_read(CCU, 4096))


def prepared_banks(boot0):
    targets = json.loads((ROOT / "manifest/kernel-gzip.json").read_text())["profiles"]
    assert len(targets) == 11, "prepared vendor bank catalog coverage changed"
    rows = []
    for target in targets:
        path = ROOT / "work" / target / "boot-prefix.img"
        assert path.exists(), f"missing prepared vendor bank: {target}"
        with path.open("rb") as handle:
            handle.seek(8192)
            bank = handle.read(65536)
        assert bank[4:12] == b"eGON.BT0", target
        assert bank[0x242e:0x24be] == boot0[0x242e:0x24be], target
        assert bank[0x2524:0x2548] == boot0[0x2524:0x2548], target
        rows.append({"target": target, "boot0_sha256": sha(bank), "same_block_and_literals": True})
    assert len(rows) == len(targets) == 11
    return rows


def verify(directory, boot0_path):
    spl, boot0 = (directory / "spl.bin").read_bytes(), boot0_path.read_bytes()
    report = {"passed": False, "spl_sha256": sha(spl), "boot0_sha256": sha(boot0)}
    output = directory / "vendor-pll-verification.json"
    try:
        manifest = json.loads((directory / "manifest.json").read_text())
        assert sha(spl) == manifest["spl_sha256"], "SPL differs from manifest"
        assert sha(boot0) == manifest["restore_source_sha256"], "boot0 differs from restore source"
        assert sha(boot0[0x242e:0x24be]) == "718286e751c10e66cb1c957250d1d31ef8616ab661bbe0420f84ef1829c6d13a", "unexpected vendor PLL power block"
        assert sha(boot0[0x2524:0x2548]) == "5cca12b4c26f3383b694e19f7b0a6a594f727b118ec954f47bde684768fb0dea", "unexpected vendor PLL register literals"
        start, end, placement = native_region(spl, (directory / "symbols.txt").read_text())
        rng = random.Random(SEED)
        patterns = (0, 0xffffffff, 0x80000000, 0x7fffffff, 0xaaaaaaaa, 0x55555555)
        seeds = [struct.pack("<I", value) * 1024 for value in patterns]
        seeds += [rng.randbytes(4096) for _ in range(256)]
        for seed in seeds:
            expected = bytearray(seed)
            for off in OFFSETS:
                struct.pack_into("<I", expected, off, struct.unpack_from("<I", seed, off)[0] | 0x80000000)
            vendor_writes, vendor_page = execute(boot0, False, VENDOR_START, VENDOR_END, seed)
            native_writes, native_page = execute(spl, True, start, end, seed)
            assert vendor_writes == native_writes, "native writes differ from vendor instructions"
            assert vendor_page == native_page == bytes(expected), "other CCU bits or registers changed"
        report.update(passed=True, native_entry=hex(start), native_exit=hex(end), native_placement=placement,
                      native_code_sha256=sha(spl[start - SRAM:end - SRAM]),
                      vendor_entry=hex(VENDOR_START), vendor_exit=hex(VENDOR_END),
                      vendor_block_sha256=sha(boot0[0x242e:0x24be]),
                      addresses=[hex(CCU + off) for off in OFFSETS], writes_per_case=9,
                      random_seed=hex(SEED), random_cases=256, fixed_cases=len(patterns), cases=len(seeds),
                      other_bits_and_entire_ccu_page_preserved=True,
                      timing_scope="Vendor 10us waits modeled; native settling relies on subsequent image loading.",
                      prepared_vendor_banks=prepared_banks(boot0))
    except Exception as error:
        report["error"] = f"{type(error).__name__}: {error}"
        raise
    finally:
        output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("spl", type=Path)
    parser.add_argument("boot0", nargs="?", type=Path)
    args = parser.parse_args()
    verify(args.spl, args.boot0 or args.spl / "restore-source.bin")
