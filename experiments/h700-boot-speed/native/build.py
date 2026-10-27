#!/usr/bin/env python3
"""Build the RAM-only A32 loader and matched LZ4 image, without device access."""
import argparse
import gzip
import hashlib
import importlib.util
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import tarfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
SPL = HERE.parent / "spl"
PIN = "f943a88389c8fd70a6b0d061a88267465e143c7ca6ea2b75837ab20f5b60c739"


def sha(data): return hashlib.sha256(data).hexdigest()


def fnv(data):
    result = 2166136261
    for byte in data: result = (result ^ byte) * 16777619 & 0xffffffff
    return result


def toc_items(package):
    result = {}
    for index in range(struct.unpack_from("<I", package, 32)[0]):
        at = 64 + index * 368
        name = package[at:at + 64].split(b"\0", 1)[0].decode()
        offset, size = struct.unpack_from("<2I", package, at + 64)
        assert offset + size <= len(package) and name not in result
        result[name] = package[offset:offset + size]
    assert result.keys() == {"u-boot", "monitor", "dtbo", "dtb"}
    return result


def vendor_version_message(code):
    """Use the pinned build's literals used by the vendor command-line builder."""
    matches = list(re.finditer(rb"(\d{2}/\d{2}/\d{4})\0(2018\.05)\0 uboot_message=%s\(%s-%s\)\0(\d{2}:\d{2}:\d{2})\0", code))
    assert len(matches) == 1, "vendor version-message literals changed"
    date, version, time = (value.decode("ascii") for value in matches[0].groups())
    return f"uboot_message={version}({date}-{time})"


