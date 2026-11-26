"""Find the vendor U-Boot code that reads the panel strap and print it.

Locates the '--allen--PE_data' format string, finds the literal-pool word
that points at it, and disassembles the surrounding Thumb-2 code.
"""
import sys, struct
from capstone import Cs, CS_ARCH_ARM, CS_MODE_THUMB

UBOOT = 0x4A000000
ROOT = "work"
target = sys.argv[1]
needle = sys.argv[2].encode() if len(sys.argv) > 2 else b"--allen--PE_data"
pkg = open(f"{ROOT}/{target}/boot-gzip/boot-package.bin", "rb").read()
ub = pkg[0x800:0x800 + 0x100000]

soff = ub.find(needle)
if soff < 0:
    sys.exit(f"{target}: string not found")
saddr = UBOOT + soff
# literal pool references (32-bit words equal to the string address) or movw/movt pairs
refs = [i for i in range(0, len(ub) - 4, 4) if struct.unpack_from("<I", ub, i)[0] == saddr]
# movw/movt encoding search: imm16 split; search for movw rX,#lo16 followed within 8 bytes by movt rX,#hi16
lo, hi = saddr & 0xFFFF, saddr >> 16
def enc_movw(imm, t=False):
    # T3 MOVW: 11110 i 10 0100 imm4 | 0 imm3 Rd imm8 ; return (hw1, hw2) sans Rd
    i = (imm >> 11) & 1; imm4 = (imm >> 12) & 0xF; imm3 = (imm >> 8) & 7; imm8 = imm & 0xFF
    hw1 = 0xF240 | (i << 10) | imm4 | (0x80 if t else 0)
    hw2 = (imm3 << 12) | imm8
    return hw1, hw2
mw = enc_movw(lo); mt = enc_movw(hi, True)
for i in range(0, len(ub) - 8, 2):
    h1, h2 = struct.unpack_from("<HH", ub, i)
    if h1 == mw[0] and (h2 & 0x70FF) == mw[1]:
        h3, h4 = struct.unpack_from("<HH", ub, i + 4)
        if h3 == mt[0] and (h4 & 0x70FF) == mt[1]:
            refs.append(i)
print(f"{target}: string '{needle.decode()}' at {saddr:#x}; references at {[hex(r) for r in refs]}")

md = Cs(CS_ARCH_ARM, CS_MODE_THUMB); md.detail = False
for r in refs[:2]:
    start = max(0, r - 0x140) & ~1
    print(f"--- code around {r:#x}")
    for insn in md.disasm(ub[start:r + 0x80], UBOOT + start):
        mark = "  <== ref" if insn.address == UBOOT + r else ""
        print(f"  {insn.address - UBOOT:#07x}: {insn.mnemonic:8s} {insn.op_str}{mark}")
