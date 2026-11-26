#!/usr/bin/env python3
"""Build and emulate an SRAM trial behind the proved one-shot vendor path."""
import argparse
import importlib.util
import json
import os
import shutil
import struct
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
FAST = HERE.parent / "fastboot"
sys.path.insert(0, str(FAST))
spec = importlib.util.spec_from_file_location("probe_adapter_build", FAST / "build.py")
b = importlib.util.module_from_spec(spec)
spec.loader.exec_module(b)


def handoff_bridge(output: Path):
    target = output / "handoff.o"
    subprocess.run([shutil.which("clang"), "--target=armv7a-none-eabi", "-c", str(HERE / "handoff32.S"),
                    "-o", str(target)], check=True)
    lld = sorted((Path.home() / ".rustup/toolchains").glob("*/lib/rustlib/*/bin/gcc-ld/ld.lld"))[-1]
    env = dict(os.environ)
    for parent in lld.parents:
        if (parent / "lib/libLLVM.dylib").is_file():
            env["DYLD_LIBRARY_PATH"] = str(parent / "lib")
            break
    (output / "handoff.ld").write_text("ENTRY(handoff_entry)\nSECTIONS { .text 0x54000 : { *(.text*) } }\n")
    subprocess.run([str(lld), "-T", str(output / "handoff.ld"), "--oformat=binary", str(target),
                    "-o", str(output / "handoff.bin")], check=True, env=env)
    blob = (output / "handoff.bin").read_bytes()
    import unicorn
    from unicorn.arm_const import UC_ARM_REG_PC, UC_ARM_REG_R0
    machine = unicorn.Uc(unicorn.UC_ARCH_ARM, unicorn.UC_MODE_ARM)
    machine.mem_map(0x54000, 4096)
    machine.mem_write(0x54000, blob)
    machine.reg_write(UC_ARM_REG_R0, 0x20060)
    seen = []
    def smc(uc, address, size, user):
        if bytes(uc.mem_read(address, 4)) == b"\x70\x00\x60\xe1":
            args = [uc.reg_read(UC_ARM_REG_R0 + i) for i in range(4)]
            b.require(args == [0x8000ff04, 0x20060, 0, 1], "AArch64 handoff arguments differ")
            seen.append(args)
            uc.emu_stop()
    machine.hook_add(unicorn.UC_HOOK_CODE, smc)
    machine.emu_start(0x54000, 0x55000, count=1000000)
    b.require(seen and machine.cpr_read(15, 0, 1, 0, 0, 0, False) & 0x1005 == 0,
              "handoff did not disable MMU/cache before SMC")
    (output / "handoff-verification.json").write_text(json.dumps({"sha256": b.kg.sha(blob), "smc": seen}, indent=2) + "\n")
    return blob