def model_policy(target, inventory):
    compatibility = json.loads((HERE.parent / "evidence/compatibility.json").read_text())["targets"]
    contract = compatibility[target]
    package = (ROOT / "work" / target / "boot-gzip/boot-package.bin").read_bytes()
    assert sha(package) == contract["package_sha256"]
    env = dict(item.split(b"=", 1) for item in (inventory / "env.bin").read_bytes()[4:].split(b"\0") if b"=" in item)
    prefix = (inventory / "prefix.bin").read_bytes()
    assert prefix[512:520] == b"EFI PART"
    table, count, entry_size = struct.unpack_from("<QII", prefix, 512 + 72)
    assert count <= 128 and entry_size == 128 and (table * 512 + count * entry_size) <= len(prefix)
    partitions, boot_lba, logo_partition = [], None, None
    for index in range(count):
        entry = prefix[table * 512 + index * entry_size:table * 512 + (index + 1) * entry_size]
        if not any(entry[:16]): continue
        name = entry[56:128].decode("utf-16-le").rstrip("\0")
        assert re.fullmatch(r"[A-Za-z0-9_-]+", name)
        partitions.append(f"{name}@mmcblk0p{index + 1}")
        if name == "boot": boot_lba = struct.unpack_from("<Q", entry, 32)[0]
        if name == "boot-resource":
            first, last = struct.unpack_from("<QQ", entry, 32)
            assert 0 < first <= last < 0x100000000
            logo_partition = (first, last - first + 1)
    assert boot_lba is not None and logo_partition is not None
    profile_spec = importlib.util.spec_from_file_location("native_device_profiles", ROOT / "tools/device_profile.py")
    profiles = importlib.util.module_from_spec(profile_spec)
    profile_spec.loader.exec_module(profiles)
    profile = next(item for item in profiles.load_profiles() if item["id"] == target)
    logo = {"partition_lba": logo_partition[0], "partition_sectors": logo_partition[1],
            "width": profile["bootlogo_width"], "height": profile["bootlogo_height"],
            "source": "mutable p2 BOOTLOGO.BMP; FAT16/FAT32; physical 24-bit BMP",
            "kernel_handoff": "boot_fb0 pixels; vendor warm metadata after early ready; owned pixel reservation held for boot"}
    assert 0 < logo["width"] * logo["height"] * 4 <= 0x400000
    values = {key.decode(): value.decode() for key, value in env.items()}
    values.update(partitions=":".join(partitions), snum="@SERIAL@")
    command = values["setargs_mmc"]
    assert re.match(r"setenv\s+bootargs ", command)
    command = re.sub(r"^setenv\s+bootargs ", "", command)
    def expand(match):
        key = match[1]
        assert key in values, f"unresolved bootargs field: {key}"
        return values[key]
    command = re.sub(r"\$\{([^}]+)\}", expand, command)
    assert "${" not in command and "disp_reserve=" not in command
    command += (" androidboot.mode=normal androidboot.serialno=@SERIAL@"
                " androidboot.hardware=sun50iw9p1 boot_type=1 androidboot.boot_type=1"
                " androidboot.secure_os_exist=0 gpt=1 " + vendor_version_message(toc_items(package)["u-boot"]) +
                " bootreason=@BOOTREASON@ lcd_type=@PANEL@ @ANDROID@")
    strap = contract["vendor_pe6_lcd_type"]
    rule = {("old", "old"): 0, ("boe", "boe"): 1, ("old", "boe"): 2}[strap["high"], strap["low"]]
    panel_contract = json.loads((HERE / "panel-contract.json").read_text())
    assert panel_contract["version"] == 1 and panel_contract["counter_hz"] == 24000000
    panel = panel_contract["models"][target]
    assert panel["package_sha256"] == sha(package)
    assert panel["vendor_uboot_sha256"] == sha(toc_items(package)["u-boot"])
    assert (panel["rule"], panel["high"], panel["low"]) == (rule, strap["high"], strap["low"])
    assert type(panel["pull_up"]) is bool
    policy = {"target": target, "model_id": sorted(compatibility).index(target) + 1,
            "panel_rule": rule, "panel_pull_up": panel["pull_up"],
            "panel_contract": panel, "bootargs_template": command, "boot_partition_lba": boot_lba,
            "boot_logo": logo,
            "package_sha256": sha(package), "environment_sha256": sha((inventory / "env.bin").read_bytes()),
            "native_record_version": 2, "native_record_bytes": 240,
            "bootreason_policy": "vendor PMIC reg20/reg00 classifier and unknown-only regf0 fallback",
            "bootreason_values": ["button", "irq", "usb", "charger", "battery", "unknow"],
            "bootreason_sources": ["unknown", "poweron", "pmu-f0-fallback"]}
    policy["native_panel"] = {"record_address": 0x4a300280, "record_bytes": 96, "record_version": 1,
        "producer": "SPL: PI15 power, PI14 reset 30/15/50/100 ms minimum waits and PE6 strap, polled between card reads",
        "consumer": "A32 loader: used only when every wait is proved and live pins match; otherwise it redoes the steps"}
    policy["kernel_decoder"] = {"source": "native/lz4-stream.c", "flags": "-O2 -munaligned-access",
        "sliced": True, "overlaps_display_waits": True}
    policy["early_display"] = {"runs_in": "a32-loader", "record_address": 0x4a300300, "record_bytes": 80,
        "vendor_dtb_sha256": sha(toc_items(package)["dtb"]),
        "first_frame": "A32 loader after BL31, before kernel unpack; panel/SPI/DE/TCON initialized",
        "kernel_handoff": "vendor warm display metadata only after validated ready record"}
    display_spec = importlib.util.spec_from_file_location("native_display_builder", HERE / "build-display.py")
    display = importlib.util.module_from_spec(display_spec)
    display_spec.loader.exec_module(display)
    display.contracts(target, policy)
    policy["early_display"]["handoff"] = display.handoff(target, policy)
    return policy


