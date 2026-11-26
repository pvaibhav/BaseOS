#!/usr/bin/env python3
"""Build a pinned mainline SPL pre-DRAM SD control; it writes only scratch."""
import argparse
import hashlib
import json
import re
import shutil
import struct
import subprocess
import tarfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
PARENT = ROOT / "work/h700-loader/mainline"
PIN = "f943a88389c8fd70a6b0d061a88267465e143c7ca6ea2b75837ab20f5b60c739"


def fnv(data):
    value = 2166136261
    for byte in data: value = ((value ^ byte) * 16777619) & 0xffffffff
    return value


# DQ read/write bit delays (tpr11, tpr12) from U-Boot v2026.10's Anbernic H700
# defconfigs, which ROCKNIX also boots LPDDR4 units with.
LPDDR4_DEFAULT_DELAYS = (0x1b1f1e1c, 0x06060606)


def dram_candidates(header):
    """The pinned boot0's three DRAM banks, deduplicated, in boot0's order.

    Every model's boot0 carries a generic LPDDR4 bank whose tpr11/tpr12 are 0:
    vendor boot0 searches those delays itself, but mainline training programs
    them as given, and with zero delays size detection finds no aliasing and
    probes outside the DRAM window. Such banks get the mainline defaults; the
    BL31 copy gets the same words, as vendor boot0 hands on its found delays.
    """
    candidates = []
    for offset in (0x38, 0x9f4, 0xa74):
        block = struct.unpack_from("<32I", header, offset)
        assert block[0] in (672, 792) and block[1] in (3, 7, 8)
        assert block[1] != 3 or not block[30] & 0x80, "vendor special DDR3 remap is unsupported"
        if block[1] == 8 and block[28] == block[29] == 0:
            block = block[:28] + LPDDR4_DEFAULT_DELAYS + block[30:]
        if block not in candidates: candidates.append(block)
    return candidates


