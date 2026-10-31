#!/usr/bin/env python3
"""Show that two capsules run the same dispatcher machine code.

    dispatcher_diff.py BUILD_A/capsule BUILD_B/capsule

Every 32-bit word of the dispatcher and copy stub (0x20400 .. copy_end) must be
identical, except literal-pool words that a pc-relative LDR reads. Those are the
build constants (SPL FNV, cookie), and each one is listed. Needs capstone
(Linux container, PYTHONPATH=work/mainline/payload/linux-deps).
"""
import json
import struct
import sys
from pathlib import Path

import capstone


def symbols(elf):
    shoff = struct.unpack_from('<I', elf, 32)[0]
    entsz, count, _ = struct.unpack_from('<3H', elf, 46)
    sections = [struct.unpack_from('<10I', elf, shoff + i * entsz) for i in range(count)]
    out = {}
    for s in sections:
        if s[1] != 2:
            continue
        strtab = sections[s[6]]
        names = elf[strtab[4]:strtab[4] + strtab[5]]
        for at in range(s[4], s[4] + s[5], s[9]):
            name, value = struct.unpack_from('<II', elf, at)
            if name:
                out[names[name:].split(b'\0')[0].decode()] = value
    return out


def literal_words(blob, start, end):
    """Addresses that a pc-relative LDR in [start, end) loads."""
    md = capstone.Cs(capstone.CS_ARCH_ARM, capstone.CS_MODE_ARM)
    md.detail = True
    pools = {}
    for at in range(start, end, 4):
        ins = next(md.disasm(blob[at - 0x20000:at - 0x20000 + 4], at), None)
        if ins and ins.mnemonic == 'ldr' and '[pc' in ins.op_str:
            mem = ins.operands[1].mem
            pools[at + 8 + mem.disp] = ins.address
    return pools, md


def main(a_dir, b_dir):
    a, b = (Path(d) for d in (a_dir, b_dir))
    blob = {d: (d / 'capsule.egon.bin').read_bytes() for d in (a, b)}
    syms = {d: symbols((d / 'capsule.elf').read_bytes()) for d in (a, b)}
    keys = ('entry', 'capsule_main', 'copy_start', 'copy_end', 'refuse', 'saved_context')
    assert all(syms[a][k] == syms[b][k] for k in keys), 'dispatcher layout differs'
    start, end = 0x20400, syms[a]['copy_end']
    pools_a, md = literal_words(blob[a], start, end)
    pools_b, _ = literal_words(blob[b], start, end)
    assert pools_a == pools_b, 'literal pool layout differs'
    diffs, code_diffs = [], []
    for at in range(start, end, 4):
        wa, wb = (struct.unpack_from('<I', blob[d], at - 0x20000)[0] for d in (a, b))
        if wa == wb:
            continue
        if at in pools_a:
            user = pools_a[at]
            ins = next(md.disasm(blob[a][user - 0x20000:user - 0x20000 + 4], user))
            diffs.append(dict(address=hex(at), a=hex(wa), b=hex(wb), loaded_by=f'{hex(user)}: {ins.mnemonic} {ins.op_str}'))
        else:
            code_diffs.append(hex(at))
    regions = {'vendor header 0x20000-0x20400 (except eGON checksum at +12)':
               blob[a][:12] + blob[a][16:0x400] == blob[b][:12] + blob[b][16:0x400],
               'stock boot0 0x21000-0x31000': blob[a][0x1000:0x11000] == blob[b][0x1000:0x11000],
               'SPL 0x31000-0x3d000 (expected to differ)': blob[a][0x11000:0x1d000] == blob[b][0x11000:0x1d000],
               'zero tail 0x3d000-0x40000': blob[a][0x1d000:] == blob[b][0x1d000:] == bytes(0x3000)}
    report = dict(dispatcher_range=[hex(start), hex(end)], words=(end - start) // 4,
                  instruction_words_differing=code_diffs, literal_words_differing=diffs,
                  regions_identical=regions,
                  manifests={str(d): json.loads((d / 'manifest.json').read_text()) for d in (a, b)})
    print(json.dumps(report, indent=2))
    assert not code_diffs, 'dispatcher instructions differ'
    assert regions['stock boot0 0x21000-0x31000'] and regions['zero tail 0x3d000-0x40000']
    print('PASS: dispatcher instructions identical; only literal-pool constants differ')


if __name__ == '__main__':
    main(*sys.argv[1:3])
