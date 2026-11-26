#!/usr/bin/env python3
"""Build and execute a matched LZ4 Android image / vendor U-Boot adapter.

Host dependencies: clang (ARM ELF target), lld, liblz4, and the existing
Unicorn/Capstone requirements. No device access. Outputs stay under work/.
"""
from __future__ import annotations

import ctypes
import ctypes.util
import gzip
import importlib.util
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import argparse
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
spec = importlib.util.spec_from_file_location("stamp_tools", HERE.parent / "uboot-stamp/build-stamped.py")
stamp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(stamp)
kg, harness, require = stamp.kg, stamp.harness, stamp.require
OUT = ROOT / "work/h700-loader/lz4-aligned"
BASE = ROOT / "work/rg34xxsp/boot-gzip"
TARGET = "rg34xxsp"
GUNZIP, PHYREAD, PHYWRITE = 0x66710, 0x3FDF8, 0x3FE3C
FIXED_TRIAL_SECTOR = None
WATCHDOG = False
PANEL = False
READER_ONLY = False
QUICK = False
RECOVERY_TEST = False
RECOVERY = {}
LATE_HDMI = False
HDMI_NODE = "/soc@03000000/hdmi@06000000"
SYMBOLS = {}


def configure_target(target):
    """Resolve only checked vendor ABI sites in an explicitly pinned package."""
    global BASE, TARGET, GUNZIP, PHYREAD, PHYWRITE
    sys.path.insert(0, str(ROOT / "experiments/h700-boot-speed"))
    from lab_devices import PROFILES
    require(target in PROFILES, "target has no pinned lab profile")
    profile = PROFILES[target]
    TARGET, BASE = target, ROOT / "work" / target / "boot-gzip"
    package = (BASE / "boot-package.bin").read_bytes()
    require(kg.sha(package) == profile["package_sha256"], "target package pin differs")
    code = package[0x800:0x100800]
    reference = (ROOT / "work/rg34xxsp/boot-gzip/boot-package.bin").read_bytes()[0x800:0x100800]
    def counterpart(offset, length):
        pattern = reference[offset:offset + length]
        hits = [at for at in range(0, len(code) - length, 2) if code[at:at + length] == pattern]
        require(len(hits) == 1, f"vendor ABI signature {offset:#x} is not unique: {hits}")
        return hits[0]
    PHYREAD, PHYWRITE = counterpart(0x3fdf8, 8), counterpart(0x3fe3c, 8)
    require(kg.word(code, (PHYREAD + 4 & ~3) + 4) == kg.word(code, (PHYWRITE + 4 & ~3) + 4),
            "physical SD wrappers use different operation tables")
    GUNZIP = counterpart(0x66710, 12)
    require(harness.branch_target(code, 0xf3c2) == GUNZIP, "gunzip call does not match signature")
    command = code.index(b"sunxi_nand_test\0")
    pointer = struct.pack("<I", stamp.LINK + command)
    hits = [at for at in range(0, len(code) - 24, 4) if code[at:at + 4] == pointer]
    require(len(hits) == 1, "unused test command table entry is not unique")
    stamp.COMMAND_ENTRY = hits[0]
    stamp.HELP_FIELD = hits[0] + 20
    stamp.HELP_TEXT = kg.word(code, stamp.HELP_FIELD) - stamp.LINK
    stamp.HELP_LEN = code.index(b"\0", stamp.HELP_TEXT) - stamp.HELP_TEXT
    require(stamp.HELP_LEN == 2119, "unused help extent differs")
    stamp.STUB_AREA = (stamp.HELP_TEXT + 4) & ~3
    stamp.REL_DYN_END = kg.word(code, 0x13d0) - stamp.LINK
    stamp.SOURCE_SHA256 = profile["package_sha256"]
    stamp.check_layout(code)
    os.environ.update(BOOT_LAB_TARGET=target, BOOT_LAB_SERIAL=profile["serial"])