def native_dram(source, header):
    arch = source / "arch/arm/mach-sunxi"
    head = source / "arch/arm/include/asm/arch-sunxi/dram_sun50i_h616.h"
    body = head.read_text()
    assert body.count("static inline int ns_to_t(int nanoseconds)") == 1
    head.write_text(body.replace("static inline int ns_to_t(int nanoseconds)",
        "static inline int ns_to_t_for_clock(unsigned int dram_clk, int nanoseconds)").replace(
            "CONFIG_DRAM_CLK / 2", "dram_clk / 2"))
    core = arch / "dram_sun50i_h616.c"
    body = core.read_text()
    # Both candidate type and the vendor SID wiring choice are runtime data.
    map_boundary = body.index("#else /* CONFIG_DRAM_SUNXI_PHY_ADDR_MAP_0 */")
    maps = re.compile(r"static const u8 phy_init\[\] = \{\n"
        r"#ifdef CONFIG_SUNXI_DRAM_H616_DDR3_1333\n(.*?)"
        r"#elif defined\(CONFIG_SUNXI_DRAM_H616_LPDDR3\)\n(.*?)"
        r"#elif defined\(CONFIG_SUNXI_DRAM_H616_LPDDR4\)\n(.*?)#endif\n\};", re.S)
    def runtime_maps(match):
        assert all(len(re.findall(r"0x[0-9a-f]+", values)) == 27 for values in match.groups())
        variant = 1 if match.start() < map_boundary else 0
        return f"static const u8 phy_init_map{variant}[][27] = {{\n" + "".join(
            f"\t[SUNXI_DRAM_TYPE_{kind}] = {{\n{values.rstrip()}\n\t}},\n"
            for kind, values in zip(("DDR3", "LPDDR3", "LPDDR4"), match.groups())) + "};"
    body, count = maps.subn(runtime_maps, body)
    assert count == 2, "both upstream PHY wiring variants must retain every candidate type"
    for directive in ("#ifdef CONFIG_DRAM_SUNXI_PHY_ADDR_MAP_1\n",
                      "#else /* CONFIG_DRAM_SUNXI_PHY_ADDR_MAP_0 */\n",
                      "#endif /* CONFIG_DRAM_SUNXI_PHY_ADDR_MAP_0 */\n"):
        assert body.count(directive) == 1
        body = body.replace(directive, "")
    old = "for (i = 0; i < ARRAY_SIZE(phy_init); i++)\n\t\twritel(phy_init[i], &ptr[i]);"
    assert body.count(old) == 1
    body = body.replace(old, "for (i = 0; i < ARRAY_SIZE(phy_init_map0[0]); i++)\n"
                            "\t\twritel(phy_init[para->type][i], &ptr[i]);")
    old = "\tptr = (u32 *)(SUNXI_DRAM_PHY0_BASE + 0xc0);"
    assert body.count(old) == 1
    body = body.replace(old,
        "\tu32 sid = readl(SUNXI_SID_BASE);\n"
        "\tconst u8 (*phy_init)[27] = (sid & 0xfbff) == 0x5000 ||\n"
        "\t\t(sid & 0xfeff) == 0x5c00 || (sid & 0xf7ff) == 0x2000 ?\n"
        "\t\tphy_init_map0 : phy_init_map1;\n" + old)
    marker = "static const struct dram_para para = {"
    assert body.count(marker) == 1
    core.write_text(body[:body.index(marker)] +
        "void baseos_set_master_priority(void) { mctl_set_master_priority(); }\n")
    fields = {"clk": 0, "type": 1, "dx_odt": 2, "dx_dri": 3, "ca_dri": 4,
              "odt_en": 5, "tpr0": 22, "tpr2": 24, "tpr6": 26,
              "tpr10": 27, "tpr11": 28, "tpr12": 29}
    candidates = dram_candidates(header)
    (arch / "dram-candidates.h").write_text("static const struct dram_para dram_candidates[] = {\n" +
        "".join("  {" + ", ".join(f".{key}=0x{block[index]:x}u" for key,index in fields.items()) + "},\n"
                for block in candidates) + "};\nstatic const unsigned int dram_vendor_candidates[][32] = {\n" +
        "".join("  {" + ", ".join(hex(value) + "u" for value in block) + "},\n" for block in candidates) + "};\n")
    shutil.copyfile(HERE / "dram-select.c", arch / "baseos_dram_select.c")
    shutil.copyfile(HERE / "dram-retry64.S", arch / "baseos_dram_retry.S")
    for name, kind in (("h616_ddr3_1333.c", "ddr3"), ("h616_lpddr3.c", "lpddr3"),
                       ("h616_lpddr4_2133.c", "lpddr4")):
        path = arch / "dram_timings" / name
        path.write_text(f"#define mctl_set_timing_params timing_{kind}\n" +
                        "#define ns_to_t(ns) ns_to_t_for_clock(para->clk, ns)\n" + path.read_text())
    makefile = arch / "Makefile"
    makefile.write_text(makefile.read_text() + "\nifdef CONFIG_XPL_BUILD\n" +
        "obj-y += baseos_dram_select.o baseos_dram_retry.o\n" +
        "CFLAGS_baseos_dram_select.o += -DDRAM_NATIVE=1\n" +
        "CFLAGS_dram_sun50i_h616.o += -Dpanic=dram_panic\n" +
        "CFLAGS_dram_dw_helpers.o += -Dpanic=dram_panic\n" +
        "CFLAGS_dram_helpers.o += -Dpanic=dram_panic\nendif\n")
    makefile = arch / "dram_timings/Makefile"
    makefile.write_text(makefile.read_text() + "\nobj-$(CONFIG_MACH_SUN50I_H616) += h616_ddr3_1333.o h616_lpddr3.o\n")
    return [block[1] for block in candidates]


