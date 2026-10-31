#!/usr/bin/env python3
"""Run BL31's own devicetree check (sunxi_baseos_validate_dtb) on three trees.

    bl31_dtb.py BL31_ELF DTB_DIR

DTB_DIR holds payload.dtb, pmic.dtb (adds /pmic@34 "x-powers,axp717") and
unterminated.dtb (a compatible without its NUL), written by tests/run.py.
Prints, per tree, whether the compiled function returned or reached a panic
entry (el3_panic, plat_panic_handler, __assert).
Its log calls (tf_log) are skipped. Linux container only (Unicorn).
"""
import json
import struct
import sys
from pathlib import Path

import unicorn as u
from unicorn import arm64_const as a


def symbols(b):
    shoff = struct.unpack_from('<Q', b, 40)[0]
    ents, num = struct.unpack_from('<HH', b, 58)
    sections = [struct.unpack_from('<IIQQQQIIQQ', b, shoff + i * ents) for i in range(num)]
    out = {}
    for s in sections:
        if s[1] != 2:
            continue
        st = sections[s[6]]
        names = b[st[4]:st[4] + st[5]]
        for at in range(s[4], s[4] + s[5], s[9]):
            n, _, _, _, v, _ = struct.unpack_from('<IBBHQQ', b, at)
            if n:
                out[names[n:names.find(b'\0', n)].decode()] = v
    return out


def load_segments(m, elf):
    phoff = struct.unpack_from('<Q', elf, 32)[0]
    stride, count = struct.unpack_from('<HH', elf, 54)
    for at in range(phoff, phoff + stride * count, stride):
        kind, _, offset, _, pa, filesz, _, _ = struct.unpack_from('<II6Q', elf, at)
        if kind == 1:
            m.mem_write(pa, elf[offset:offset + filesz])


def check(elf, dtb):
    syms = symbols(elf)
    m = u.Uc(u.UC_ARCH_ARM64, u.UC_MODE_ARM)
    m.mem_map(0x40000000, 0x100000)
    m.mem_map(0x4a200000, 0x100000)
    m.mem_map(0x4b000000, 0x10000)  # stack
    load_segments(m, elf)
    m.mem_write(0x4a200000, dtb)
    done, stop = 0x4b00f000, {}

    def code(mc, pc, size, ctx):
        if pc in panics:
            stop['result'] = 'panic'
            mc.emu_stop()
        elif pc == syms.get('tf_log'):
            mc.reg_write(a.UC_ARM64_REG_PC, mc.reg_read(a.UC_ARM64_REG_X30))
        elif pc == done:
            stop['result'] = 'returned'
            mc.emu_stop()

    panics = {syms[n] for n in ('el3_panic', 'plat_panic_handler', '__assert') if n in syms}
    assert panics
    for name in ('el3_panic', 'plat_panic_handler', '__assert', 'tf_log'):
        if name in syms:
            m.hook_add(u.UC_HOOK_CODE, code, begin=syms[name], end=syms[name])
    m.hook_add(u.UC_HOOK_CODE, code, begin=done, end=done)
    m.reg_write(a.UC_ARM64_REG_SP, 0x4b00e000)
    m.reg_write(a.UC_ARM64_REG_X0, 0x4a200000)
    m.reg_write(a.UC_ARM64_REG_X1, 0x100000)
    m.reg_write(a.UC_ARM64_REG_X30, done)
    m.emu_start(syms['sunxi_baseos_validate_dtb'], 0, count=50000000)
    return stop.get('result', 'did not finish')


def main(elf_path, dtb_dir):
    elf = Path(elf_path).read_bytes()
    report = {name: check(elf, (Path(dtb_dir) / f'{name}.dtb').read_bytes())
              for name in ('payload', 'pmic', 'unterminated')}
    print(json.dumps({'bl31': elf_path, **report}))


if __name__ == '__main__':
    main(*sys.argv[1:3])