def build(target, inventory, output, single_cpu=False, kernel_probes=False, persistent=False, raw_capsule=False):
    assert not (persistent and raw_capsule), "choose one attempt mode"
    assert not output.exists(), "use a fresh immutable output directory"
    assert not single_cpu or kernel_probes, "single-CPU diagnosis requires kernel probes"
    assert not (persistent or raw_capsule) or not kernel_probes, "persistent builds require unmodified kernel/monitor"
    policy = model_policy(target, inventory)
    if persistent:
        policy["persistent_boot"] = {"version": 1, "pending_magic": 0x42535031,
            "lab_request": 0x42534c31, "lab_running": 0x42534c32,
            "journal_operation": 3, "cookie": "fnv1a32(raw generation32); zero forbidden",
            "healthy_media_writes": False, "failure_recovery": "restore-stock64K-and-system-reset"}
    if raw_capsule:
        policy["persistent_boot"] = {"version": 2, "pending_magic": 0x42535031,
            "cookie": "fnv1a32(raw generation32); zero forbidden", "commit_owner": "raw-capsule-outer",
            "inner_mode": "adopt-only", "healthy_media_writes": False,
            "failure_recovery": "watchdog reset then outer raw stock; no inner media restore"}
        policy["raw_capsule"] = json.loads((HERE / "capsule/contract.json").read_text())
    assert "maxcpus=" not in policy["bootargs_template"]
    policy["diagnostic_maxcpus"] = 1 if single_cpu else None
    policy["kernel_probes"] = kernel_probes
    if single_cpu: policy["bootargs_template"] += " maxcpus=1"
    archive = ROOT / "work/h700-loader/mainline/u-boot-v2026.10.tar.gz"
    assert sha(archive.read_bytes()) == PIN
    spec = importlib.util.spec_from_file_location("baseos_fast_builder", HERE.parent / "fastboot/build.py")
    fast = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fast)
    source_image = ROOT / "work" / target / "boot-gzip/boot.img"
    original = source_image.read_bytes()
    source_manifest = json.loads(source_image.with_name("manifest.json").read_text())
    assert sha(original) == source_manifest["boot_image_sha256"]
    parts, end = fast.kg.split(original)
    assert end == len(original) and not parts[2] and not parts[3]
    kernel = gzip.decompress(parts[0])
    assert sha(kernel) == source_manifest["kernel_sha256"]
    source_kernel_sha256 = sha(kernel)
    if kernel_probes:
        # Binary probes are restricted to the one inspected vendor Image.
        assert source_kernel_sha256 == "1332da28ef5dd078963c98cc4e11de816b8fddc7c7b75dab93b79eda68d01bf0"
    output.mkdir(parents=True)
    clang = shutil.which("clang")
    lld = sorted((Path.home() / ".rustup/toolchains").glob("*/lib/rustlib/*/bin/gcc-ld/ld.lld"))[-1]
    linker_env = dict(os.environ)
    for parent in lld.parents:
        if (parent / "lib/libLLVM.dylib").is_file():
            linker_env["DYLD_LIBRARY_PATH"] = str(parent / "lib")
            break
    if kernel_probes:
        subprocess.run([clang, "--target=aarch64-none-elf", "-c", str(HERE / "kernel-fault64.S"),
                        "-o", str(output / "kernel-fault.o")], check=True)
        (output / "kernel-fault.ld").write_text("SECTIONS { .text 0x4098d640 : { *(.text*) } }\n")
        subprocess.run([str(lld), "-T", str(output / "kernel-fault.ld"), "--oformat=binary",
                        str(output / "kernel-fault.o"), "-o", str(output / "kernel-fault.bin")],
                       check=True, env=linker_env)
        fault_code = (output / "kernel-fault.bin").read_bytes()
        fault_offset = 0x4098d640 - 0x40080000
        assert len(fault_code) == 0x9c0 and kernel[fault_offset:fault_offset + len(fault_code)] == bytes(len(fault_code))
        kernel = kernel[:fault_offset] + fault_code + kernel[fault_offset + len(fault_code):]
        subprocess.run([clang, "--target=aarch64-none-elf", "-c", str(HERE / "initcall64.S"),
                        "-o", str(output / "initcall.o")], check=True)
        (output / "initcall.ld").write_text(
            "initcall_resume = 0x40083958; SECTIONS { .text 0x4098d680 : { *(.text*) } }\n")
        subprocess.run([str(lld), "-T", str(output / "initcall.ld"), "--oformat=binary",
                        str(output / "initcall.o"), "-o", str(output / "initcall.bin")],
                       check=True, env=linker_env)
        initcall = (output / "initcall.bin").read_bytes()
        stub_offset, hook_offset = 0x4098d680 - 0x40080000, 0x3954
        assert len(initcall) == 164 and kernel[stub_offset:stub_offset + len(initcall)] == bytes(len(initcall))
        assert struct.unpack_from("<I", kernel, hook_offset)[0] == 0xa9b77bfd
        kernel = bytearray(kernel)
        kernel[stub_offset:stub_offset + len(initcall)] = initcall
        struct.pack_into("<I", kernel, hook_offset, 0x14000000 | ((0x4098d680 - 0x40083954) >> 2))
        kernel = bytes(kernel)
    parts[0] = fast.compress(kernel)
    package = (ROOT / "work" / target / "boot-gzip/boot-package.bin").read_bytes()
    assert sha(package) == policy["package_sha256"]
    toc = toc_items(package)
    source_monitor_sha256 = sha(toc["monitor"])
    if kernel_probes:
        subprocess.run([clang, "--target=aarch64-none-elf", "-c", str(HERE / "monitor-probe64.S"),
                        "-o", str(output / "monitor-probe.o")], check=True)
        (output / "monitor-probe.ld").write_text(
            "psci_resume = 0x480098e0; SECTIONS { .text 0x480000c0 : { *(.text*) } }\n")
        subprocess.run([str(lld), "-T", str(output / "monitor-probe.ld"), "--oformat=binary",
                        str(output / "monitor-probe.o"), "-o", str(output / "monitor-probe.bin")],
                       check=True, env=linker_env)
        monitor_probe = (output / "monitor-probe.bin").read_bytes()
        monitor = bytearray(toc["monitor"])
        assert len(monitor_probe) == 40 and monitor[0xc0:0xf8] == bytes(56)
        assert struct.unpack_from("<I", monitor, 0x98dc)[0] == 0xa9be7bfd
        assert struct.unpack_from("<I", monitor, 0x159d0)[0] == 0xa94847f0
        monitor[0xc0:0xf8] = monitor_probe.ljust(56, b"\0")
        for at, destination in ((0x98dc, 0xc0),):
            struct.pack_into("<I", monitor, at, 0x14000000 | (((destination - at) >> 2) & 0x3ffffff))
        toc["monitor"] = bytes(monitor)
    monitor = toc["monitor"]
    (output / "monitor.bin").write_bytes(monitor)
    # BaseOS modifies this tree in kernel_gzip.py. The old Android-embedded
    # tree still enables debug printing and five deferred USB controllers.
    parts[4] = toc["dtb"]
    image = fast.kg.join(original[:2048], parts, original[64:576].split(b"\0")[0])
    assert len(image) <= 0x1000000 and len(image) % 512 == 0
    (output / "image.bin").write_bytes(image)
    (output / "kernel.bin").write_bytes(kernel)
    (output / "policy.json").write_text(json.dumps(policy, indent=2) + "\n")
    lib = output / "libfdt"
    lib.mkdir()
    with tarfile.open(archive) as tar:
        for member in tar.getmembers():
            if member.isfile() and member.name.startswith("u-boot-2026.10/scripts/dtc/libfdt/"):
                (lib / Path(member.name).name).write_bytes(tar.extractfile(member).read())
    shim = output / "freestanding"
    shim.mkdir()
    (shim / "stdlib.h").write_text("#include <stddef.h>\n")
    (shim / "string.h").write_text("""#ifndef BASEOS_STRING_H
#define BASEOS_STRING_H
#include <stddef.h>
void *memcpy(void *, const void *, size_t);
void *memmove(void *, const void *, size_t);
void *memset(void *, int, size_t);
void *memchr(const void *, int, size_t);
int memcmp(const void *, const void *, size_t);
size_t strlen(const char *);
size_t strnlen(const char *, size_t);
int strcmp(const char *, const char *);
int strncmp(const char *, const char *, size_t);
char *strchr(const char *, int);
char *strrchr(const char *, int);
#endif
""")
    sys.path.insert(0, str(HERE.parent / "variant-survey"))
    binding_spec = importlib.util.spec_from_file_location("baseos_fdt_contract", HERE.parent / "variant-survey/fdt-contract.py")
    binding = importlib.util.module_from_spec(binding_spec)
    binding_spec.loader.exec_module(binding)
    inputs = [HERE / name for name in ("entry32.S", "loader.c", "runtime.c")]
    inputs += [HERE / "lz4-stream.c", SPL / "native-handoff.h", Path(__file__), HERE / "lz4-stream.h",
               SPL / "native-panel.h",
               HERE / "panel-probe.h", HERE / "panel-contract.json",
               HERE / "boot-logo-file.h", HERE / "boot-logo-pixels.h", SPL / "native-logo.h",
               HERE / "build-display.py", HERE / "early-display.c", HERE / "display-engine.json",
               HERE / "display-handoff.json",
               HERE / "panel-init.json", HERE / "panel-spi9.h", SPL / "native-display.h",
               ROOT / "devices.json", ROOT / "tools/device_profile.py",
               HERE / "kernel-trace64.S", HERE / "check-entry64.c",
               HERE / "kernel-fault64.S",
               HERE / "monitor-probe64.S", HERE / "initcall64.S",
               *[SPL / name for name in ("native-boot.c", "native-cache.c", "native-cache-off.S", "dram-select.c")]]
    if persistent or raw_capsule:
        inputs += [SPL / name for name in ("early-mmc.c", "build-early-mmc.py",
                                          "persistent-attempt.h", "stock-resume64.S", "dram-retry64.S")]
        inputs.append(ROOT / "src/native-attempt.h")
        inputs.append(HERE.parent / "variant-survey/fdt-contract.py")
    if raw_capsule:
        # Derived payload FNV/cookie headers and final binaries are excluded to avoid a generation cycle.
        inputs += [HERE / "capsule" / name for name in
                   ("contract.json", "build.py", "entry.S", "capsule.c", "payloads.S", "include/string.h")]
    source_boot0 = (inventory / "boot0.bin").read_bytes()
    generation = hashlib.sha256(json.dumps(policy, sort_keys=True).encode() + bytes.fromhex(sha(image)) +
                                bytes.fromhex(PIN) + source_boot0 +
                                b"".join(path.read_bytes() for path in inputs)).digest()
    if persistent or raw_capsule:
        assert fnv(generation), "zero-cookie generation cannot be installed"
    header = (f"#define BASEOS_MODEL_ID {policy['model_id']}u\n"
              f"#define BASEOS_PANEL_RULE {policy['panel_rule']}\n"
              f"#define BASEOS_PANEL_PULL_UP {int(policy['panel_pull_up'])}\n"
              f"#define BASEOS_LOGO_PARTITION_SECTOR {policy['boot_logo']['partition_lba']}u\n"
              f"#define BASEOS_LOGO_PARTITION_SECTORS {policy['boot_logo']['partition_sectors']}u\n"
              f"#define BASEOS_LOGO_WIDTH {policy['boot_logo']['width']}u\n"
              f"#define BASEOS_LOGO_HEIGHT {policy['boot_logo']['height']}u\n"
              f"#define BASEOS_DISPLAY_BOOT_DISP 0x{policy['early_display']['handoff']['boot_disp']:x}u\n"
              f"#define BASEOS_DISPLAY_BOOT_DISP1 0x{policy['early_display']['handoff']['boot_disp1']:x}u\n"
              f"#define BASEOS_DISPLAY_BOOT_DISP2 0x{policy['early_display']['handoff']['boot_disp2']:x}u\n"
              f"#define BASEOS_IMAGE_BYTES {len(image)}u\n#define BASEOS_IMAGE_FNV 0x{fnv(image):08x}u\n"
              f"static const unsigned char baseos_generation[32] = {{{','.join(map(str,generation))}}};\n"
              f"static const char baseos_bootargs_template[] = {json.dumps(policy['bootargs_template'])};\n"
              "static const char *const baseos_dram_properties[24] = {" +
              ",".join(json.dumps(name) for name in binding.DRAM_PROPERTIES) + "};\n")
    (output / "native-generated.h").write_text(header)
    shutil.copyfile(SPL / "native-handoff.h", output / "native-handoff.h")
    shutil.copyfile(SPL / "native-logo.h", output / "native-logo.h")
    shutil.copyfile(SPL / "native-display.h", output / "native-display.h")
    shutil.copyfile(SPL / "native-panel.h", output / "native-panel.h")
    shutil.copyfile(HERE / "lz4-stream.h", output / "lz4-stream.h")
    display_spec = importlib.util.spec_from_file_location("native_display_builder", HERE / "build-display.py")
    display = importlib.util.module_from_spec(display_spec)
    display_spec.loader.exec_module(display)
    display_manifest = display.build(output, target, policy, generation)
    if kernel_probes:
        subprocess.run([clang, "--target=aarch64-none-elf", "-c", str(HERE / "kernel-trace64.S"),
                        "-o", str(output / "trampoline.o")], check=True)
        (output / "native-trace-constants.h").write_text(
            f"#define BASEOS_KERNEL_BYTES {len(kernel)}u\n#define BASEOS_KERNEL_FNV 0x{fnv(kernel):08x}u\n")
        subprocess.run([clang, "--target=aarch64-none-elf", "-Os", "-mgeneral-regs-only", "-mstrict-align",
                        "-ffreestanding", "-fno-builtin", "-fno-stack-protector", "-fno-unwind-tables",
                        "-fno-asynchronous-unwind-tables", "-I", str(output), "-c", str(HERE / "check-entry64.c"),
                        "-o", str(output / "check-entry.o")], check=True)
        (output / "trace.ld").write_text("ENTRY(kernel_trace)\nSECTIONS { .text 0 : { *(.text.entry) *(.text*) *(.rodata*) } /DISCARD/ : { *(.comment) *(.ARM.attributes) } }\n")
        subprocess.run([str(lld), "-T", str(output / "trace.ld"), "--image-base=0", "--oformat=binary",
                        str(output / "trampoline.o"), str(output / "check-entry.o"),
                        "-o", str(output / "trampoline.bin")], check=True, env=linker_env)
        trampoline = (output / "trampoline.bin").read_bytes()
        assert 0 < len(trampoline) <= 1024 and len(trampoline) % 4 == 0
        (output / "native-trampoline.inc").write_text(".word " + ",".join(hex(value) for value in struct.unpack(f"<{len(trampoline)//4}I", trampoline)) + "\n")
    sources = inputs[:4] + [HERE / "early-display.c"] + [lib / name for name in ("fdt.c", "fdt_ro.c", "fdt_rw.c", "fdt_wip.c", "fdt_check.c")]
    diagnostic_flags = ["-DBASEOS_NATIVE_DIAGNOSTIC=1"] if kernel_probes else []
    objects = []
    for source in sources:
        obj = output / (source.name + ".o")
        # Only the kernel decoder runs hot enough to matter; it alone uses unaligned word access.
        tuning = ["-munaligned-access", "-O2"] if source.name == "lz4-stream.c" else ["-mno-unaligned-access", "-Os"]
        subprocess.run([clang, "--target=armv7a-none-eabi", "-march=armv7-a", "-marm", "-mfloat-abi=soft",
                        *tuning, "-ffreestanding", "-fno-builtin", "-fno-stack-protector",
                        "-fno-unwind-tables", "-fno-asynchronous-unwind-tables", "-ffunction-sections", "-fdata-sections",
                        "-Wall", "-Wextra", "-Werror", "-I", str(shim), "-I", str(lib), "-I", str(output),
                        *diagnostic_flags,
                        "-c", str(source), "-o", str(obj)], check=True)
        objects.append(str(obj))
    script = output / "link.ld"
    script.write_text("""ENTRY(native_entry)
SECTIONS {
  .text 0x4a000000 : { *(.text.entry) *(.text*) *(.rodata*) *(.data*) }
  .bss (NOLOAD) : { . = ALIGN(8); __bss_start = .; *(.bss*) *(COMMON) . = ALIGN(8); __bss_end = .; }
  ASSERT(__bss_end < 0x4a0e0000, "BL33 overlaps own stack")
  /DISCARD/ : { *(.ARM.exidx*) *(.ARM.extab*) *(.comment) *(.ARM.attributes) }
}
""")
    command = [str(lld), "-T", str(script), "--gc-sections", "-Map=" + str(output / "loader.map"), *objects]
    subprocess.run(command + ["-o", str(output / "loader.elf")], check=True, env=linker_env)
    subprocess.run(command + ["--oformat=binary", "-o", str(output / "loader.bin")], check=True, env=linker_env)
    blob = bytearray((output / "loader.bin").read_bytes())
    match = re.search(r"^\s*([0-9a-f]+)\s+[0-9a-f]+\s+[0-9a-f]+\s+\d+\s+native_kernel_trampoline\s*$",
                      (output / "loader.map").read_text(), re.M)
    assert bool(match) == kernel_probes
    # Header data is retained for BL31 readers; the only entry instruction is ours.
    blob[4:0x640] = package[0x804:0xe40]
    blob += bytes(-len(blob) % 512)
    assert len(blob) <= 0xe0000
    (output / "loader.bin").write_bytes(blob)
    firmware, regions = bytearray(), []
    for name, destination, payload in (("monitor", 0x48000000, toc["monitor"]),
                                        ("dtbo", 0x4a100000, toc["dtbo"]),
                                        ("dtb", 0x4a200000, toc["dtb"]),
                                        ("loader", 0x4a000000, bytes(blob))):
        limit = 0x40000 if name == "monitor" else 0x100000
        assert len(payload) <= limit
        regions.append({"name": name, "offset": len(firmware), "bytes": len(payload), "destination": destination})
        firmware.extend(payload)
        firmware.extend(bytes(-len(firmware) % 512))
    assert len(firmware) <= 0x100000
    (output / "firmware.bin").write_bytes(firmware)
    with (output / "native-generated.h").open("a") as stream:
        stream.write(f"\n#define BASEOS_FIRMWARE_BYTES {len(firmware)}u\n"
                     f"#define BASEOS_FIRMWARE_FNV 0x{fnv(firmware):08x}u\n"
                     "struct baseos_firmware_extent { unsigned int offset, bytes, destination; };\n"
                     f"static const struct baseos_firmware_extent baseos_firmware_extents[{len(regions)}] = {{\n" +
                     "".join(f"  {{{item['offset']}u, {item['bytes']}u, 0x{item['destination']:x}u}},\n" for item in regions) + "};\n")
    report = dict(policy, generation=generation.hex(), image_sha256=sha(image), image_bytes=len(image),
                  image_fnv=fnv(image), loader_sha256=sha(blob), loader_bytes=len(blob), loader_fnv=fnv(blob),
                  firmware_sha256=sha(firmware), firmware_bytes=len(firmware), firmware_fnv=fnv(firmware),
                  firmware_regions=regions, kernel_sha256=sha(kernel), libfdt_archive_sha256=PIN,
                  early_display_tables=display_manifest,
                  native_diagnostic=kernel_probes,
                  entry_checksum_witness=kernel_probes,
                  kernel_exception_witness=kernel_probes,
                  source_kernel_sha256=source_kernel_sha256,
                  source_monitor_sha256=source_monitor_sha256,
                  monitor_psci_probe=kernel_probes, kernel_initcall_probe=kernel_probes, monitor_sha256=sha(monitor),
                  kernel_dtb_source="prepared-toc1", kernel_dtb_sha256=sha(toc["dtb"]),
                  durable_install_eligible=False)
    if persistent or raw_capsule:
        report["source_boot0_sha256"] = sha(source_boot0)
        report["native_generated_header_sha256"] = sha((output / "native-generated.h").read_bytes())
        report["persistent_source_hashes"] = {
            str(path.relative_to(ROOT)): sha(path.read_bytes()) for path in inputs}
    if kernel_probes:
        report.update(kernel_smc_entry=int(match[1], 16), kernel_fault_vectors=0x4098d800, initcall_trace_gp=14)
    (output / "manifest.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({key: report[key] for key in ("target", "generation", "image_bytes", "loader_bytes",
                                                  "loader_sha256", "durable_install_eligible")}, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("target")
    parser.add_argument("inventory", type=Path)
    parser.add_argument("output", type=Path)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--raw-capsule", action="store_true", help="private outer-commit/inner-adopt generation")
    mode.add_argument("--persistent", action="store_true", help="bind the persistent RTC/SPL protocol; hardware acceptance still required")
    parser.add_argument("--kernel-probes", action="store_true", help="enable diagnostic kernel/monitor hooks and entry checksum")
    parser.add_argument("--single-cpu-diagnostic", action="store_true")
    args = parser.parse_args()
    if args.single_cpu_diagnostic and not args.kernel_probes:
        parser.error("--single-cpu-diagnostic requires --kernel-probes")
    build(args.target, args.inventory.resolve(), args.output.resolve(),
          single_cpu=args.single_cpu_diagnostic, kernel_probes=args.kernel_probes, persistent=args.persistent, raw_capsule=args.raw_capsule)