def fnv(data: bytes) -> int:
    value = 2166136261
    for byte in data:
        value = ((value ^ byte) * 16777619) & 0xFFFFFFFF
    return value


def frame(block: bytes, output_size: int) -> bytes:
    return struct.pack("<4I", 0x344C4F42, len(block), output_size, fnv(block)) + block


def write_artifact(path: Path, blob: bytes) -> None:
    require(not path.exists() or path.read_bytes() == blob,
            f"{path} already contains another artifact; choose a fresh --output")
    path.write_bytes(blob)


def compress(kernel: bytes) -> bytes:
    library = ctypes.util.find_library("lz4") or "/opt/homebrew/opt/lz4/lib/liblz4.dylib"
    lib = ctypes.CDLL(library)
    lib.LZ4_compressBound.argtypes = [ctypes.c_int]
    lib.LZ4_compress_HC.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int]
    capacity = lib.LZ4_compressBound(len(kernel))
    output = ctypes.create_string_buffer(capacity)
    size = lib.LZ4_compress_HC(kernel, output, len(kernel), capacity, 12)
    require(size > 0, "LZ4 compression failed")
    return frame(output.raw[:size], len(kernel))


def compile_adapter(gzip_sectors: int, fast_sectors: int) -> tuple[bytes, int]:
    clang = os.environ.get("CLANG", shutil.which("clang"))
    lld = os.environ.get("LLD", shutil.which("ld.lld"))
    if not lld:
        candidates = sorted((Path.home() / ".rustup/toolchains").glob("*/lib/rustlib/*/bin/gcc-ld/ld.lld"))
        lld = str(candidates[-1]) if candidates else None
    require(bool(clang and lld), "clang and lld are required; set CLANG/LLD if necessary")
    common = [str(clang), "--target=armv7a-none-eabi", "-march=armv7-a", "-mthumb", "-mfloat-abi=soft",
              "-mno-unaligned-access", "-Os", "-fpie", "-ffreestanding", "-fno-builtin", "-fno-stack-protector",
              "-fno-unwind-tables", "-fno-asynchronous-unwind-tables", "-Wall", "-Wextra", "-Werror",
              f"-DTRIAL_GZIP_SECTORS={gzip_sectors}", f"-DTRIAL_FAST_SECTORS={fast_sectors}", "-c"]
    sources = ["entry.S", "unpack.c", "read.c"] + (["panel.c"] if PANEL else [])
    if QUICK:
        from oneshot import parse_env
        import recovery_env
        saved = OUT.parent / "baseline-env.bin"
        require(kg.sha(saved.read_bytes()) == saved.with_suffix(".sha256").read_text().strip(), "environment backup differs")
        RECOVERY.update(recovery_env.images(parse_env(saved.read_bytes()), RECOVERY_TEST))
        (OUT / "recovery-data.h").write_text(recovery_env.header(RECOVERY))
        for name, blob in RECOVERY.items(): write_artifact(OUT / ("env-" + name + ".bin"), blob)
        sources.append("recovery.c")
    for source in sources:
        flags = ["-DTRIAL_WATCHDOG=1"] if WATCHDOG else []
        if FIXED_TRIAL_SECTOR is not None: flags.append(f"-DFIXED_TRIAL_SECTOR={FIXED_TRIAL_SECTOR}")
        if PANEL: flags.append("-DPANEL_TRIAL=1")
        if LATE_HDMI: flags.append("-DLATE_HDMI=1")
        if QUICK: flags += ["-DQUICK_RECOVERY=1", "-I", str(OUT)]
        subprocess.run(common + flags + [str(HERE / source), "-o", str(OUT / (source + ".o"))], check=True)
    link = OUT / "link.ld"
    link.write_text(f"SECTIONS {{ .text {stamp.LINK + stamp.STUB_AREA:#x} : {{ *(.text.entry) *(.text*) *(.rodata*) *(.data*) }} "
                    "/DISCARD/ : { *(.ARM.exidx*) *(.ARM.extab*) *(.comment) *(.ARM.attributes) } }\n")
    linker_env = os.environ.copy()
    for parent in Path(lld).parents:
        if (parent / "lib/libLLVM.dylib").is_file():
            linker_env["DYLD_LIBRARY_PATH"] = str(parent / "lib")
            break
    subprocess.run([str(lld), "-T", str(link), "--entry=unpack_kernel", "--oformat=binary",
                    "-Map=" + str(OUT / "adapter.map"), *[str(OUT / (source + ".o")) for source in sources],
                    "-o", str(OUT / "adapter.bin")], check=True, env=linker_env)
    symbols = (OUT / "adapter.map").read_text()
    hit = re.search(r"^\s*([0-9a-f]+)\s+[0-9a-f]+\s+[0-9a-f]+\s+\d+\s+fallback_jump\s*$", symbols, re.M)
    require(hit is not None, "fallback branch missing from link map")
    site = (int(hit.group(1), 16) & ~1) - stamp.LINK
    code = bytearray((OUT / "adapter.bin").read_bytes())
    local = site - stamp.STUB_AREA
    require(harness.branch_target(code, local) == local, "fallback placeholder is not b.w self")
    code[local:local + 4] = stamp.encode_branch(site, GUNZIP, link=False)
    for match in re.finditer(r"^\s*([0-9a-f]+)\s+[0-9a-f]+\s+[0-9a-f]+\s+\d+\s+(\w+)\s*$", symbols, re.M):
        SYMBOLS[match.group(2)] = (int(match.group(1), 16) & ~1) - stamp.LINK
    at = SYMBOLS["vendor_phyread"]
    local = at - stamp.STUB_AREA
    require(harness.branch_target(code, local) == local, "phyread veneer differs")
    code[local:local + 4] = stamp.encode_branch(at, PHYREAD, link=False)
    if QUICK:
        at = SYMBOLS["vendor_phywrite"]
        local = at - stamp.STUB_AREA
        require(harness.branch_target(code, local) == local, "phywrite veneer differs")
        code[local:local + 4] = stamp.encode_branch(at, PHYWRITE, link=False)
    if PANEL:
        for name, target in (("vendor_env_get", 0xE8F8), ("vendor_env_set", 0xE620),
                             ("vendor_env_save", 0x5DCB8), ("vendor_logo", 0x2A2C),
                             ("vendor_reserve", 0x393B4)):
            at = SYMBOLS[name]
            local = at - stamp.STUB_AREA
            require(harness.branch_target(code, local) == local, f"{name}: veneer differs")
            code[local:local + 4] = stamp.encode_branch(at, target, link=False)
        if LATE_HDMI:
            for name, target in (("vendor_fdt_path", 0x67FDC), ("vendor_fdt_getprop", 0x67E7C)):
                at = SYMBOLS[name]
                local = at - stamp.STUB_AREA
                require(harness.branch_target(code, local) == local, "FDT veneer differs")
                code[local:local + 4] = stamp.encode_branch(at, target, link=False)
    require(len(code) <= stamp.HELP_LEN - (stamp.STUB_AREA - stamp.HELP_TEXT), "adapter exceeds reclaimed help text")
    return bytes(code), site