def main(output: Path, boot0: Path | None = None, dram: bool = False, package: Path | None = None, trace=False,
         native_loader: Path | None = None, persistent: bool = False, raw_capsule: bool = False):
    archive = PARENT / "u-boot-v2026.10.tar.gz"
    assert hashlib.sha256(archive.read_bytes()).hexdigest() == PIN
    assert output.parent.resolve() == PARENT and not output.exists(), "choose a fresh directory under mainline/"
    output.mkdir()
    with tarfile.open(archive) as tar: tar.extractall(output, filter="data")
    source = output / "u-boot-2026.10"
    backup = (boot0 or ROOT / "work/h700-loader/after-dram-boot0.bin").read_bytes()
    assert len(backup) == 65536 and backup[4:12] == b"eGON.BT0"
    (output / "restore-source.bin").write_bytes(backup)
    shutil.copyfile(HERE / "early-mmc.c", source / "arch/arm/mach-sunxi/baseos_early_mmc.c")
    makefile = source / "arch/arm/mach-sunxi/Makefile"
    makefile.write_text(makefile.read_text() + "\nifdef CONFIG_XPL_BUILD\nobj-y += baseos_early_mmc.o\nendif\n")
    candidates = native_dram(source, backup) if dram else []
    if package:
        assert dram and len(package.read_bytes()) == 0x140000
        (output / "handoff-package.bin").write_bytes(package.read_bytes())
        shutil.copyfile(HERE / "vendor-handoff.c", source / "arch/arm/mach-sunxi/baseos_vendor_handoff.c")
        shutil.copyfile(HERE / "fdt-stamp.c", source / "arch/arm/mach-sunxi/baseos_fdt_stamp.c")
        shutil.copyfile(HERE / "handoff-trace.h", source / "arch/arm/mach-sunxi/handoff-trace.h")
        makefile.write_text(makefile.read_text() + "\nifdef CONFIG_XPL_BUILD\nobj-y += baseos_vendor_handoff.o baseos_fdt_stamp.o\nendif\n")
    assert not raw_capsule or (native_loader and package and dram and not trace and not persistent), "raw is a separate clean adoption mode"
    assert not persistent or (native_loader and package and dram and not trace), "persistent requires clean native payloads"
    native_manifest = None
    if native_loader:
        assert package and not trace
        native_manifest = json.loads((native_loader / "manifest.json").read_text())
        assert bool(native_manifest.get("persistent_boot")) == (persistent or raw_capsule), "native generation/persistent policy mismatch"
        if persistent: assert native_manifest["persistent_boot"]["version"] == 1, "raw generation requires --raw-capsule"
        native_header = (native_loader / "native-generated.h").read_bytes()
        if persistent or raw_capsule:
            assert hashlib.sha256(backup).hexdigest() == native_manifest["source_boot0_sha256"], "stock source differs from native generation"
            assert hashlib.sha256(native_header).hexdigest() == native_manifest["native_generated_header_sha256"], "native generated header differs from manifest"
            for relative, digest in native_manifest["persistent_source_hashes"].items():
                pinned = (ROOT / relative).resolve()
                assert pinned.is_relative_to(ROOT), "persistent source pin escapes repository"
                assert hashlib.sha256(pinned.read_bytes()).hexdigest() == digest, f"persistent source changed: {relative}"
        assert hashlib.sha256(package.read_bytes()).hexdigest() == native_manifest["package_sha256"]
        for name in ("loader", "image", "firmware"):
            payload = (native_loader / f"{name}.bin").read_bytes()
            assert hashlib.sha256(payload).hexdigest() == native_manifest[f"{name}_sha256"]
        arch = source / "arch/arm/mach-sunxi"
        shutil.copyfile(HERE / "native-boot.c", arch / "baseos_vendor_handoff.c")
        for name in ("native-handoff.h", "native-logo.h", "native-display.h", "native-panel.h", "native-cache.c", "native-cache-off.S"):
            shutil.copyfile(HERE / name, arch / name)
        shutil.copyfile(HERE.parent / "native/boot-logo-file.h", arch / "boot-logo-file.h")
        header = native_header.decode()
        header += (f"\n#define BASEOS_FIRMWARE_SECTOR {native_manifest['boot_partition_lba'] + 40 * 2048}ul\n"
                   f"#define BASEOS_IMAGE_SECTOR {native_manifest['boot_partition_lba'] + 48 * 2048}ul\n")
        (arch / "native-generated.h").write_text(header)
        makefile.write_text(makefile.read_text().replace("baseos_vendor_handoff.o baseos_fdt_stamp.o",
            "baseos_vendor_handoff.o native-cache.o native-cache-off.o"))
        (output / "native-loader.json").write_text(json.dumps(native_manifest, indent=2) + "\n")
    if raw_capsule:
        assert native_manifest["persistent_boot"]["version"] == 2 and native_manifest["raw_capsule"]["mode"] == "raw-capsule-outer-commit-inner-adopt"
        cookie = fnv(bytes.fromhex(native_manifest["generation"]))
        assert cookie
        arch = source / "arch/arm/mach-sunxi"
        shutil.copyfile(ROOT / "src/native-attempt.h", arch / "persistent-attempt.h")
        (arch / "raw-capsule-generated.h").write_text(
            f"#define BASEOS_ATTEMPT_COOKIE 0x{cookie:08x}u\n" +
            "static const unsigned char baseos_raw_board[8] = {" + ",".join(str(b) for b in backup[0xf0:0xf8]) + "};\n")
    if persistent:
        cookie = fnv(bytes.fromhex(native_manifest["generation"]))
        assert cookie, "zero-cookie generation cannot be installed"
        journal = bytearray(512)
        struct.pack_into("<4I", journal, 0, 0x50534f42, 1, 3, 10)
        journal[384:416] = bytes.fromhex(native_manifest["generation"])
        struct.pack_into("<I", journal, 508, fnv(journal[:508]))
        (output / "persistent-journal.bin").write_bytes(journal)
        arch = source / "arch/arm/mach-sunxi"
        shutil.copyfile(ROOT / "src/native-attempt.h", arch / "persistent-attempt.h")
        shutil.copyfile(HERE / "stock-resume64.S", arch / "stock-resume64.S")
        (arch / "persistent-generated.h").write_text(
            f"#define BASEOS_ATTEMPT_COOKIE 0x{cookie:08x}u\n"
            "#define BASEOS_LAB_MAGIC 0x42534c31u\n#define BASEOS_LAB_RUNNING 0x42534c32u\n"
            f"#define BASEOS_BOOT0_HEADER_FNV 0x{fnv(backup[:512]):08x}u\n"
            f"#define BASEOS_PERSISTENT_JOURNAL_FNV 0x{fnv(journal):08x}u\n")
    board = source / "arch/arm/mach-sunxi/board.c"
    body = board.read_text()
    old = "void board_init_f(ulong dummy)\n{\n"
    assert body.count(old) == 1
    entry = "\textern char __bss_start[], __bss_end[];\n\textern void baseos_early_mmc(void);\n"
    if raw_capsule:
        entry += ("\textern void baseos_raw_adopt(void);\n\tbaseos_raw_adopt();\n"
                  "\tmemset(__bss_start, 0, __bss_end - __bss_start);\n")
    elif persistent:
        entry += ("\textern void baseos_persistent_prepare(int);\n"
            "\tint resume = !!(readl((void *)0x070001f8) & BIT(16));\n"
            "\tmemset(__bss_start, 0, __bss_end - __bss_start);\n"
            "\tif (!resume) {\n\t\twritel(1, (void *)0x030090b4);\n"
            "\t\twritel(0xb1, (void *)0x030090b8);\n\t\twritel(0x14af, (void *)0x030090b0);\n\t}\n"
            "\tbaseos_persistent_prepare(resume);\n")
        body = body.replace("\tclock_init();", "\tif (!resume) clock_init();")
        body = body.replace("\tpreloader_console_init();", "\tif (!resume) preloader_console_init();")
        body = body.replace("\ti2c_init_board();\n\ti2c_init(CONFIG_SYS_I2C_SPEED, CONFIG_SYS_I2C_SLAVE);",
            "\tif (!resume) {\n\t\ti2c_init_board();\n\t\ti2c_init(CONFIG_SYS_I2C_SPEED, CONFIG_SYS_I2C_SLAVE);\n\t}")
    else:
        entry += ("\twritel(1, (void *)0x030090b4);\n\twritel(0xb1, (void *)0x030090b8);\n"
                  "\twritel(0x14af, (void *)0x030090b0);\n"
                  "\tmemset(__bss_start, 0, __bss_end - __bss_start);\n")
    body = body.replace(old, old + entry)
    assert body.count("\tspl_init();") == 1
    body = body.replace("\tspl_init();", "\tspl_early_init();")
    assert body.count("\tsunxi_board_init();") == 1
    body = body.replace("\tsunxi_board_init();", "\tbaseos_early_mmc();\n\tsunxi_board_init();")
    board.write_text(body)
    mount = "/work/" + output.name + "/u-boot-2026.10"
    flags = f"-DBASEOS_BOOT0_FNV=0x{fnv(backup):08x}u" + (" -DBASEOS_DRAM_CONTROL=1" if dram else "")
    if package: flags += f" -DBASEOS_VENDOR_HANDOFF=1 -DBASEOS_TOC_FNV=0x{fnv(package.read_bytes()):08x}u"
    if trace:
        assert package, "trace requires the monitor handoff"
        flags += " -DBASEOS_TRACE_HANDOFF=1"
    if persistent:
        flags += " -DBASEOS_PERSISTENT=1 -DBASEOS_PERSISTENT_LAB=1"
    if raw_capsule:
        flags += " -DBASEOS_RAW_CAPSULE=1"
    resume_command = ""
    if persistent:
        resume_command = ("aarch64-linux-gnu-gcc -c arch/arm/mach-sunxi/stock-resume64.S -o ../stock-resume64.o\n"
            "aarch64-linux-gnu-objcopy -O binary -j .text ../stock-resume64.o ../stock-resume64.bin\n"
            "python3 -c 'import pathlib,struct; b=pathlib.Path(\"../stock-resume64.bin\").read_bytes(); "
            "assert len(b)==188; pathlib.Path(\"arch/arm/mach-sunxi/stock-resume64.inc\").write_text("
            "\",\".join(hex(w) for w in struct.unpack(\"<47I\",b))+\",\\n\")'\n")
    command = f'''unset PKG_CONFIG_SYSROOT_DIR PKG_CONFIG_PATH CC CXX LD AR AS CPP ARCH
export SOURCE_DATE_EPOCH=1790899200
cd {mount}
{resume_command}make CROSS_COMPILE=aarch64-linux-gnu- anbernic_rg35xx_h700_defconfig > ../configure.log 2>&1
scripts/config --set-val SPL_BSS_START_ADDR 0x30000 --set-val SPL_BSS_MAX_SIZE 0x4000 --enable SPL_MMC_WRITE
scripts/config --disable AXP_DISABLE_BOOT_ON_POWERON
make CROSS_COMPILE=aarch64-linux-gnu- olddefconfig >> ../configure.log 2>&1
make HOSTCC=/usr/bin/gcc HOSTCXX=/usr/bin/g++ CROSS_COMPILE=aarch64-linux-gnu- KCFLAGS='{flags}' -j8 spl/u-boot-spl.bin > ../build.log 2>&1
'''
    if raw_capsule:
        command = command.replace("--enable SPL_MMC_WRITE", "--disable SPL_MMC_WRITE")
    (output / "build.sh").write_text(command)
    subprocess.run(["docker", "exec", "baseos-h700-mainline", "/bin/sh", "-euc", command], check=True)
    blob = (source / "spl/sunxi-spl.bin").read_bytes()
    symbols = subprocess.run(["docker", "exec", "baseos-h700-mainline", "aarch64-linux-gnu-nm", "-n",
                              mount + "/spl/u-boot-spl"], check=True, capture_output=True, text=True).stdout
    (output / "symbols.txt").write_text(symbols)
    assert len(blob) <= 0xc000
    (output / "spl.bin").write_bytes(blob)
    report = {"upstream_archive_sha256": PIN, "spl_sha256": hashlib.sha256(blob).hexdigest(),
              "spl_bytes": len(blob), "entry": "0x20060", "bss": "0x30000",
              "restore_source_sha256": hashlib.sha256(backup).hexdigest(), "restore_source_fnv": hex(fnv(backup)),
              "source_sector": "0x56001", "mirror_sector": "0x56200", "restores_primary": False,
              "native_dram": dram, "candidate_types": candidates, "early_watchdog": True}
    report["supports_primary_restore"] = not raw_capsule
    if raw_capsule:
        assert not (output / "persistent-journal.bin").exists() and not (output / "stock-resume64.bin").exists()
        report.update(raw_capsule=native_manifest["raw_capsule"], persistent_boot=native_manifest["persistent_boot"],
            protocol_version=2, inner_mode="adopt-only", generation_cookie=cookie,
            embedded_board8=backup[0xf0:0xf8].hex(), embedded_board_source_offset="0xf0",
            early_watchdog=False, adopts_existing_watchdog=True, normal_media_writes=False,
            source_sector=None, mirror_sector=None, resume_compiled=False, journal_compiled=False,
            hardware_qualified=False, durable_install_eligible=False)
    if persistent:
        report.update(persistent_boot=native_manifest["persistent_boot"],
            generation_cookie=cookie, immutable_journal_sha256=hashlib.sha256(journal).hexdigest(),
            immutable_journal_fnv=fnv(journal), source_header_fnv=fnv(backup[:512]),
            normal_media_writes=False, restore_first=False,
            resume_trampoline_sha256=hashlib.sha256((output / "stock-resume64.bin").read_bytes()).hexdigest(),
            durable_install_eligible=False)
    if package: report["handoff_package_sha256"] = hashlib.sha256(package.read_bytes()).hexdigest()
    report["rtc_handoff_trace"] = trace
    if native_manifest: report["native_loader"] = native_manifest
    (output / "manifest.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--boot0", type=Path)
    parser.add_argument("--dram", action="store_true")
    parser.add_argument("--handoff-package", type=Path)
    parser.add_argument("--trace-handoff", action="store_true")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--raw-capsule", action="store_true", help="adopt the exact outer-owned attempt; no journal/resume/media writes")
    mode.add_argument("--persistent", action="store_true", help="immutable journal, RTC attempt, failure-only recovery; acceptance required")
    parser.add_argument("--native-loader", type=Path)
    args = parser.parse_args()
    main(args.output.resolve(), args.boot0, args.dram, args.handoff_package, args.trace_handoff, args.native_loader, persistent=args.persistent, raw_capsule=args.raw_capsule)
