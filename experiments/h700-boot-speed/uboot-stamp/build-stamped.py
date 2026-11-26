#!/usr/bin/env python3
"""Build boot-package-stamped.bin: the rg34xxsp TOC1 package with six counter stamps.

Each stamp redirects one existing Thumb-2 BL to a stub that stores CNTPCT
(64-bit, 24 MHz) into low DRAM and tail-branches (b.w) to the original target.
Registers r0-r3 are pushed and popped; flags, r4-r12, lr and sp are untouched.

Stub placement: NOT the 0xFF tail of the item (0xFDAA0..0x100000). That range is
inside .bss (__bss_start 0x4A0E6450 .. __bss_end 0x4A133848, see crt0 at 0xF94)
and outside the copied image (__image_copy_end 0x4A0E6450, relocate_code at
0x136C), so crt0 zeroes it before board_init_r and later uses it for variables.
The stubs go into the long help text of the `sunxi_nand_test` command
(.rodata 0x92FD7, 2119 bytes); its only reference is that command's table entry.
Its first byte is set to NUL so `help sunxi_nand_test` prints an empty text.

    /tmp/uboot-venv/bin/python work/rg34xxsp-stamp/build-stamped.py
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import struct
import sys
from pathlib import Path

sys.dont_write_bytecode = True
# `work` is a symlink shared by several worktrees: take the checkout from the
# path the script was started by (not its resolved location), or from the cwd.
HERE = Path(__file__).absolute().parent
REPO = next((p for p in (HERE.parent.parent, Path.cwd()) if (p / "tools/kernel_gzip.py").exists()), None)
if REPO is None:
    sys.exit("run from the repository checkout: tools/kernel_gzip.py not found")
SOURCE = REPO / "work/rg34xxsp/boot-gzip/boot-package.bin"
OUTPUT = HERE / "boot-package-stamped.bin"
SOURCE_SHA256 = "32f92785f3e0819acbd60776e2ef839c1fd1924b31960b98363a93c85300db7e"

ITEM, ITEM_SIZE, LINK = 0x800, 0x100000, 0x4A000000
STAMP_BASE = 0x40000100
MAGIC = 0x5354414D                      # "STAM" little-endian word, at STAMP_BASE - 8
HELP_TEXT, HELP_LEN = 0x92FD7, 2119     # sunxi_nand_test long help (.rodata)
HELP_FIELD = 0xE6210 + 20               # its cmd_tbl entry's help pointer
COMMAND_ENTRY = 0xE6210
REL_DYN_END = 0xFDAA0
STUB_AREA = (HELP_TEXT + 4) & ~3        # 0x92FD8, first aligned byte after the NUL we write
# Layout constants read from the binary and checked below.
IMAGE_COPY_END_LIT, BSS_LITS, REL_DYN_LITS = 0x13C8, (0xFC4, 0xFC8), (0x13CC, 0x13D0)

# (slot, call site, expected original target, meaning)
HOOKS = [
    (0, 0x2B22, 0x2AA8, "board_late_init: logo routine 0x2A2C returned (call of 0x2AA8)"),
    (1, 0xF13E, 0xEA90, "run_main_loop: call of main_loop (vendor post-logo steps done)"),
    (2, 0xC1FC, 0x0FE0, "do_sunxi_flash: first BL (memset of its local buffer): card read starts"),
    (3, 0x7E80, 0xE8F8, "do_bootm: first BL (env_get boot_from_partion): card read done, bootm starts"),
    (4, 0x152A, 0x13FC, "do_bootm_linux(OS_PREP): call of boot_prep_linux: kernel unpacked"),
    (5, 0x1482, 0xE8F8, "boot_jump_linux: env_get(machid): about to jump to the kernel"),
]


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


sys.path.insert(0, str(REPO / "tools"))
kg = load("kernel_gzip_stamp", REPO / "tools/kernel_gzip.py")
harness = load("fast_boot_emulation_stamp", REPO / "tests/test-fast-boot-emulation.py")
require, word = kg.require, kg.word


# ---------------------------------------------------------------- Thumb-2 encoding
def encode_branch(at: int, target: int, link: bool) -> bytes:
    """BL (link) or B.W (T4) at offset `at` to offset `target`, both in the same image."""
    offset = target - (at + 4)
    require(offset % 2 == 0 and -(1 << 24) <= offset < (1 << 24), f"branch {at:#x}->{target:#x} out of range")
    s = (offset >> 24) & 1
    i1, i2 = (offset >> 23) & 1, (offset >> 22) & 1
    j1, j2 = (1 - i1) ^ s, (1 - i2) ^ s
    first = 0xF000 | (s << 10) | ((offset >> 12) & 0x3FF)
    second = (0xD000 if link else 0x9000) | (j1 << 13) | (j2 << 11) | ((offset >> 1) & 0x7FF)
    blob = struct.pack("<HH", first, second)
    require(harness.branch_target(blob.rjust(at + 4, b"\0"), at) == target, "branch encoder disagrees with decoder")
    return blob


PUSH_R0_R3, POP_R0_R3, NOP = 0xB40F, 0xBC0F, 0xBF00
MRRC_CNTPCT_R0_R1 = struct.pack("<HH", 0xEC51, 0x0F0E)   # mrrc p15, 0, r0, r1, c14 (same bytes as vendor 0xD64)
MOVW_R3_0 = struct.pack("<HH", 0xF04F, 0x0300)           # mov.w r3, #0 (no flags)


def ldr_lit(rt: int, at: int, literal: int) -> int:
    """16-bit ldr rt, [pc, #imm] at `at` loading the word at `literal`."""
    base = (at + 4) & ~3
    imm = literal - base
    require(literal % 4 == 0 and 0 <= imm <= 1020 and imm % 4 == 0, "literal out of reach")
    return 0x4800 | (rt << 8) | (imm // 4)


def str_imm(rt: int, rn: int, imm: int) -> int:
    require(imm % 4 == 0 and 0 <= imm <= 124, "str offset")
    return 0x6000 | ((imm // 4) << 6) | (rn << 3) | rt


def h(*halfwords: int) -> bytes:
    return struct.pack(f"<{len(halfwords)}H", *halfwords)


def stub(at: int, slot: int, target: int) -> bytes:
    """Stub code at offset `at` (4-aligned) for `slot`, ending in b.w target, literals after."""
    require(at % 4 == 0, "stub not aligned")
    if slot == 0:
        lit_base, lit_magic = at + 48, at + 52
        code = (h(PUSH_R0_R3) + MRRC_CNTPCT_R0_R1 +
                h(ldr_lit(2, at + 6, lit_base), ldr_lit(3, at + 8, lit_magic),
                  str_imm(3, 2, 0)) +                     # [base-8] = "STAM"
                MOVW_R3_0 +
                h(str_imm(0, 2, 8), str_imm(1, 2, 12)) +  # slot 0 = counter
                h(*[str_imm(3, 2, off) for off in range(16, 56, 4)]) +  # slots 1..5 = 0
                h(POP_R0_R3))
        code += encode_branch(at + len(code), target, link=False)
        code += h(NOP) * ((48 - len(code)) // 2)
        require(len(code) == 48, "stub 0 layout")
        return code + struct.pack("<II", STAMP_BASE - 8, MAGIC)
    lit = at + 20
    code = (h(PUSH_R0_R3) + MRRC_CNTPCT_R0_R1 +
            h(ldr_lit(2, at + 6, lit), str_imm(0, 2, 0), str_imm(1, 2, 4), POP_R0_R3))
    code += encode_branch(at + len(code), target, link=False)
    code += h(NOP) * ((20 - len(code)) // 2)
    require(len(code) == 20, "stub layout")
    return code + struct.pack("<I", STAMP_BASE + 8 * slot)


# ---------------------------------------------------------------- safety checks
def check_layout(u: bytes) -> dict:
    image_copy_end = word(u, IMAGE_COPY_END_LIT) - LINK
    bss = [word(u, at) - LINK for at in BSS_LITS]
    rel = [word(u, at) - LINK for at in REL_DYN_LITS]
    require(word(u, 0x13C4) == LINK, "relocate_code __image_copy_start literal")
    require(rel[0] == image_copy_end and bss[0] == rel[0], "unexpected section layout")
    tail = len(u)
    while u[tail - 1] == 0xFF:
        tail -= 1
    require(tail == rel[1] == REL_DYN_END, "0xFF padding does not start at __rel_dyn_end")
    require(bss[0] <= tail and ITEM_SIZE <= bss[1], "padding is expected to lie inside .bss")
    # Every absolute address in the image has an R_ARM_RELATIVE entry in .rel.dyn.
    pointers: dict[int, list[int]] = {}
    for at in range(rel[0], rel[1], 8):
        location, kind = struct.unpack_from("<II", u, at)
        require(kind == 0x17 and LINK <= location < LINK + image_copy_end, "unexpected relocation")
        pointers.setdefault(word(u, location - LINK) - LINK, []).append(location - LINK)
    end = HELP_TEXT + HELP_LEN
    require(u[HELP_TEXT - 1] == 0 and u[end] == 0 and u.index(b"\0", HELP_TEXT) == end, "help text bounds")
    require(word(u, HELP_FIELD) - LINK == HELP_TEXT and
            u[word(u, COMMAND_ENTRY) - LINK:].startswith(b"sunxi_nand_test\0"), "not the sunxi_nand_test entry")
    inside = {p: s for p, s in pointers.items() if HELP_TEXT <= p < end}
    require(inside == {HELP_TEXT: [HELP_FIELD]}, f"other references into the help text: {inside}")
    # PC-relative loads/adr (Thumb 16/32-bit, ARM) into the help text: over-approximate scan.
    hits = []
    for at in range(0, image_copy_end - 4, 2):
        a, b = struct.unpack_from("<HH", u, at)
        targets = []
        if a & 0xF800 in (0x4800, 0xA000):
            targets.append(((at + 4) & ~3) + (a & 0xFF) * 4)
        if a & 0xFF7F == 0xF85F:
            targets.append(((at + 4) & ~3) + ((b & 0xFFF) if a & 0x80 else -(b & 0xFFF)))
        if a & 0xFBFF in (0xF20F, 0xF2AF):
            imm = ((a >> 10) & 1) << 11 | ((b >> 12) & 7) << 8 | (b & 0xFF)
            targets.append(((at + 4) & ~3) + (-imm if a & 0xA0 == 0xA0 else imm))
        if at % 4 == 0:
            w = word(u, at)
            if w & 0x0F7F0000 == 0x051F0000:
                targets.append(at + 8 + ((w & 0xFFF) if w & 0x00800000 else -(w & 0xFFF)))
        hits += [(at, t) for t in targets if HELP_TEXT <= t < end]
    require(not hits, f"pc-relative references into the help text: {hits}")
    # Nothing branches into the padding or the help text.
    branches = [at for at in range(0, image_copy_end - 2, 2)
                if (t := harness.branch_target(u, at)) is not None and (HELP_TEXT <= t < end or tail <= t < ITEM_SIZE)]
    require(not branches, f"branches into the stub area or padding: {branches}")
    padding_refs = sorted({p for p in pointers if tail <= p < ITEM_SIZE})
    return dict(image_copy_end=image_copy_end, bss=[hex(b) for b in bss], rel_dyn=[hex(r) for r in rel],
                padding=tail, relocations=str(sum(map(len, pointers.values()))),
                padding_pointers=[f"{p:#x} (literal at {pointers[p][0]:#x})" for p in padding_refs],
        help_pointer_sites=[hex(s) for s in inside[HELP_TEXT]])


def build() -> dict:
    package = SOURCE.read_bytes()
    require(kg.sha(package) == SOURCE_SHA256, "unexpected source package")
    require(word(package, 36) == len(package) == 0x140000 and kg.checksum(package, 20) == word(package, 20),
            "source TOC1 checksum/length")
    require(word(package, 64 + 64) == ITEM and word(package, 64 + 68) == ITEM_SIZE, "U-Boot item geometry")
    original = package[ITEM:ITEM + ITEM_SIZE]
    require(word(original, 20) == ITEM_SIZE and kg.checksum(original, 12) == word(original, 12),
            "source U-Boot checksum/length")
    layout = check_layout(original)
    u = bytearray(original)
    u[HELP_TEXT] = 0
    at, stubs = STUB_AREA, []
    for slot, site, target, meaning in HOOKS:
        require(harness.branch_target(original, site) == target and original[site + 3] & 0xD0 == 0xD0,
                f"call site {site:#x} is not bl {target:#x}")
        code = stub(at, slot, target)
        u[at:at + len(code)] = code
        u[site:site + 4] = encode_branch(site, at, link=True)
        stubs.append(dict(slot=slot, stub=at, stub_size=len(code), call_site=site, original_target=target,
                          meaning=meaning, slot_address=STAMP_BASE + 8 * slot,
                          site_before=original[site:site + 4].hex(), site_after=u[site:site + 4].hex()))
        at += len(code)
    require(at <= HELP_TEXT + HELP_LEN, "stubs do not fit")
    struct.pack_into("<I", u, 12, kg.checksum(u, 12))
    patched = bytearray(package)
    patched[ITEM:ITEM + ITEM_SIZE] = u
    struct.pack_into("<I", patched, 20, kg.checksum(patched, 20))
    allowed = set(range(20, 24)) | {ITEM + i for i in range(12, 16)} | {ITEM + i for i in range(HELP_TEXT, at)}
    for hook in stubs:
        allowed |= {ITEM + hook["call_site"] + i for i in range(4)}
    diff = kg.changed(package, bytes(patched))
    require(diff <= allowed, f"bytes changed outside the patch: {sorted(diff - allowed)[:8]}")
    require(kg.checksum(patched, 20) == word(patched, 20) and kg.checksum(u, 12) == word(u, 12), "checksums")
    OUTPUT.write_bytes(bytes(patched))
    info = dict(
        source=str(SOURCE.relative_to(REPO)), output=OUTPUT.name,
        original_package_sha256=kg.sha(package), patched_package_sha256=kg.sha(bytes(patched)),
        original_uboot_sha256=kg.sha(original), patched_uboot_sha256=kg.sha(bytes(u)),
        package_size=len(patched), card_offset=16793600, uboot_item_offset=ITEM, link_address=hex(LINK),
        stamp_base=hex(STAMP_BASE), magic_address=hex(STAMP_BASE - 8), magic=hex(MAGIC),
        slot_layout="slot n at STAMP_BASE + 8n: CNTPCT low word, then high word (little-endian); 24 MHz",
        dev_mem_page="dd if=/dev/mem bs=4096 skip=262144 count=1 (physical 0x40000000); magic at +0xF8, slots at +0x100",
        stub_area=[hex(STUB_AREA), hex(at)], stub_area_was="sunxi_nand_test long help text (.rodata), first byte set to NUL",
        changed_bytes=len(diff), checksums=dict(uboot_item_word12=hex(word(u, 12)), package_word20=hex(word(patched, 20))),
        layout={k: (hex(v) if isinstance(v, int) else v) for k, v in layout.items()},
        stubs=[{k: (hex(v) if isinstance(v, int) and k not in ("slot", "stub_size") else v) for k, v in s.items()}
               for s in stubs])
    (HERE / "stamps.json").write_text(json.dumps(info, indent=2) + "\n")
    return info


if __name__ == "__main__":
    result = build()
    print(json.dumps({k: result[k] for k in ("patched_package_sha256", "stub_area", "changed_bytes", "layout")}, indent=2))
