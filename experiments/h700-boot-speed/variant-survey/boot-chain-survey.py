"""Survey vendor boot chains of every target for hardware-variant handling."""
import struct, sys, os, re, subprocess
ROOT = "work"
TARGETS = ["rg28xx","rg34xx","rg34xxsp","rg35xxh","rg35xxplus","rg35xxpro","rg35xxsp","rg40xxh","rg40xxv","rgcubexx","rgsp"]
def boot0(t):
    d = open(f"{ROOT}/{t}/boot-prefix.img","rb").read()
    return d[8192:8192+0x1300]
def pkg(t): return open(f"{ROOT}/{t}/boot-gzip/boot-package.bin","rb").read()

print("### boot0 header: words that differ across targets (offset: per-target value)")
heads = {t: boot0(t) for t in TARGETS}
n = 0x1300//4
words = {t: struct.unpack("<%dI"%n, heads[t]) for t in TARGETS}
print("magic:", heads["rgsp"][4:12])
diffoffs = [i for i in range(n) if len({words[t][i] for t in TARGETS}) > 1]
print("differing word offsets:", [hex(i*4) for i in diffoffs])
for i in diffoffs:
    print(f"  {i*4:#06x}: " + " ".join(f"{t}={words[t][i]:#x}" for t in TARGETS))
# dram_para block: print 0x38..0x38+32*4 for rgsp as reference
print("### dram parameter block candidates (rgsp words 0x38..0xb8):")
print(" ".join(f"{w:#x}" for w in words["rgsp"][0x38//4:0xb8//4]))

print("\n### boot package items")
names = [b"u-boot", b"monitor", b"scp", b"dtb", b"dtbo", b"optee", b"soc-cfg", b"logo", b"dram"]
for t in TARGETS:
    p = pkg(t)
    items = []
    for nm in names:
        i = 0
        while True:
            i = p.find(nm + b"\0", i)
            if i < 0 or i > 0x2000: break
            if i % 16 == 0:  # item name field is aligned
                off, ln, enc, typ, run = struct.unpack_from("<5I", p, i+64)
                items.append(f"{nm.decode()}@{off:#x}+{ln:#x} run={run:#x}")
            i += 1
    print(f"  {t}: {items}")

print("\n### U-Boot: panel/variant detection strings per target")
for t in TARGETS:
    s = subprocess.run(["strings","-n","4"], input=pkg(t), capture_output=True).stdout.decode("latin1").splitlines()
    hits = sorted({x for x in s if re.search(r"lcd_type|allen|PE_pull|PE_data|panel_id|lcd_id|board_id|dram_type|auto.?scan|hw_ver|hardware.?ver|lcd_detect|detect_lcd", x, re.I)})
    ver = next((x for x in s if x.startswith("U-Boot 2018")), "?")
    print(f"  {t}: {ver}\n     {hits}")
