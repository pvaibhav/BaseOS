#!/usr/bin/env python3
"""v3: sixteen stamps, ten of them on U-Boot's init_sequence_r table entries (pre-logo).

Table entries are function pointers; each chosen entry is redirected to a stub that
stamps and tail-branches to the original routine. The six BL hooks of v2 stay. The
first stub (table entry 0, board_init_r start) writes the magic and clears every
slot; the last (boot_jump_linux) copies the whole block (8 + 16*8 = 136 bytes) to
the tail of the device tree at ft_addr + ft_len - 192.
"""
from __future__ import annotations

import importlib.util
import json
import struct
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("v1", HERE / "build-stamped.py")
v1 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(v1)
harness, kg, require, word = v1.harness, v1.kg, v1.require, v1.word
h, ldr_lit, str_imm, encode_branch = v1.h, v1.ldr_lit, v1.str_imm, v1.encode_branch
LINK, ITEM, ITEM_SIZE = v1.LINK, v1.ITEM, v1.ITEM_SIZE
SCRATCH, MAGIC, STAMP_BASE = v1.STAMP_BASE - 8, v1.MAGIC, v1.STAMP_BASE
TABLE = 0x9EDFC                 # init_sequence_r: 33 Thumb pointers, then 0xffffffff
BLOCK_WORDS = 2 + 16 * 2        # magic, pad, 16 slots
TAIL = 192

# (slot, kind, where, expected original target, meaning)
HOOKS = [
    (0, "table", 0, 0xF11C, "init_sequence_r[0]: board_init_r sequence starts (magic, clears slots)"),
    (1, "table", 10, 0x25D8, "init_sequence_r[10]: power/PMIC init starts"),
    (2, "table", 11, 0x12A30, "init_sequence_r[11]: after power init"),
    (3, "table", 14, 0x2A04, "init_sequence_r[14]: display driver init starts"),
    (4, "table", 21, 0x2738, "init_sequence_r[21]: after display init and stdio"),
    (5, "table", 24, 0xF26C, "init_sequence_r[24]: flash hook / burn key"),
    (6, "table", 25, 0xF1BC, "init_sequence_r[25]: initr_sunxi_flash (card init, IR, key recovery)"),
    (7, "table", 26, 0xF174, "init_sequence_r[26]: after flash init"),
    (8, "table", 27, 0x2A8C, "init_sequence_r[27]: boot0 update check"),
    (9, "table", 30, 0x2B14, "init_sequence_r[30]: board_late_init starts (logo next)"),
    (10, "bl", 0x2B22, 0x2AA8, "logo routine returned"),
    (11, "bl", 0xF13E, 0xEA90, "main_loop entered"),
    (12, "bl", 0xC1FC, 0x0FE0, "card read of the boot image starts"),
    (13, "bl", 0x7E80, 0xE8F8, "card read done, bootm starts"),
    (14, "bl", 0x152A, 0x13FC, "kernel unpacked, boot_prep_linux"),
    (15, "bl", 0x1482, 0xE8F8, "boot_jump_linux: about to jump; copies the block into the tree"),
]


