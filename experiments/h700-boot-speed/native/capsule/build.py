#!/usr/bin/env python3
"""Build a source-bound 128 KiB raw capsule without accessing a device."""
import argparse
import hashlib
import json
import os
import re
import shutil
import struct
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
SOURCES = ("contract.json", "build.py", "entry.S", "capsule.c", "payloads.S", "include/string.h")


def sha(data):
    return hashlib.sha256(data).hexdigest()


def fnv(data):
    value = 2166136261
    for byte in data:
        value = (value ^ byte) * 16777619 & 0xffffffff
    return value


def build(inner, stock_path, native_manifest_path, output, diagnostic=False, purpose=None):
    assert not output.exists(), "choose a fresh immutable output directory"
    assert not diagnostic or purpose, "diagnostic capsules require a distinct explicit purpose"
    if native_manifest_path.is_dir():
        native_manifest_path /= "manifest.json"
    native = json.loads(native_manifest_path.read_text())
    inner_manifest = json.loads((inner / "manifest.json").read_text())
    stock, image = stock_path.read_bytes(), (inner / "spl.bin").read_bytes()
    assert len(stock) == 65536 and stock[4:12] == b"eGON.BT0"
    assert len(image) == 49152 and image[4:12] == b"eGON.BT0"
    assert sha(image) == inner_manifest["spl_sha256"]
    assert sha(stock) == inner_manifest["restore_source_sha256"]
    assert native["source_boot0_sha256"] == sha(stock)
    assert native["raw_capsule"] == json.loads((HERE / "contract.json").read_text())
    assert native["persistent_boot"]["version"] == 2
    assert not native["native_diagnostic"]
    assert native["kernel_sha256"] == native["source_kernel_sha256"]
    assert native["monitor_sha256"] == native["source_monitor_sha256"]
    for relative, digest in native["persistent_source_hashes"].items():
        source = (ROOT / relative).resolve()
        assert source.is_relative_to(ROOT)
        assert sha(source.read_bytes()) == digest, f"generation source changed: {relative}"
    capsule_pins = {str((HERE / name).relative_to(ROOT)): sha((HERE / name).read_bytes()) for name in SOURCES}
    capsule_pins["src/native-attempt.h"] = sha((ROOT / "src/native-attempt.h").read_bytes())
    assert all(native["persistent_source_hashes"].get(name) == digest for name, digest in capsule_pins.items())
    if not diagnostic:
        assert inner_manifest["native_loader"] == native
        assert inner_manifest["inner_mode"] == "adopt-only" and inner_manifest["protocol_version"] == 2
        assert not inner_manifest["supports_primary_restore"]
        assert not inner_manifest["resume_compiled"] and not inner_manifest["journal_compiled"]
    generation = bytes.fromhex(native["generation"])
    assert len(generation) == 32
    cookie = fnv(generation)
    assert cookie
    output.mkdir(parents=True)
    (output / "stock.bin").write_bytes(stock)
    (output / "native.bin").write_bytes(image)
    header = bytearray(stock[:0x400])
    struct.pack_into("<I", header, 0, 0xea0000fe)  # ARM branch to 0x20400.
    (output / "capsule-header.bin").write_bytes(header)
    (output / "config-values.h").write_text(
        f"#define NATIVE_FNV 0x{fnv(image):08x}u\n#define STOCK_FNV 0x{fnv(stock):08x}u\n"
        f"#define CAPSULE_COOKIE 0x{cookie:08x}u\n")
    clang = shutil.which("clang")
    lld = sorted((Path.home() / ".rustup/toolchains").glob("*/lib/rustlib/*/bin/gcc-ld/ld.lld"))[-1]
    env = dict(os.environ)
    for parent in lld.parents:
        if (parent / "lib/libLLVM.dylib").is_file():
            env["DYLD_LIBRARY_PATH"] = str(parent / "lib")
            break
    flags = ["--target=armv7a-none-eabi", "-march=armv8-a", "-mcpu=cortex-a53", "-marm", "-mfloat-abi=soft",
             "-Oz", "-ffunction-sections", "-fdata-sections", "-fno-stack-protector", "-fno-unwind-tables",
             "-fno-asynchronous-unwind-tables", "-ffreestanding", "-fno-builtin", "-fstack-usage",
             "-DRAW_CAPSULE_ARM_PROTOCOL=1", "-I" + str(HERE / "include"), "-I" + str(ROOT / "src"),
             "-include", str(output / "config-values.h")]
    linker = """ENTRY(entry)
SECTIONS {
 .header 0x20000 : { KEEP(*(.header)) }
 .text 0x20400 : { KEEP(*(.text.entry)) *(.text*) *(.rodata*) *(.data*) }
 .copy : { KEEP(*(.copy)) }
 .stock 0x21000 : { KEEP(*(.stock)) }
 .native 0x31000 : { KEEP(*(.native)) }
 .bss (NOLOAD) : { *(.bss*) *(COMMON) }
 /DISCARD/ : { *(.ARM.exidx*) *(.ARM.extab*) *(.comment) *(.ARM.attributes) }
}
ASSERT(SIZEOF(.header)==0x400,"reserve vendor mutable header");
ASSERT(ADDR(.copy)+SIZEOF(.copy)<=0x21000,"dispatcher exceeds prefix");
ASSERT(SIZEOF(.copy)<=0x1000,"copy stub exceeds reserved extent");
ASSERT(SIZEOF(.stock)==0x10000,"stock must be64KiB");
ASSERT(SIZEOF(.native)==0xc000,"native must be48KiB");
ASSERT(SIZEOF(.bss)==0,"hidden BSS forbidden");
"""
    (output / "capsule.ld").write_text(linker)
    objects = []
    for name in ("entry.S", "capsule.c", "payloads.S"):
        obj = output / (name + ".o")
        subprocess.run([clang, *flags, "-c", str(HERE / name), "-o", str(obj)], cwd=output, check=True)
        objects.append(str(obj))
    for binary, name in ((False, "capsule.elf"), (True, "capsule.bin")):
        command = [str(lld), "--gc-sections", "-T", str(output / "capsule.ld"), "-Map=" + str(output / "capsule.map")]
        if binary:
            command += ["--oformat=binary"]
        subprocess.run([*command, *objects, "-o", str(output / name)], env=env, check=True)
    blob = bytearray((output / "capsule.bin").read_bytes())
    assert len(blob) == 0x1d000
    blob += bytes(0x20000 - len(blob))
    struct.pack_into("<II", blob, 12, 0x5f0a6c39, len(blob))
    struct.pack_into("<I", blob, 12, sum(struct.unpack("<32768I", blob)) & 0xffffffff)
    (output / "capsule.egon.bin").write_bytes(blob)
    prefix_end = int(re.search(r"^\s*([0-9a-f]+)\s+[0-9a-f]+\s+0\s+1\s+copy_end$",
                              (output / "capsule.map").read_text(), re.M)[1], 16)
    report = dict(target=native["target"], purpose=purpose or "raw-capsule outer commit and strict inner adoption",
                  diagnostic=diagnostic, native_diagnostic=False, normal_protocol_compiled=True,
                  generation=native["generation"], generation_cookie=cookie, pending=0x42535031,
                  inner_adoption_implemented=not diagnostic, native_manifest_sha256=sha(native_manifest_path.read_bytes()),
                  inner_manifest_sha256=sha((inner / "manifest.json").read_bytes()),
                  native_sha256=sha(image), stock_sha256=sha(stock), native_fnv=fnv(image), stock_fnv=fnv(stock),
                  raw_image_bytes=len(blob), raw_image_sha256=sha(blob), linked_prefix_end=hex(prefix_end),
                  prefix_free_bytes=0x21000-prefix_end, source_pins=capsule_pins, compiler_flags=flags,
                  contract=native["raw_capsule"], outer_checksum="Allwinner eGON additive32 over128KiB",
                  hardware_qualified=False, durable_install_eligible=False,
                  unresolved=["early refusal recovery policy", "physical WDT retention across inner RMR3",
                              "warm outer and actual ROM/media capsule qualification", "recovery TOC updater suppression",
                              "installation/torn-primary acceptance"])
    (output / "manifest.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({key: report[key] for key in ("target", "generation", "raw_image_sha256", "raw_image_bytes",
                                                "prefix_free_bytes", "diagnostic", "durable_install_eligible")}, indent=2))
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inner", type=Path)
    parser.add_argument("stock", type=Path)
    parser.add_argument("native_manifest", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--diagnostic-purpose", help="explicit nondeployable control using a non-adopting inner")
    args = parser.parse_args()
    build(args.inner.resolve(), args.stock.resolve(), args.native_manifest.resolve(), args.output.resolve(),
          diagnostic=bool(args.diagnostic_purpose), purpose=args.diagnostic_purpose)
