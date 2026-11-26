#!/usr/bin/env python3
"""Build a nonpersistent SRAM probe from pinned, unmodified upstream DRAM code."""
import argparse
import hashlib
import json
import os
import re
import shutil
import struct
import subprocess
import tarfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
ARCHIVE_SHA = "f943a88389c8fd70a6b0d061a88267465e143c7ca6ea2b75837ab20f5b60c739"


def digest(data): return hashlib.sha256(data).hexdigest()


def build(archive: Path, output: Path, header_path: Path | None = None, autodetect: bool = False):
    if digest(archive.read_bytes()) != ARCHIVE_SHA:
        raise SystemExit("upstream v2026.10 archive hash differs")
    if output.exists():
        raise SystemExit("choose a fresh output directory; installed artifacts are immutable")
    output.mkdir(parents=True)
    header_path = header_path or ROOT / "work/h700-loader/vendor-live-boot0.bin"
    header = header_path.read_bytes()
    assert len(header) == 65536 and header[4:12] == b"eGON.BT0"
    words = struct.unpack_from("<32I", header, 0x38)
    types = {3: ("DDR3_1333", "h616_ddr3_1333.c", "ddr3"),
             7: ("LPDDR3", "h616_lpddr3.c", "lpddr3"),
             8: ("LPDDR4", "h616_lpddr4_2133.c", "lpddr4")}
    assert words[1] in types and words[0] in (672, 792)
    patches = []
    with tarfile.open(archive) as tar:
        def source(name): return tar.extractfile("u-boot-2026.10/" + name).read()
        sources = {}
        def adapt(name, filename):
            blob = source(name)
            sources[name] = digest(blob)
            # Keep all executable source; replace only its U-Boot include layer.
            body = re.sub(r"^#include[^\n]*\n", "", blob.decode(), flags=re.M)
            (output / filename).write_text(body)
        headers = ["clock_sun50i_h6.h", "cpu_sun50i_h6.h", "prcm_sun50i.h",
                   "dram_sun50i_h616.h", "dram_dw_helpers.h"]
        for name in headers:
            adapt("arch/arm/include/asm/arch-sunxi/" + name, name)
        files = ["dram_sun50i_h616.c", "dram_dw_helpers.c", "dram_helpers.c",
                 *["dram_timings/" + types[kind][1] for kind in (types if autodetect else (words[1],))]]
        for name in files:
            adapt("arch/arm/mach-sunxi/" + name, Path(name).name)
    if autodetect or words[1] == 7:
        path = output / "dram_sun50i_h616.c"
        body = path.read_text()
        # The April ROCKNIX correction is already present in pinned v2026.10.
        assert body.count("case SUNXI_DRAM_TYPE_LPDDR3:\n\t\tval = para->tpr6 >> 16 & 0xff;") == 1
    candidates = []
    if autodetect:
        timing_header = output / "dram_sun50i_h616.h"
        body = timing_header.read_text()
        assert body.count("static inline int ns_to_t(int nanoseconds)") == 1
        assert body.count("CONFIG_DRAM_CLK / 2") == 1
        timing_header.write_text(body.replace("static inline int ns_to_t(int nanoseconds)",
            "static inline int ns_to_t_for_clock(unsigned int dram_clk, int nanoseconds)").replace(
                "CONFIG_DRAM_CLK / 2", "dram_clk / 2"))
        for _, name, _ in types.values():
            path = output / name
            path.write_text("#define ns_to_t(ns) ns_to_t_for_clock(para->clk, ns)\n" + path.read_text())
        patches.append("Calculate timing cycles from each runtime candidate clock, including RG28XX 792 MHz DDR3")
        path = output / "dram_sun50i_h616.c"
        body = path.read_text()
        marker = "static const struct dram_para para = {"
        assert body.count(marker) == 1
        path.write_text(body[:body.index(marker)] +
                        "void baseos_set_master_priority(void) { mctl_set_master_priority(); }\n")
        patches.append("Replace the fixed DRAM entry with SRAM candidate retry and runtime timing dispatch")
        for offset in (0x38, 0x9f4, 0xa74):
            block = struct.unpack_from("<32I", header, offset)
            assert block[1] in types and block[0] in (672, 792)
            if block not in candidates: candidates.append(block)
        fields = {"clk": 0, "type": 1, "dx_odt": 2, "dx_dri": 3, "ca_dri": 4,
                  "odt_en": 5, "tpr0": 22, "tpr2": 24, "tpr6": 26,
                  "tpr10": 27, "tpr11": 28, "tpr12": 29}
        (output / "dram-candidates.h").write_text("static const struct dram_para dram_candidates[] = {\n" +
            "".join("  {" + ", ".join(f".{name}=0x{block[index]:x}u" for name, index in fields.items()) + "},\n"
                    for block in candidates) + "};\n")
    settings = {"CLK": words[0], "SUNXI_DX_ODT": words[2], "SUNXI_DX_DRI": words[3],
                "SUNXI_CA_DRI": words[4], "SUNXI_ODT_EN": words[5]}
    tpr_words = {0: 22, 2: 24, 6: 26, 10: 27, 11: 28, 12: 29}
    settings.update({"SUNXI_TPR" + str(i): words[index] for i, index in tpr_words.items()})
    shim = r'''
#ifndef PROBE_SHIM_H
#define PROBE_SHIM_H
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
typedef uint8_t u8;
typedef uint16_t u16;
typedef uint32_t u32;
typedef uint64_t u64;
typedef unsigned long ulong;
typedef unsigned long phys_addr_t;
#define BIT(n) (1u << (n))
#define GENMASK(h,l) ((~0u << (l)) & (~0u >> (31-(h))))
#define DIV_ROUND_UP(n,d) (((n)+(d)-1)/(d))
#define max(a,b) ((a) > (b) ? (a) : (b))
#define ARRAY_SIZE(a) (sizeof(a)/sizeof((a)[0]))
#define check_member(type,member,off) _Static_assert(offsetof(struct type,member)==(off), "register offset")
#define debug(...) ((void)0)
#define CONFIG_MACH_SUN50I_H616 1
#define CONFIG_DRAM_SUNXI_PHY_ADDR_MAP_1 1
#define CFG_SYS_SDRAM_BASE 0x40000000u
'''
    shim += "".join(f"#define CONFIG_DRAM_{key} 0x{value:x}u\n" for key, value in settings.items())
    shim += f"#define CONFIG_SUNXI_DRAM_H616_{types[words[1]][0]} 1\n"
    if autodetect: shim += "#define DRAM_AUTODETECT 1\nvoid baseos_set_master_priority(void);\n"
    shim += r'''
#define dsb() __asm__ volatile("dsb sy" ::: "memory")
#define dmb() __asm__ volatile("dmb sy" ::: "memory")
static inline u32 probe_readl(const volatile void *p) { u32 v=*(const volatile u32 *)p; dmb(); return v; }
static inline void probe_writel(u32 v, volatile void *p) { dmb(); *(volatile u32 *)p=v; }
#define readl(p) probe_readl((const volatile void *)(uintptr_t)(p))
#define writel(v,p) probe_writel(v,(volatile void *)(uintptr_t)(p))
#define writel_relaxed(v,p) (*(volatile u32 *)(p)=(v))
#define setbits_le32(p,v) writel(readl(p)|(v),p)
#define clrbits_le32(p,v) writel(readl(p)&~(v),p)
#define clrsetbits_le32(p,c,s) writel((readl(p)&~(c))|(s),p)
unsigned long timer_get_us(void);
void udelay(unsigned long);
__attribute__((noreturn)) void panic(const char *, ...);
unsigned long sunxi_dram_init(void);
void mctl_await_completion(u32 *, u32, u32);
bool mctl_mem_matches(u32);
bool mctl_mem_matches_base(u32, ulong);
bool mctl_check_memory(phys_addr_t);
void *memcpy(void *, const void *, size_t);
int printf(const char *, ...);
'''
    shim += "".join(f'#include "{name}"\n' for name in headers) + "#endif\n"
    (output / "shim.h").write_text(shim)
    clang = shutil.which("clang")
    lld = sorted((Path.home() / ".rustup/toolchains").glob("*/lib/rustlib/*/bin/gcc-ld/ld.lld"))[-1]
    env = dict(os.environ)
    for parent in lld.parents:
        if (parent / "lib/libLLVM.dylib").is_file():
            env["DYLD_LIBRARY_PATH"] = str(parent / "lib")
            break
    objects = []
    local_sources = [HERE / "probe-entry.S", HERE / "probe.c"]
    if autodetect: local_sources += [HERE / "dram-select.c", HERE / "dram-retry.S"]
    for path in local_sources + [output / Path(f).name for f in files]:
        target = output / (path.name + ".o")
        command = [clang, "--target=armv7a-none-eabi", "-march=armv7-a", "-marm", "-mfloat-abi=soft",
                   "-mno-unaligned-access", "-Os", "-ffreestanding", "-fno-builtin", "-fno-stack-protector",
                   "-fno-unwind-tables", "-fno-asynchronous-unwind-tables", "-I", str(output)]
        if path.suffix == ".c": command += ["-include", str(output / "shim.h")]
        if autodetect:
            if path.name in ("dram_sun50i_h616.c", "dram_dw_helpers.c", "dram_helpers.c"):
                command += ["-Dpanic=dram_panic"]
            for _, name, symbol in types.values():
                if path.name == name: command += ["-Dmctl_set_timing_params=timing_" + symbol]
        subprocess.run(command + ["-c", str(path), "-o", str(target)], check=True)
        objects.append(str(target))
    (output / "link.ld").write_text("ENTRY(probe_entry)\nSECTIONS { . = 0x20000; .text : { *(.text.entry) *(.text*) *(.rodata*) *(.data*) } .bss : { *(.bss*) *(COMMON) } /DISCARD/ : { *(.ARM.exidx*) *(.ARM.extab*) *(.comment) *(.ARM.attributes) } } ASSERT(. < 0x26000, \"SRAM payload overlaps stack\");\n")
    for binary, name in ((False, "probe.elf"), (True, "probe.bin")):
        command = [str(lld), "-T", str(output / "link.ld"), "-Map=" + str(output / "probe.map")]
        if binary: command += ["--oformat=binary"]
        subprocess.run(command + objects + ["-o", str(output / name)], check=True, env=env)
    blob = (output / "probe.bin").read_bytes()
    report = {"source": "https://github.com/u-boot/u-boot/tree/v2026.10", "archive_sha256": ARCHIVE_SHA,
              "sources": sources, "boot0_header_sha256": digest(header), "dram_settings": settings,
              "autodetect": autodetect, "candidate_types": [block[1] for block in candidates], "patches": patches,
              "probe_sha256": digest(blob), "probe_bytes": len(blob), "load_address": "0x20000",
              "status": "built; hardware and cold-entry support unproved"}
    (output / "manifest.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--boot0", type=Path, help="pinned live or prepared 64 KiB boot0 containing parameter sets")
    parser.add_argument("--autodetect", action="store_true", help="build the SRAM candidate-selection experiment")
    args = parser.parse_args()
    build(args.archive, args.output, args.boot0, args.autodetect)