def build() -> tuple[dict, bytes, bytes, dict]:
    require(not PANEL or WATCHDOG, "panel trials require watchdog recovery")
    OUT.mkdir(parents=True, exist_ok=True)
    base = (BASE / "boot-package.bin").read_bytes()
    require(kg.sha(base) == stamp.SOURCE_SHA256, "source package is not the pinned RG34XX SP build")
    old_image = (BASE / "boot.img").read_bytes()
    manifest = json.loads((BASE / "manifest.json").read_text())
    require(kg.sha(old_image) == manifest["boot_image_sha256"], "source Android image differs")
    parts, end = kg.split(old_image)
    require(end == len(old_image), "unexpected bytes after Android image")
    kernel = gzip.decompress(parts[0])
    require(kg.sha(kernel) == manifest["kernel_sha256"], "source kernel differs")
    payload = compress(kernel)
    fast_parts = list(parts)
    fast_parts[0] = payload
    packed_image = kg.join(old_image[:2048], fast_parts, old_image[0x40:0x240].split(b"\0")[0])
    adapter, fallback = compile_adapter(len(old_image) // 512, len(packed_image) // 512)
    original = base[stamp.ITEM:stamp.ITEM + stamp.ITEM_SIZE]
    stamp.check_layout(original)
    require(harness.branch_target(original, 0xF3C2) == GUNZIP, "gunzip call differs")
    uboot = bytearray(original)
    uboot[stamp.HELP_TEXT] = 0
    uboot[stamp.STUB_AREA:stamp.STUB_AREA + len(adapter)] = adapter
    if not READER_ONLY:
        uboot[0xF3C2:0xF3C6] = stamp.encode_branch(0xF3C2, stamp.STUB_AREA, link=True)
    # Reuse the unused test command's existing relocated function-pointer slot.
    command_field = stamp.COMMAND_ENTRY + 12
    struct.pack_into("<I", uboot, command_field, stamp.LINK + SYMBOLS["trial_read"] + 1)
    if PANEL:
        require(harness.branch_target(original, 0x2B1E) == 0x2A2C, "logo call differs")
        uboot[0x2B1E:0x2B22] = stamp.encode_branch(0x2B1E, SYMBOLS["trial_logo"], link=True)
        require(harness.branch_target(original, 0x58CE) == 0x393B4, "display reservation call differs")
        uboot[0x58CE:0x58D2] = stamp.encode_branch(0x58CE, SYMBOLS["trial_reserve"], link=True)
    struct.pack_into("<I", uboot, 12, kg.checksum(uboot, 12))
    package = bytearray(base)
    package[stamp.ITEM:stamp.ITEM + stamp.ITEM_SIZE] = uboot
    struct.pack_into("<I", package, 20, kg.checksum(package, 20))
    allowed = set(range(20, 24)) | {stamp.ITEM + i for i in range(12, 16)}
    allowed |= {stamp.ITEM + i for i in range(stamp.HELP_TEXT, stamp.STUB_AREA + len(adapter))}
    allowed |= {stamp.ITEM + i for i in range(0xF3C2, 0xF3C6)}
    allowed |= {stamp.ITEM + i for i in range(command_field, command_field + 4)}
    if PANEL: allowed |= {stamp.ITEM + i for i in range(0x2B1E, 0x2B22)}
    if PANEL: allowed |= {stamp.ITEM + i for i in range(0x58CE, 0x58D2)}
    require(kg.changed(base, bytes(package)) <= allowed, "unexpected package change")
    if not READER_ONLY: parts[0] = payload
    cmdline = old_image[0x40:0x240].split(b"\0")[0]
    image = kg.join(old_image[:2048], parts, cmdline)
    require(kg.split(image)[0][1:] == kg.split(old_image)[0][1:], "non-kernel Android component changed")
    prefix = ROOT / "work" / TARGET / "boot-prefix.img"
    derived = kg.derive(TARGET, prefix)
    report = {"target": TARGET, "original_package_sha256": kg.sha(base), "patched_package_sha256": kg.sha(package),
              "original_boot_image_sha256": kg.sha(old_image), "boot_image_sha256": kg.sha(image),
              "original_boot_image_size": len(old_image), "boot_image_size": len(image),
              "kernel_sha256": kg.sha(kernel), "kernel_size": len(kernel), "payload_size": len(payload),
              "adapter_size": len(adapter), "adapter_offset": hex(stamp.STUB_AREA), "fallback_offset": hex(fallback)}
    report["watchdog_armed_seconds"] = 16 if WATCHDOG else 0
    report["panel_trial"] = PANEL
    report["vendor_trial_reader"] = True
    report["reader_only"] = READER_ONLY
    report["quick_recovery"] = QUICK
    report["recovery_test"] = RECOVERY_TEST
    report["fdt_edits"] = []
    report["runtime_late_hdmi"] = LATE_HDMI
    report["dual_slots"] = PANEL
    report["gzip_read_sectors"] = len(old_image) // 512
    report["fast_read_sectors"] = len(packed_image) // 512
    if QUICK: report["recovery_environments"] = {name: kg.sha(blob) for name, blob in RECOVERY.items()}
    report["symbols"] = SYMBOLS
    write_artifact(OUT / "boot-package.bin", bytes(package))
    write_artifact(OUT / "boot.img", image)
    return report, bytes(uboot), image, derived


def verify_panel(uboot: bytes, tree: bytes, environment: dict) -> list:
    cases = []
    for delta in (0, 0x03000000):
        for flag, save_ok, strap in ((None, True, 0), ("no", True, 0), ("yes-more", True, 0),
                                     ("yes", False, 0), ("yes", True, 0), ("yes", True, 0x40)):
            values = dict(environment)
            if flag is not None: values["baseos_skip_logo"] = flag
            m = harness.Machine(uboot, tree, values, 0)
            if delta: m.uc.mem_write(stamp.LINK + delta, uboot)
            m.uc.mem_map(0x0300B000, 4096)
            m.put32(0x0300B090, 0xFFFFFFFF)
            m.put32(0x0300B0AC, 0xFFFFFFFF)
            m.put32(0x0300B0A0, strap)
            events = []
            def save():
                require(m.env["baseos_skip_logo"] == "no", "normal logo not restored before save")
                require(m.get32(0x030090B8) == 0, "watchdog armed before save completed")
                events.append("save-normal")
                return 0 if save_ok else 1
            current = bytearray(RECOVERY.get("armed_yes", b""))
            def read_sector():
                require(m.arg(0) == 0x42000 and m.arg(1) == 1, "panel recovery read extent differs")
                m.uc.mem_write(m.arg(2), bytes(current[:512]))
                return 1
            def write_sector():
                require(m.arg(0) == 0x42000 and m.arg(1) == 1, "panel recovery write extent differs")
                events.append("save-normal")
                if not save_ok: return 0
                current[:512] = m.read(m.arg(2), 512)
                require(bytes(current) == RECOVERY["normal"], "panel recovery environment differs")
                return 1
            for at, callback in ((0xE8F8, m.env_get), (0xE620, m.env_set), (0x5DCB8, save),
                                 (0x2A2C, lambda: events.append("original-logo") or 37)):
                if delta or at in (0x5DCB8, 0x2A2C): m.replace(stamp.LINK + delta + at, callback)
            if QUICK:
                m.replace(stamp.LINK + delta + PHYREAD, read_sector)
                m.replace(stamp.LINK + delta + PHYWRITE, write_sector)
            result = m.call(delta + SYMBOLS["trial_logo"], [])
            skipped = flag == "yes" and save_ok
            expected = (["save-normal"] if flag == "yes" else []) + ([] if skipped else ["original-logo"])
            require(events == expected and result == (0 if skipped else 37), "panel fallback differs")
            require(m.get32(stamp.LINK + delta + SYMBOLS["trial_sector"]) == (0x62000 if skipped else 0x5A000),
                    "trial slot selection differs after relocation")
            require(m.get32(0x030090B8) == (0xB1 if skipped else 0), "panel watchdog differs")
            if skipped:
                require(m.env["lcd_type"] == ("old" if strap else "boe"), "panel strap differs")
                require(m.get32(0x0300B090) == 0xF0FFFFFF and m.get32(0x0300B0AC) == 0xFFFFDFFF,
                        "PE6 mux/pull changed other pins")
            m.env["disp_reserve"] = "-352321536,0xea000018"
            m.replace(stamp.LINK + delta + 0x393B4, lambda: events.append("original-reserve") or 19)
            reserved = m.call(delta + SYMBOLS["trial_reserve"], [])
            require(reserved == (0 if skipped else 19), "reservation fallback differs")
            require(("disp_reserve" not in m.env) == skipped, "stale framebuffer was not removed")
            if LATE_HDMI:
                tree_after = m.read(harness.FDT, harness.fdt.total_size(m.read(harness.FDT, 8)))
                status = kg.fdt_edit.find(tree_after, HDMI_NODE.lstrip("/"), "status")[1]
                require(status == (b"late\0" if skipped else b"okay\0"), "conditional HDMI status differs")
            cases.append({"relocation": hex(delta), "flag": flag, "save_ok": save_ok,
                          "strap": strap, "events": events})
    return cases


def verify_recovery(uboot: bytes, tree: bytes, environment: dict) -> list:
    cases = []
    for name in ("armed_no", "armed_yes", "normal", "unknown", "write-failed", "readback-differs"):
        m = harness.Machine(uboot, tree, environment, 0)
        current = bytearray(RECOVERY[name if name in RECOVERY else "armed_no"])
        if name == "unknown": current[200] ^= 1
        writes = []
        def read():
            require(m.arg(0) == 0x42000 and m.arg(1) == 1 and m.arg(2) % 64 == 0,
                    "unaligned/out-of-range recovery read")
            data = bytearray(current[:512])
            if name == "readback-differs" and writes: data[300] ^= 1
            m.uc.mem_write(m.arg(2), bytes(data))
            return 1
        def write():
            require(m.arg(0) == 0x42000 and m.arg(1) == 1 and m.arg(2) % 64 == 0,
                    "unaligned/out-of-range recovery write")
            writes.append(m.read(m.arg(2), 512))
            if name == "write-failed": return 0
            current[:512] = writes[-1]
            require(bytes(current) == RECOVERY["normal"], "recovery changed other environment bytes")
            return 1
        m.replace(PHYREAD, read)
        m.replace(PHYWRITE, write)
        result = m.call(SYMBOLS["trial_persist"], [])
        expected = name in ("armed_no", "armed_yes", "normal")
        require(result == (0 if expected else 1), "recovery failure control returned success")
        require(len(writes) == (0 if name in ("normal", "unknown") else 1), "unexpected recovery write")
        if expected:
            before = len(writes)
            require(m.call(SYMBOLS["trial_persist"], []) == 0 and len(writes) == before,
                    "repeated recovery wrote again")
        cases.append({"name": name, "success": expected, "written_bytes": 512 * len(writes)})
    return cases


def verify(uboot: bytes, image: bytes, derived: dict, adapter_size: int,
           regression_package: Path | None = None) -> dict:
    # Unicorn does not enforce the physical CPU's SCTLR.A behavior. Reject
    # every unaligned multi-byte access made by injected code explicitly.
    from unicorn import Uc, UC_ARCH_ARM, UC_MODE_THUMB, UC_HOOK_MEM_READ, UC_HOOK_MEM_WRITE
    from unicorn.arm_const import UC_ARM_REG_PC, UC_ARM_REG_SP, UC_ARM_REG_LR, UC_ARM_REG_R0
    payload = compress(derived["kernel"]) if READER_ONLY else kg.split(image)[0][0]

    def guarded_unpack(code, entry=None):
        entry = stamp.STUB_AREA if entry is None else entry
        machine = Uc(UC_ARCH_ARM, UC_MODE_THUMB)
        machine.mem_map(0x40000000, 0x10000000)
        machine.mem_map(0x03009000, 4096)
        machine.mem_write(stamp.LINK, code)
        machine.mem_write(0x45000000, payload)
        machine.mem_write(0x47000000, struct.pack("<I", len(payload)))
        machine.reg_write(UC_ARM_REG_SP, 0x4FFF0000)
        machine.reg_write(UC_ARM_REG_LR, 0x4FFE0001)
        for n, value in enumerate((0x40080000, 32 * 1024 * 1024, 0x45000000, 0x47000000)):
            machine.reg_write(UC_ARM_REG_R0 + n, value)
        def guard(uc, access, address, size, value, _):
            if size == 1 or address % size == 0:
                return
            pc = uc.reg_read(UC_ARM_REG_PC)
            if stamp.LINK + stamp.STUB_AREA <= pc < stamp.LINK + stamp.STUB_AREA + adapter_size:
                detail = dict(pc=hex(pc), address=hex(address), size=size)
                raise harness.Failed(f"injected unaligned access: {detail}")
        machine.hook_add(UC_HOOK_MEM_READ | UC_HOOK_MEM_WRITE, guard)
        machine.emu_start((stamp.LINK + entry) | 1, 0x4FFE0000, timeout=120_000_000)
        require(machine.reg_read(UC_ARM_REG_PC) == 0x4FFE0000
                and machine.reg_read(UC_ARM_REG_R0) == 0, "aligned decode did not return success")
        require(machine.mem_read(0x40080000, len(derived["kernel"])) == derived["kernel"], "aligned decode kernel differs")
        require(struct.unpack("<I", machine.mem_read(0x47000000, 4))[0] == len(derived["kernel"]), "aligned decode length differs")
    regression = None
    if regression_package:
        broken = regression_package.read_bytes()[stamp.ITEM:stamp.ITEM + stamp.ITEM_SIZE]
        try:
            guarded_unpack(broken, 0x92FD8)
        except harness.Failed as error:
            require(str(error).startswith("injected unaligned access:"), f"unexpected regression failure: {error}")
            regression = str(error)
        else:
            require(False, "past hardware-failed adapter did not trip the alignment guard")
    guarded_unpack(uboot)
    prefix = ROOT / "work/rg34xxsp/boot-prefix.img"
    environment = harness.stored_environment(prefix)
    candidate_derived = dict(derived, uboot=uboot)
    target = harness.Target("rg34xxsp", candidate_derived, environment)
    if WATCHDOG:
        # The register writes execute as ARM instructions. Timing/reset is a
        # separately proved hardware behavior, not emulated by this memory map.
        original_init = h_init = harness.Machine.__init__
        def mapped_init(machine, *args, **kwargs):
            h_init(machine, *args, **kwargs)
            machine.uc.mem_map(0x03009000, 4096)
        harness.Machine.__init__ = mapped_init
    old = harness.boot(harness.Target("rg34xxsp", derived, environment), derived["boot"])
    new = harness.boot(target, image)
    fallback = harness.boot(target, derived["boot"])
    expected_tree = bytearray(old["tree"])
    for label, result in (("lz4", new), ("gzip fallback", fallback)):
        require(result["loaded"] and result["jump"] == old["jump"], f"{label}: boot/jump differs")
        require(result["machine"].read(0x40080000, len(derived["kernel"])) == derived["kernel"], f"{label}: kernel differs")
        require(result["tree"] == bytes(expected_tree) and result["images"] == old["images"], f"{label}: FDT/ramdisk differs")
    require(sum(size for _, size, _ in new["reads"]) == len(image), "card read extent differs")
    failures = []
    for label, payload, capacity in [
        ("truncated header", b"BOL4" + bytes(8), 64),
        ("hash mismatch", frame(b"\x10A", 1)[:-1] + b"B", 64),
        ("zero offset", frame(b"\x10A\0\0", 5), 64),
        ("offset before output", frame(b"\x10A\x02\0", 5), 64),
        ("literal overflow", frame(b"\x50abcde", 4), 64),
        ("output capacity", frame(b"\x10A", 1), 0),
        ("short output", frame(b"\x10A", 2), 64),
    ]:
        machine = harness.Machine(uboot, target.tree, environment, 0)
        source, length, destination = 0x46000000, 0x47000000, 0x40080000
        machine.uc.mem_write(source, payload)
        machine.put32(length, len(payload))
        machine.uc.mem_write(destination - 16, b"\xA5" * 96)
        result = machine.call(stamp.STUB_AREA, [destination, capacity, source, length])
        require(result not in (None, 0), f"{label}: invalid input succeeded")
        require(machine.read(destination - 16, 16) == b"\xA5" * 16
                and machine.read(destination + capacity, 16) == b"\xA5" * 16,
                f"{label}: destination bounds violated")
        failures.append(label)
    if WATCHDOG:
        require(new["machine"].get32(0x030090B4) == 1 and new["machine"].get32(0x030090B8) == 0xB1
                and new["machine"].get32(0x030090B0) == 0x14AF, "watchdog not armed before trial decode")
        require(fallback["machine"].get32(0x030090B8) == 0, "gzip fallback unexpectedly armed watchdog")
    panel_cases = verify_panel(uboot, target.tree, environment) if PANEL else []
    if WATCHDOG: harness.Machine.__init__ = original_init
    gzip_sectors = len(derived["boot"]) // 512
    for result in (gzip_sectors, 0):
        m = harness.Machine(uboot, target.tree, environment, 0)
        current = bytearray(RECOVERY.get("armed_no", b""))
        def read():
            if QUICK and m.arg(0) == 0x42000:
                require(m.arg(1) == 1, "environment read size differs")
                m.uc.mem_write(m.arg(2), bytes(current[:512]))
                return 1
            require([m.arg(i) for i in range(3)] == [0x5A000, gzip_sectors, 0x45000000], "trial reader extent differs")
            return result
        def write():
            require(m.arg(0) == 0x42000 and m.arg(1) == 1, "environment write extent differs")
            current[:512] = m.read(m.arg(2), 512)
            require(bytes(current) == RECOVERY["normal"], "saved environment differs beyond its first sector")
            return 1
        m.replace(0x3FDF8, read)
        if QUICK: m.replace(0x3FE3C, write)
        require(m.command(SYMBOLS["trial_read"], "sunxi_nand_test") == (0 if result else 1), "reader result differs")
    recovery_cases = verify_recovery(uboot, target.tree, environment) if QUICK else []
    return {"kernel_exact": True, "expected_fdt_and_ramdisk_exact": True, "gzip_fallback_exact": True,
            "injected_accesses_aligned": True, "past_failure_caught": regression,
            "read_bytes": sum(size for _, size, _ in new["reads"]), "rejected": failures,
            "panel_cases": panel_cases, "quick_recovery_cases": recovery_cases}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUT)
    parser.add_argument("--regression-package", type=Path)
    parser.add_argument("--watchdog", action="store_true")
    parser.add_argument("--panel-trial", action="store_true")
    parser.add_argument("--reader-only", action="store_true")
    parser.add_argument("--quick-recovery", action="store_true")
    parser.add_argument("--recovery-test", action="store_true")
    parser.add_argument("--late-hdmi", action="store_true")
    args = parser.parse_args()
    OUT = args.output.resolve()
    WATCHDOG = args.watchdog
    PANEL = args.panel_trial
    READER_ONLY = args.reader_only
    QUICK = args.quick_recovery
    RECOVERY_TEST = args.recovery_test
    LATE_HDMI = args.late_hdmi
    require(not RECOVERY_TEST or QUICK and READER_ONLY, "recovery control requires a gzip-only quick reader")
    report, uboot, image, derived = build()
    print(json.dumps(report, indent=2), flush=True)
    report["verification"] = verify(uboot, image, derived, report["adapter_size"], args.regression_package)
    (OUT / "manifest.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report["verification"], indent=2), flush=True)
