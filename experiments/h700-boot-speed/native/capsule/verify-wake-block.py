#!/usr/bin/env python3
"""Which DRAM block the compiled capsule hands stock boot0 on a wake; host only."""
from pathlib import Path
import argparse, importlib.util, json, struct, sys

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('capsule_model', HERE / 'verify.py')
prove = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prove)

# CubeXX LPDDR4 block from issue #31 (bank 15 with unit geometry and delays).
CUBE_LOG = bytes.fromhex(
    'a002000008000000080808080e0e0e0e0e0e0000bbbb8778fa30000000000004'
    '00000000340000001b0000003300000003000000000000000000000004000000'
    '7200000000000000090000000000000000000000240000000000000000000000'
    '01000000000000008080804033662f402024252207070707608c000200000000'
)
AUTOSCANNED = {6: 0x30fa, 7: 0x04001000, 30: 0x6041}
words = lambda b: list(struct.unpack('<32I', b))
pack = lambda w: struct.pack('<32I', *w)


def renewed(bank, salt):
    w = words(bank)
    for i in range(32):
        if i != 1:
            w[i] = (w[i] ^ (0x01010101 * (i + 1) + salt)) & 0xffffffff or 1
    return pack(w)


def with_words(block, values):
    w = words(block)
    for i, v in values.items():
        w[i] = v
    return pack(w)


def changed(block, index, value):
    return with_words(block, {index: value})


def run(name, outer=None, media=0, route='resume', extra=None, **kw):
    def mutation(m, syms):
        if outer is not None:
            m.mem_write(0x20038, outer)
        if extra:
            extra(m, syms)
    row, m, *_ = prove.run('protocol', name, route, seed=90, media=media, mutation=mutation, **kw)
    if row['outcome'] == 'inner-entry':
        row['output'] = bytes(m.mem_read(0x20000, 0x10000 if row['selected'] == 'stock' else 0xc000))
    return row


def mmio(row):
    return [(e['kind'], e['address'], e['value']) for e in row['events'] if e['kind'] in ('read', 'write')]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    parser.add_argument('--target')
    args = parser.parse_args()
    prove.DIRECTORY = args.directory.resolve()
    prove.inputs = {n: (prove.DIRECTORY / f'{n}.bin').read_bytes() for n in ('stock', 'native')}
    stock = prove.inputs['stock']
    first = stock[0x38:0xb8]
    mode = stock[0x350]
    banks = {k: stock[0x374 + (k - 1) * 0x80:0x3f4 + (k - 1) * 0x80] for k in range(1, 16)}
    results, failed = [], []

    def check(name, ok, row=None, **facts):
        entry = dict(name=name, passed=bool(ok), **facts)
        if row:
            entry.update(outcome=row['outcome'], selected=row.get('selected'),
                         wake_block=row.get('wake_block'), write_ranges=row['write_ranges'])
        results.append(entry)
        if not ok:
            failed.append(name)
            print('FAIL ' + name, flush=True)

    def attempt(name, expect, outer, expected_block, **kw):
        try:
            row = run(name, outer, **kw)
        except AssertionError as error:
            return check(name, False, error=str(error))
        out = row.get('output', b'')
        block = out[0x38:0xb8]
        others = all(out[i] == stock[i] for i in range(0x10000) if not 0x38 <= i < 0xb8 and i != 0x28)
        wrote = row['write_ranges'].get('wake-dram-block')
        ok = (row['outcome'] == 'inner-entry' and row['selected'] == 'stock' and row['wake_block'] == expect
              and block == expected_block and others and out[0x28] == kw.get('media', 0)
              and wrote == (['0x21038', '0x210b8'] if expect == 'outer' else None)
              and mmio(row) == [('read', hex(prove.RET), '0x10000')])
        check(name, ok, row, handed_over_type=struct.unpack_from('<I', block, 4)[0] if block else None)
        return row

    types = {words(b)[1] for b in [first, *banks.values()]}
    unit = renewed(first, 0)
    attempt('fresh install: outer equals built-in', 'outer', first, first)
    attempt('renewed first bank', 'outer', unit, unit)
    attempt('resume with nonzero pair still hands over outer', 'outer', unit, unit, pair=(1, 0))
    for media in (1, 0x12, 0xff):
        attempt(f'renewed first bank media {media:#x}', 'outer', unit, unit, media=media)
    seen = set()
    for k, bank in banks.items():
        if bank in seen:
            continue
        seen.add(bank)
        block = renewed(bank, k)
        attempt(f'renewed bank {k} (type {words(bank)[1]})', 'outer', block, block)
    autoscanned = with_words(banks[1], AUTOSCANNED)
    attempt('DDR3 bank 1 autoscanned', 'outer', autoscanned, autoscanned)
    cube = attempt('CubeXX issue 31 block', 'outer', CUBE_LOG, CUBE_LOG)
    check('issue 31 block handed over as LPDDR4', cube and cube['output'][0x3c] == 8)
    attempt('zero clock rejected', 'built-in', changed(unit, 0, 0), first)
    for t in sorted({0, 1, 2, 4, 5, 6, 7, 8, 0xffffffff} - types):
        attempt(f'unknown memory type {t:#x} rejected', 'built-in', changed(unit, 1, t), first)
    attempt('all-zero block rejected', 'built-in', bytes(128), first)
    attempt('all-ones block rejected', 'built-in', b'\xff' * 128, first)

    def neighbours(m, syms):
        m.mem_write(0x20034, b'\x5a\xa5\x5a\xa5')
        m.mem_write(0x200b8, b'\x5a\xa5\x5a\xa5')
    attempt('neighbour bytes untouched', 'outer', unit, unit, extra=neighbours)

    for name, at in (('stock body', 0x22000), ('stock bank table', 0x21374), ('stock first bank', 0x21040)):
        def damage(m, syms, at=at):
            m.mem_write(at, bytes([m.mem_read(at, 1)[0] ^ 1]))
        row = run('guard ' + name, unit, extra=damage)
        check('damaged ' + name + ' refuses before write',
              row['outcome'] == 'refused' and 'wake-dram-block' not in row['write_ranges'] and not row['mmio_writes'], row)

    row = run('retention bit 17 alone takes normal route', unit, route='native', retention=0x20000)
    check(row['name'], row['selected'] == 'native' and 'wake-dram-block' not in row['write_ranges'], row)
    for label, outer in (('renewed', unit), ('foreign', CUBE_LOG), ('zero', bytes(128))):
        row = run('cold native ignores outer ' + label, outer, route='native')
        check(row['name'], row['selected'] == 'native' and 'wake-dram-block' not in row['write_ranges'], row)
        row = run('cold stock ignores outer ' + label, outer, route='native', pair=(1, 0))
        check(row['name'], row['selected'] == 'stock' and row['output'][0x38:0xb8] == first and 'wake-dram-block' not in row['write_ranges'], row)

    report = dict(passed=not failed, failed=failed, target=args.target, bank_mode=mode, cases=len(results))
    (args.directory / 'wake-verification.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report))
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