def ldr_imm(rt: int, rn: int, imm: int) -> int:
    require(imm % 4 == 0 and imm < 128, "ldr offset")
    return 0x6800 | (imm // 4) << 6 | rn << 3 | rt


def stores(rt: int, rn: int, start: int, end: int) -> bytes:
    """str rt,[rn,#k] for k in start..end step 4, bumping rn by 64 when the offset exceeds 124."""
    out, base = b"", 0
    for k in range(start, end, 4):
        while k - base > 124:
            out += h(0x3040 | rn << 8)          # adds rn, #64
            base += 64
        out += h(str_imm(rt, rn, k - base))
    return out, base


def stub_first(at: int, target: int) -> bytes:
    lits = at + 96
    code = h(v1.PUSH_R0_R3) + v1.MRRC_CNTPCT_R0_R1
    code += h(ldr_lit(2, at + len(code), lits), ldr_lit(3, at + len(code) + 2, lits + 4), str_imm(3, 2, 0))
    code += h(str_imm(0, 2, 8), str_imm(1, 2, 12)) + v1.MOVW_R3_0
    body, _ = stores(3, 2, 16, 8 + 16 * 8)
    code += body + h(v1.POP_R0_R3)
    code += encode_branch(at + len(code), target, link=False)
    require(len(code) <= 96, f"first stub too long: {len(code)}")
    code += h(v1.NOP) * ((96 - len(code)) // 2)
    return code + struct.pack("<II", SCRATCH, MAGIC)


def stub_last(at: int, target: int) -> bytes:
    size = 16 + BLOCK_WORDS * 4 + 4 + 8           # rough; fixed below
    code = h(v1.PUSH_R0_R3) + v1.MRRC_CNTPCT_R0_R1
    lits_at = None
    code += h(0, 0, 0)                            # placeholder: ldr r2,=slot15; str r0; str r1 (patched after)
    code += h(ldr_imm(2, 5, 0x6C), ldr_imm(1, 5, 0x70), 0x1852)    # r2 = ft_addr + ft_len
    code += h(0x3AC0)                                              # subs r2, #192
    code += h(0)                                                   # placeholder: ldr r0,=SCRATCH
    body = b""
    base = 0
    for k in range(0, BLOCK_WORDS * 4, 4):
        while k - base > 124:
            body += h(0x3040 | 0 << 8, 0x3040 | 2 << 8)            # adds r0,#64 ; adds r2,#64
            base += 64
        body += h(ldr_imm(1, 0, k - base), str_imm(1, 2, k - base))
    code += body + h(v1.POP_R0_R3)
    code += encode_branch(at + len(code), target, link=False)
    length = (len(code) + 3) & ~3
    code += h(v1.NOP) * ((length - len(code)) // 2)
    lits = at + length
    patched = bytearray(code)
    struct.pack_into("<HHH", patched, 6, ldr_lit(2, at + 6, lits), str_imm(0, 2, 0), str_imm(1, 2, 4))
    struct.pack_into("<H", patched, 20, ldr_lit(0, at + 20, lits + 4))
    return bytes(patched) + struct.pack("<II", STAMP_BASE + 8 * 15, SCRATCH)


def build() -> dict:
    package = v1.SOURCE.read_bytes()
    require(kg.sha(package) == v1.SOURCE_SHA256, "unexpected source package")
    original = package[ITEM:ITEM + ITEM_SIZE]
    v1.check_layout(original)
    u = bytearray(original)
    u[v1.HELP_TEXT] = 0
    at, stubs = v1.STUB_AREA, []
    for slot, kind, where, target, meaning in HOOKS:
        if kind == "table":
            site = TABLE + 4 * where
            require(word(original, site) == (LINK + target) | 1, f"table entry {where} is not {target:#x}")
        else:
            site = where
            require(harness.branch_target(original, site) == target and original[site + 3] & 0xD0 == 0xD0,
                    f"call site {site:#x} is not bl {target:#x}")
        if slot == 0:
            code = stub_first(at, target)
        elif slot == 15:
            code = stub_last(at, target)
        else:
            code = v1.stub(at, slot, target)
        u[at:at + len(code)] = code
        if kind == "table":
            struct.pack_into("<I", u, site, (LINK + at) | 1)
        else:
            u[site:site + 4] = encode_branch(site, at, link=True)
        stubs.append(dict(slot=slot, kind=kind, stub=hex(at), stub_size=len(code), site=hex(site),
                          original_target=hex(target), meaning=meaning, slot_address=hex(STAMP_BASE + 8 * slot)))
        at += len(code)
    require(at <= v1.HELP_TEXT + v1.HELP_LEN, "stubs do not fit")
    struct.pack_into("<I", u, 12, kg.checksum(u, 12))
    patched = bytearray(package)
    patched[ITEM:ITEM + ITEM_SIZE] = u
    struct.pack_into("<I", patched, 20, kg.checksum(patched, 20))
    allowed = set(range(20, 24)) | {ITEM + i for i in range(12, 16)} | {ITEM + i for i in range(v1.HELP_TEXT, at)}
    for s in stubs:
        allowed |= {ITEM + int(s["site"], 16) + i for i in range(4)}
    diff = kg.changed(package, bytes(patched))
    require(diff <= allowed, f"bytes changed outside the patch: {sorted(diff - allowed)[:8]}")
    out = HERE / "boot-package-stamped-v3.bin"
    out.write_bytes(bytes(patched))
    info = dict(output=out.name, original_package_sha256=kg.sha(package), patched_package_sha256=kg.sha(bytes(patched)),
                changed_bytes=len(diff), stub_area=[hex(v1.STUB_AREA), hex(at)], block_words=BLOCK_WORDS, tail=TAIL,
                stubs=stubs)
    return info, bytes(u)


def verify(u: bytes, info: dict) -> dict:
    from unicorn.arm_const import UC_ARM_REG_R5
    prefix = v1.REPO / "work/rg34xxsp/boot-prefix.img"
    environment = harness.stored_environment(prefix)
    derived = kg.derive("rg34xxsp", prefix)
    tree = harness.Target("rg34xxsp", derived, environment).tree
    # 1. every table stub: direct call, stamps and passes through
    for s in info["stubs"]:
        if s["kind"] != "table":
            continue
        m = harness.Machine(u, tree, {}, 0)
        reached = []
        m.replace(int(s["original_target"], 16), lambda: reached.append(1) or 0)
        m.uc.mem_write(SCRATCH, b"\xEE" * (BLOCK_WORDS * 4))
        if s["slot"] == 0 and "entry_stub" in info:
            # Later instruments retain the true entry stamp in slot zero.
            m.uc.emu_start(LINK + int(info["entry_stub"], 16), LINK + 0x640)
        result = m.call(int(s["stub"], 16), [])
        lo, hi = struct.unpack("<II", m.read(int(s["slot_address"], 16), 8))
        require(reached == [1] and result == 0 and (lo | hi) != 0 and lo != 0xEEEEEEEE,
                f"table stub {s['slot']} failed: reached {reached} result {result} lo {lo:#x}")
        if s["slot"] == 0:
            words = struct.unpack(f"<{BLOCK_WORDS}I", m.read(SCRATCH, BLOCK_WORDS * 4))
            require(words[0] == MAGIC and all(w == 0 for w in words[4:]), "first stub did not clear the slots")
    # 2. the BL stubs along the harness flow, stopping after the FDT copy
    harness.BOOT_JUMP_LINUX = 0x14C2
    plain = harness.boot(harness.Target("rg34xxsp", derived, environment), derived["boot"])
    patched = harness.boot(harness.Target("rg34xxsp", dict(derived, uboot=u), environment), derived["boot"])
    mp, mu = plain["machine"], patched["machine"]
    ft_addr, ft_len = mu.get32(mu.images + 0x6C), mu.get32(mu.images + 0x70)
    require((ft_addr, ft_len) == (mp.get32(mp.images + 0x6C), mp.get32(mp.images + 0x70)), "ft fields differ")
    require(mp.read(ft_addr, ft_len - TAIL) == mu.read(ft_addr, ft_len - TAIL), "tree differs before the block")
    blob = mu.read(ft_addr + ft_len - TAIL, BLOCK_WORDS * 4)
    words = struct.unpack(f"<{BLOCK_WORDS}I", blob)
    stamps = [words[2 + 2 * i] | words[3 + 2 * i] << 32 for i in range(16)]
    require(all(s == 0 for s in stamps[:12]) and 0 < stamps[12] < stamps[13] < stamps[14] < stamps[15],
            f"BL stamps not in order: {stamps}")
    t = mu.read(ft_addr, ft_len)
    used = max(struct.unpack_from(">I", t, 8)[0] + struct.unpack_from(">I", t, 36)[0],
               struct.unpack_from(">I", t, 12)[0] + struct.unpack_from(">I", t, 32)[0])
    require(used <= ft_len - TAIL and ft_len <= harness.fdt.total_size(t), f"no room: blocks end {used}, tail {ft_len - TAIL}")
    return dict(ft_len=ft_len, blocks_end=used, tail_offset=ft_len - TAIL, bl_stamps=stamps[12:])


if __name__ == "__main__":
    info, u = build()
    info["verification"] = verify(u, info)
    (HERE / "stamps-v3.json").write_text(json.dumps(info, indent=2) + "\n")
    print(json.dumps({k: info[k] for k in ("patched_package_sha256", "changed_bytes", "stub_area", "verification")}, indent=1))