def build(probe_dir: Path, output: Path, operation: int, spl: bool, target_name="rg34xxsp", train=False, restore_primary=False):
    b.configure_target(target_name)
    output.mkdir(parents=True, exist_ok=True)
    proof = json.loads((probe_dir / "manifest.json").read_text())
    if spl:
        b.require(not restore_primary or proof.get("supports_primary_restore"), "SPL has no checked primary restore support")
        b.require(operation == 2, "SPL control uses operation 2")
        probe = (probe_dir / "spl.bin").read_bytes()
        verification = json.loads((probe_dir / "verification.json").read_text())
        b.require(b.kg.sha(probe) == proof["spl_sha256"] == verification["spl_sha256"] and
                  (verification["no_dram_mapped"] or verification.get("native_dram_model") and
                   verification.get("sram_before_dram")) and verification["reset"], "unproved SPL control")
        if proof.get("native_dram"):
            selection = json.loads((probe_dir / "selection-verification.json").read_text())
            b.require(selection["spl_sha256"] == proof["spl_sha256"] and selection["native_aarch64"],
                      "native DRAM selection proof differs")
        bridge = handoff_bridge(output)
        packed = struct.pack("<2I", len(probe), len(bridge)) + probe + bridge
    else:
        probe = packed = (probe_dir / "probe.bin").read_bytes()
        b.require(b.kg.sha(probe) == proof["probe_sha256"] and len(probe) < 0x6000, "probe differs")
        if proof.get("autodetect"):
            selection = json.loads((probe_dir / "selection-verification.json").read_text())
            b.require(selection["probe_sha256"] == proof["probe_sha256"] and
                      len(selection["retry_cases"]) >= 4 and len(selection["timing_cases"]) == 6,
                      "runtime DRAM selection proof differs")
    sources = output / "adapter-source"
    sources.mkdir(exist_ok=True)
    for name in ("entry.S", "read.c", "panel.c", "recovery.c"):
        shutil.copyfile(FAST / name, sources / name)
    (sources / "unpack.c").write_text(("#define MAINLINE_SPL_TRIAL 1\n" if spl else "") +
                                      (HERE / "probe-copy.c").read_text())
    b.HERE = sources
    b.OUT = output
    b.WATCHDOG = b.QUICK = True
    b.PANEL = target_name == "rg34xxsp"
    if not b.PANEL: b.FIXED_TRIAL_SECTOR = 0x62000
    payload = struct.pack("<4I", 0x344c4f42, len(packed), operation, b.fnv(packed)) + packed
    b.compress = lambda kernel: payload
    report, code, image, derived = b.build()
    h = b.harness
    environment = h.stored_environment(b.ROOT / "work" / target_name / "boot-prefix.img")
    target = h.Target(target_name, dict(derived, uboot=code), environment)
    original_init = h.Machine.__init__
    reached = []
    journal = bytearray(512)
    struct.pack_into("<3I", journal, 0, 0x50534f42, 1, operation)
    struct.pack_into("<I", journal, 508, b.fnv(journal[:508]))
    instrument = True
    def mapped_init(machine, *args, **kwargs):
        original_init(machine, *args, **kwargs)
        machine.uc.mem_map(0x03009000, 4096)
        machine.uc.mem_map(0x04020000, 4096)
        machine.uc.mem_map(0x20000, 0x38000 if spl else 0x8000)
        if spl: machine.uc.mem_map(0x03000000, 4096)
        machine.uc.cpr_write(15, 0, 2, 0, 0, 0, False, 0x47000000)
        machine.put32(0x47000000, 0xc12)
        if instrument:
            def read():
                b.require(machine.arg(0) == 0x56000 and machine.arg(1) == 1, "probe journal read extent differs")
                machine.uc.mem_write(machine.arg(2), bytes(journal))
                return 1
            def write():
                b.require(machine.arg(0) == 0x56000 and machine.arg(1) == 1, "probe journal write extent differs")
                journal[:] = machine.read(machine.arg(2), 512)
                b.require(struct.unpack_from("<I", journal, 12)[0] in (8, 10), "pre-SRAM checkpoint differs")
                return 1
            machine.replace(b.PHYREAD, read)
            machine.replace(b.PHYWRITE, write)
        def enter(uc, address, size, data):
            b.require(machine.get32(0x47000000) == 0xc0e, "SRAM memory type/execute permission was not fixed")
            b.require(machine.arg(0) == (0x20060 if spl else operation), "probe operation differs")
            b.require(machine.read(0x20000, len(probe)) == probe, "SRAM copy differs")
            if spl:
                b.require(machine.read(0x54000, len(bridge)) == bridge and
                          machine.get32(0x03000000) & 1 and machine.get32(0x03000004) & (1 << 24),
                          "SPL bridge/SRAM ownership differs")
            b.require(machine.get32(0x030090b8) == 0xb1, "probe entered without watchdog")
            reached.append(address)
            machine.jump = {"probe": address}
            uc.emu_stop()
        entry = 0x54000 if spl else 0x20000
        machine.uc.hook_add(h.UC_HOOK_CODE, enter, begin=entry, end=entry)
    h.Machine.__init__ = mapped_init
    try:
        result = h.boot(target, image)
        b.require(result["loaded"] and reached == [0x54000 if spl else 0x20000], "actual bootm did not reach SRAM probe")
        baseline = h.boot(target, (b.BASE / "boot.img").read_bytes())
        b.require(baseline["machine"].read(0x40080000, len(derived["kernel"])) == derived["kernel"],
                  "normal gzip kernel differs")
        instrument = False
        panel_cases = b.verify_panel(code, target.tree, environment) if b.PANEL else []
        recovery_cases = b.verify_recovery(code, target.tree, environment)
        reader_cases = []
        for read_ok, write_ok in ((True, True), (False, True), (True, False)):
            machine = h.Machine(code, target.tree, environment, 0)
            current = bytearray(b.RECOVERY["armed_yes"])
            image_reads = []
            def reader():
                sector, count, destination = [machine.arg(i) for i in range(3)]
                if sector == 0x42000:
                    b.require(count == 1, "reader recovery extent differs")
                    machine.uc.mem_write(destination, bytes(current[:512]))
                    return 1
                b.require(bytes(current) == b.RECOVERY["normal"], "image read preceded normal environment persistence")
                # RG34XX SP panel hook normally changes this variable to the fast slot.
                expected_sector = 0x62000 if not b.PANEL else 0x5a000
                expected_count = len(image) // 512 if expected_sector == 0x62000 else len((b.BASE / "boot.img").read_bytes()) // 512
                b.require((sector, count, destination) == (expected_sector, expected_count, 0x45000000),
                          "trial image read extent differs")
                image_reads.append(sector)
                return count if read_ok else 0
            def writer():
                b.require(machine.arg(0) == 0x42000 and machine.arg(1) == 1, "reader recovery write extent differs")
                if not write_ok: return 0
                current[:512] = machine.read(machine.arg(2), 512)
                return 1
            machine.replace(b.PHYREAD, reader)
            machine.replace(b.PHYWRITE, writer)
            status = machine.command(b.SYMBOLS["trial_read"], "sunxi_nand_test")
            b.require(status == (0 if read_ok and write_ok else 1) and len(image_reads) == int(write_ok),
                      "trial reader failure handling differs")
            reader_cases.append({"read_ok": read_ok, "write_ok": write_ok, "image_reads": len(image_reads)})
    finally:
        h.Machine.__init__ = original_init
    report.update(operation=operation, probe_sha256=b.kg.sha(probe), probe_bytes=len(probe))
    if proof.get("autodetect"):
        report.update(autodetect=True, candidate_types=proof["candidate_types"])
    if spl:
        report.update(spl_sha256=proof["spl_sha256"], bridge_sha256=b.kg.sha(bridge),
                      restore_source_sha256=proof["restore_source_sha256"], restores_primary=restore_primary,
                      native_dram=proof.get("native_dram", False), train_native_dram=train)
    report["verification"] = {"bootm_sram_copy_exact": True, "gzip_fallback_exact": True,
                              "panel_cases": panel_cases, "quick_recovery_cases": recovery_cases,
                              "reader_cases": reader_cases}
    (output / "manifest.json").write_text(json.dumps(report, indent=2) + "\n")
    package_proof = dict(report)
    package_proof.pop("boot_image_sha256")
    (output / "package-proof.json").write_text(json.dumps(package_proof, indent=2) + "\n")
    print(json.dumps({key: report[key] for key in ("patched_package_sha256", "boot_image_sha256", "operation",
                                                 "probe_bytes", "adapter_size")}, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("probe", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--operation", type=int, choices=(0, 1, 2), required=True)
    parser.add_argument("--spl", action="store_true")
    parser.add_argument("--target", choices=("rg34xxsp", "rg40xxv"), default="rg34xxsp")
    parser.add_argument("--train", action="store_true", help="arm native SPL DRAM training after its hardware control")
    parser.add_argument("--restore-primary", action="store_true", help="verify restoration of pinned stock boot0 before DRAM")
    args = parser.parse_args()
    build(args.probe.resolve(), args.output.resolve(), args.operation, args.spl, args.target, args.train, args.restore_primary)
