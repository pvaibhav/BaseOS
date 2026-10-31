#!/usr/bin/env python3
"""Run the built SPL card-read stage and then the helper, up to the BL31 entry.

    chain_replay.py VARIANT PAYLOAD [--card BOOT_PREFIX_IMG] [--mbps 23.5]

Runs inside the pinned Linux container (Unicorn; never on macOS) with
PYTHONPATH=work/mainline/payload/linux-deps and the repo at /repo.

What runs is the compiled code: the SPL's baseos_vendor_handoff (logo, firmware
and payload reads, panel steps, records, adoption check) and then, after its
branch to 0x4a000000, the helper copied there from firmware.bin (records,
display, kernel unpack, FDT, handoff). Modelled, as in the older harnesses
(experiments/h700-mainline/verify-spl.py and verify-u2-helper.py): DRAM training,
PMIC, SPL cache setup, EL3 system registers, PLL lock bits, the TCON scanline
counter and the card (mmc_bread returns firmware.bin and payload bytes at their
sectors, and with --card the stock boot-resource partition with BOOTLOGO.BMP;
without it there is no logo and the helper lights a black frame). Time is the
CNTPCT value the code reads: 1 us per read, plus the card transfer time per
mmc_bread, so every wait the code makes is measured in the same clock.

Checks: the GPIO trace (PI8-PI10, PI14, PI15) decodes to the vendor power and
reset steps with their minimum waits, then exactly the generated init stream,
every vendor delay honoured; the TCON is enabled at least 50 ms after the last
stream byte and the backlight at least 100 ms after that; no RTC writes and only
the SPL's single watchdog reload; the helper reaches BL31 with x0 = 0x4a300400.
--tamper stream|fnv edits the SPL's panel record just before the helper runs
(an impossible stream position with a correct checksum, or a wrong checksum).
The helper must then reset the panel itself and send the whole stream again.
Prints a JSON report and writes it to work/v2/firmware/VARIANT/chain-<payload>[-tamper].json.
"""
import argparse
import importlib.util
import json
import re
import struct
import sys
from pathlib import Path

import capstone
import unicorn as u
from unicorn import arm64_const as a

REPO = Path('/repo')
spec = importlib.util.spec_from_file_location('system', REPO / 'experiments/h700-boot-speed/spl/arm64-system.py')
system = importlib.util.module_from_spec(spec)
spec.loader.exec_module(system)

TICKS_PER_MS = 24000
PI_CFG1, PI_DATA = 0x0300b124, 0x0300b130
CS, CLK, MOSI, RESET, POWER = 1 << 8, 1 << 9, 1 << 10, 1 << 14, 1 << 15


def fnv(data):
    h = 2166136261
    for v in data:
        h = (h ^ v) * 16777619 & 0xffffffff
    return h


def elf_symbols(path):
    b = path.read_bytes()
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


def special_instructions(code, base, start, end):
    md = capstone.Cs(capstone.CS_ARCH_ARM64, capstone.CS_MODE_ARM)
    found = {}
    for off in range(start - base, end - base, 4):
        ins = next(md.disasm(code[off:off + 4], base + off), None)
        if ins and ins.mnemonic in ('mrs', 'msr', 'dc', 'ic', 'tlbi', 'wfi', 'at'):
            found[ins.address] = ins
    return found


def stream_from(header_path, name='old'):
    text = header_path.read_text()
    body = re.search(r'baseos_panel_%s\[\] = \{([^}]*)\}' % name, text)[1]
    return [int(x, 16) for x in body.split(',')]


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('variant')
    p.add_argument('payload', type=Path)
    p.add_argument('--card', type=Path, help='card image whose boot-resource partition holds the logo')
    p.add_argument('--mbps', type=float, default=23.5, help='card read rate, MB/s')
    p.add_argument('--fw-root', type=Path, default=REPO / 'work/v2/firmware')
    p.add_argument('--tamper', choices=('stream', 'fnv'))
    args = p.parse_args()
    out_dir, build = args.fw_root / args.variant, args.fw_root / 'build' / args.variant
    manifest = json.loads((out_dir / 'manifest.json').read_text())
    generation = bytes.fromhex(manifest['generation'])
    cookie = int(manifest['cookie'], 16)
    spl_blob = (out_dir / 'spl.bin').read_bytes()
    firmware = (out_dir / 'firmware.bin').read_bytes()
    payload = args.payload.read_bytes()
    card = args.card.read_bytes() if args.card else b''
    assert payload[32:64] == generation, 'payload generation differs: restamp it first'
    helper = (out_dir / 'helper.bin').read_bytes()
    hsym = elf_symbols(build / 'helper/helper.elf')
    ssym = {n: int(v, 16) for v, n in re.findall(r'^([0-9a-f]+) [a-zA-Z] (\w+)$',
                                               (build / 'spl/symbols.txt').read_text(), re.M)}
    stack_top = int(re.search(r'^CONFIG_SPL_STACK=(0x[0-9a-f]+)$',
                              (build / 'spl/u-boot-2026.10/.config').read_text(), re.M)[1], 16)
    stream = stream_from(build / 'helper/early-display-generated.h')
    ops_text = (build / 'helper/early-display-operations.h').read_text()
    polls = [tuple(int(x, 16) for x in m.groups()) for m in
             re.finditer(r'\{DISPLAY_POLL,(0x[0-9a-f]+)u,(0x[0-9a-f]+)u,(0x[0-9a-f]+)u', ops_text)]
    p4 = manifest['p4_first_lba']
    fw_lba, image_lba, logo_lba = p4 + 40 * 2048, p4 + 48 * 2048, (204800, 270336)

    m = u.Uc(u.UC_ARCH_ARM64, u.UC_MODE_ARM)
    m.ctl_set_cpu_model(a.UC_CPU_ARM64_A53)
    m.mem_map(0x20000, 0x39000)
    m.mem_write(0x20000, spl_blob)
    m.mem_map(0x01000000, 0x01400000)
    m.mem_map(0x03000000, 0x08000000)
    m.mem_map(0x40000000, 0x40000000)
    m.reg_write(a.UC_ARM64_REG_PSTATE, 0x3cd)
    m.reg_write(a.UC_ARM64_REG_SP, stack_top)
    m.reg_write(a.UC_ARM64_REG_X18, 0x41000000)  # gd: ram_size 1 GiB
    m.mem_write(0x41000000, struct.pack('<Q', 0x40000000) * 512)
    result = 0x41002000
    m.mem_write(result + 46 * 4, struct.pack('<I', 16))
    params = [0] * 32
    params[1], params[7] = 8, 1024 << 16  # LPDDR4, 1024 MiB (checked by the helper)
    m.mem_write(0x41004000, struct.pack('<32I', *params))
    for idx, val in enumerate((0x41003000, result, 0)):
        m.reg_write(system.xreg(idx), val)
    m.mem_write(0x41003000 + 136, struct.pack('<Q', ssym['mmc_bread']))  # blk_desc.block_read
    m.reg_write(a.UC_ARM64_REG_X30, 0x58000)
    m.mem_write(ssym['dram_probe_selected_type'], struct.pack('<I', 8))
    m.mem_write(ssym['dram_probe_attempts'], struct.pack('<I', 1))
    for ad, val in ((0x07000138, cookie), (0x0700013c, 0x42535031), (0x030090b4, 1), (0x030090b8, 0xb1)):
        m.mem_write(ad, struct.pack('<I', val))

    regs = system.SystemRegisters(3)
    st = dict(clock=500 * TICKS_PER_MS,  # 0.5 s after power-on; any origin works
              sctlr=0, phase='spl', outcome=None, handoff=None,
              reads=[], trace=[], mmio=[], cache_ops=0)

    def ret(mc, v):
        mc.reg_write(a.UC_ARM64_REG_X0, v)
        mc.reg_write(a.UC_ARM64_REG_PC, mc.reg_read(a.UC_ARM64_REG_X30))

    spl_special = special_instructions(spl_blob, 0x20000, 0x20060, ssym['__image_copy_end'])
    helper_special = special_instructions(helper, 0x4a000000, 0x4a000000, 0x4a000000 + len(helper))
    mocked = {ssym[n] for n in ('sunxi_board_init', 'baseos_native_cache_on', 'baseos_native_cache_off')}

    def tick():
        st['clock'] += 24
        return st['clock']

    def code(mc, pc, size, ctx):
        if pc == 0x40000000:
            st['outcome'] = 'bl31'
            st['bl31'] = dict(clock=st['clock'], x=[mc.reg_read(system.xreg(i)) for i in range(4)])
            mc.emu_stop()
            return
        if pc == 0x4a000000 and st['phase'] == 'spl':
            st['phase'] = 'helper'
            st['handoff'] = dict(clock=st['clock'], x=[mc.reg_read(system.xreg(i)) for i in range(4)],
                                 daif_masked=mc.reg_read(a.UC_ARM64_REG_PSTATE) & 0x3c0 == 0x3c0,
                                 panel_record=bytes(mc.mem_read(0x4a300280, 120)).hex(),
                                 logo_record=bytes(mc.mem_read(0x4a300200, 16)).hex(),
                                 native_record=bytes(mc.mem_read(0x4a300000, 240)).hex())
            assert bytes(mc.mem_read(0x4a000000, len(helper))) == helper, 'helper copy differs'
            if args.tamper:
                record = bytearray(mc.mem_read(0x4a300280, 120))
                assert struct.unpack_from('<I', record, 4)[0] == 2, 'tamper needs a version 2 record'
                if args.tamper == 'stream':
                    struct.pack_into('<I', record, 96, 5)  # not just after a delay entry
                    record[12:16] = bytes(4)
                    struct.pack_into('<I', record, 12, fnv(record))
                else:
                    record[64] ^= 1  # generation byte; checksum now wrong
                mc.mem_write(0x4a300280, bytes(record))
            return
        if pc == 0x58000:
            st['outcome'] = 'spl-refused'
            mc.emu_stop()
            return
        if pc == hsym['helper_fault']:
            st['outcome'] = 'helper-fault'
            mc.emu_stop()
            return
        if pc in mocked:
            ret(mc, 0)
            return
        if pc == ssym['pmic_bus_read']:
            mc.mem_write(mc.reg_read(a.UC_ARM64_REG_X1), b'\0')
            ret(mc, 0)
            return
        if pc == ssym['pmic_bus_write']:
            raise AssertionError('unexpected PMIC write')
        if pc == ssym['baseos_selected_vendor_params']:
            ret(mc, 0x41004000)
            return
        if pc == ssym['mmc_bread']:
            start, count, dst = (mc.reg_read(system.xreg(i)) for i in (1, 2, 3))
            assert 0 < count <= 1024
            st['reads'].append((start, count, st['clock']))
            if logo_lba[0] <= start and start + count <= logo_lba[1]:
                if not card:
                    ret(mc, 0)  # no logo
                    return
                src, base = card, 0
            elif fw_lba <= start < fw_lba + len(firmware) // 512:
                src, base = firmware, fw_lba
            elif image_lba <= start < image_lba + 0x8000:
                src, base = payload, image_lba
            else:
                ret(mc, 0)
                return
            piece = src[(start - base) * 512:(start - base + count) * 512]
            mc.mem_write(dst, piece)
            st['clock'] += int(2400 + count * 512 * TICKS_PER_MS * 1000 / (args.mbps * 1e6))
            ret(mc, len(piece) // 512)
            return
        ins = spl_special.get(pc) if pc < 0x40000000 else helper_special.get(pc)
        if not ins:
            return
        mn, ops = ins.mnemonic, ins.op_str.split(', ')
        if mn == 'wfi':
            st['outcome'] = 'wfi-' + st['phase']
            mc.emu_stop()
            return
        if mn == 'mrs' and ops[1] in ('cntpct_el0', 'cntfrq_el0'):
            mc.reg_write(system.xreg(int(ops[0][1:])), tick() if ops[1] == 'cntpct_el0' else 24000000)
        elif st['phase'] == 'spl':
            if mn == 'msr' and ops[0] == 'daifset':
                mc.reg_write(a.UC_ARM64_REG_PSTATE, mc.reg_read(a.UC_ARM64_REG_PSTATE) | 0x3c0)
            elif mn == 'msr' and ops[0] == 'rmr_el3':
                raise AssertionError('SPL reached RMR')
            else:
                assert regs.execute(mc, ins), (hex(pc), mn, ins.op_str)
                return
        else:  # helper: the same model as verify-u2-helper.py
            if mn == 'mrs':
                value = {'ctr_el0': 4 << 16, 'clidr_el1': (2 << 24) | 3 | (4 << 3),
                         'ccsidr_el1': (1 << 13) | (3 << 3) | 2, 'sctlr_el3': st['sctlr']}.get(ops[1], 0)
                mc.reg_write(system.xreg(int(ops[0][1:])), value)
            elif mn == 'msr' and ops[0] == 'sctlr_el3':
                st['sctlr'] = mc.reg_read(system.xreg(int(ops[1][1:])))
            elif mn == 'dc':
                st['cache_ops'] += 1
        mc.reg_write(a.UC_ARM64_REG_PC, pc + 4)

    scan = [0]

    def read(mc, access, ad, size, value, ctx):
        old = int.from_bytes(mc.mem_read(ad, size), 'little')
        if ad == 0x065110fc:
            scan[0] += 1
            old = (scan[0] & 0xfff) << 16
        elif 0x03001000 <= ad < 0x03001100:
            old |= 0x90000000
        for addr, clear, setbits in polls:
            if addr == ad:
                old = (old & ~clear) | setbits
        mc.mem_write(ad, old.to_bytes(size, 'little'))

    def write(mc, access, ad, size, value, ctx):
        pc = mc.reg_read(a.UC_ARM64_REG_PC)
        if 0x07000000 <= ad < 0x07001000:
            raise AssertionError(('RTC write', hex(ad), hex(value), hex(pc)))
        if 0x03009000 <= ad < 0x0300a000:
            assert (ad, value, st['phase']) == (0x030090b0, 0x14af, 'spl'), ('watchdog write', hex(ad), hex(value))
        if 0x0300b000 <= ad < 0x0300b400:
            st['trace'].append((st['clock'], pc, ad, value))
        if ad in (0x06511040, 0x0300a040):
            st['mmio'].append((st['clock'], pc, ad, value))

    hooks = set(spl_special) | set(helper_special) | mocked | {0x58000, 0x4a000000, 0x40000000, hsym['helper_fault']}
    hooks |= {ssym[n] for n in ('pmic_bus_read', 'pmic_bus_write', 'baseos_selected_vendor_params', 'mmc_bread')}
    for pc in hooks:
        m.hook_add(u.UC_HOOK_CODE, code, begin=pc, end=pc)
    for lo, hi in ((0x01000000, 0x02400000), (0x03000000, 0x03100000), (0x06500000, 0x06520000)):
        m.hook_add(u.UC_HOOK_MEM_READ, read, begin=lo, end=hi - 1)
    m.hook_add(u.UC_HOOK_MEM_WRITE, write, begin=0x03000000, end=0x0affffff)
    try:
        m.emu_start(ssym['baseos_vendor_handoff'], 0, count=1500000000)
    except u.UcError as error:
        raise AssertionError((str(error), hex(m.reg_read(a.UC_ARM64_REG_PC)), st['phase'])) from error

    report = check(st, stream, hsym, ssym, generation, args, payload)
    (out_dir / f'chain-{args.payload.stem}{"-tamper-" + args.tamper if args.tamper else ""}.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))


def ms(ticks): return round(ticks / TICKS_PER_MS, 2)


def check(st, stream, hsym, ssym, generation, args, payload):
    assert st['outcome'] == 'bl31', st['outcome']
    assert st['handoff']['x'] == [0x4a300000, 0, 0, 0] and st['handoff']['daif_masked']
    assert st['bl31']['x'] == [0x4a300400, 0, 0, 0]
    native = bytes.fromhex(st['handoff']['native_record'])
    started, read_started, loaded = struct.unpack_from('<3Q', native, 24)
    assert native[200:232] == generation and struct.unpack_from('<II', native, 188) == (len(payload), fnv(payload))

    def owner(pc): return 'spl' if pc < 0x40000000 else 'helper'

    # Decode the PI data register trace: power/reset levels and 9-bit SPI frames.
    level, bits, frames, power, reset_edges, cfg = 0, [], [], None, [], 0
    frame_start = None
    for clock, pc, ad, value in st['trace']:
        if ad == PI_CFG1:
            cfg = value
            continue
        if ad != PI_DATA:
            continue
        if value & POWER and not level & POWER:
            power = (clock, owner(pc))
            assert cfg & 0xff000000 == 0x11000000, 'PI14/PI15 not outputs at power on'
        if (value ^ level) & RESET:
            reset_edges.append((clock, bool(value & RESET), owner(pc)))
        if not value & CS and frame_start is None and (value ^ level) & CLK and not value & CLK:
            frame_start = (clock, owner(pc))
            assert cfg & 0xfff == 0x111, 'PI8-PI10 not outputs at the first SPI clock'
        if not value & CS and value & CLK and not level & CLK:
            bits.append(1 if value & MOSI else 0)
        if value & CS and not level & CS and frame_start is not None:
            assert len(bits) == 9, ('frame bits', len(bits))
            frames.append(dict(value=int(''.join(map(str, bits)), 2), start=frame_start[0], end=clock,
                               by=frame_start[1]))
            bits, frame_start = [], None
        level = value
    expected = [op for op in stream if not op & 0x8000]
    assert power and [e[1] for e in reset_edges[:3]] == [True, False, True], reset_edges
    if args.tamper:
        # The SPL's part is a prefix; then the helper resets the panel and sends it all.
        spl = [f['value'] for f in frames if f['by'] == 'spl']
        assert spl and spl == expected[:len(spl)], 'SPL frames are not a stream prefix'
        assert [e[1:] for e in reset_edges[3:]] == [(False, 'helper'), (True, 'helper')], reset_edges
        assert reset_edges[3][0] > frames[len(spl) - 1]['end']
        spl_prefix, frames = len(spl), frames[len(spl):]
        # Power and reset are already high, so only the new low/high pulse is visible.
        waits = [reset_edges[3][0], reset_edges[4][0], frames[0]['start']]
        minimum = [50, 100]
    else:
        assert len(reset_edges) == 3, reset_edges
        spl_prefix = 0
        waits = [power[0]] + [e[0] for e in reset_edges[:3]] + [frames[0]['start']]
        minimum = [30, 15, 50, 100]
    assert [f['value'] for f in frames] == expected, 'decoded init stream differs from the generated stream'
    assert all(f['by'] == 'helper' for f in frames) if args.tamper else True
    for i, need in enumerate(minimum):
        assert waits[i + 1] - waits[i] >= need * TICKS_PER_MS, ('reset step wait', i, ms(waits[i + 1] - waits[i]))
    delays, fi = [], 0
    for op in stream:
        if op & 0x8000:
            gap = frames[fi]['start'] - frames[fi - 1]['end']
            assert gap >= (op & 0x7fff) * TICKS_PER_MS, ('stream delay', op & 0x7fff, ms(gap))
            delays.append(dict(after_frame=fi, need_ms=op & 0x7fff, got_ms=ms(gap),
                               sent_after_by=frames[fi]['by']))
        else:
            fi += 1
    enable = next(c for c, pc, ad, v in st['mmio'] if ad == 0x06511040 and v & 0x80000000)
    backlight = next(c for c, pc, ad, v in st['mmio'] if ad == 0x0300a040 and v & 1 and c >= enable)
    assert enable - frames[-1]['end'] >= 50 * TICKS_PER_MS, ms(enable - frames[-1]['end'])
    assert backlight - enable >= 100 * TICKS_PER_MS, ms(backlight - enable)
    panel = bytes.fromhex(st['handoff']['panel_record'])
    magic, version, size = struct.unpack_from('<3I', panel)
    record = dict(magic=hex(magic), version=version, bytes=size)
    if version == 2 and not args.tamper:
        nxt, reserved, since, wait = struct.unpack_from('<IIQQ', panel, 96)
        record.update(stream_next=nxt, stream_since_ms=ms(since - loaded), stream_wait_ms=ms(wait))
        by_spl = sum(f['by'] == 'spl' for f in frames)
        assert by_spl == len([op for op in stream[:nxt] if not op & 0x8000]), 'record disagrees with the trace'
    t0 = loaded  # SPL finished reading the payload
    return dict(
        variant=args.variant, payload=str(args.payload), payload_bytes=len(payload),
        logo='stock BOOTLOGO.BMP from ' + str(args.card) if args.card else 'none (black frame)',
        card_mb_per_s=args.mbps, outcome='reached BL31 entry, x0=0x4a300400',
        logo_record_at_handoff=st['handoff']['logo_record'],
        tamper=args.tamper, spl_frames_before_helper_reset=spl_prefix,
        frames=len(frames), frames_sent_by_spl=sum(f['by'] == 'spl' for f in frames),
        frames_sent_by_helper=sum(f['by'] == 'helper' for f in frames),
        panel_record_at_handoff=record, stream_delays=delays,
        reset_steps_ms=[ms(w - waits[0]) for w in waits[1:]], reset_steps_by=[power[1]] + [e[2] for e in reset_edges[:3]] + [e[2] for e in reset_edges[3:]],
        timeline_ms_from_payload_loaded={
            'spl_start': ms(started - t0), 'card_read_start': ms(read_started - t0), 'panel_power_on': ms(power[0] - t0),
            'first_stream_byte': ms(frames[0]['start'] - t0), 'last_stream_byte': ms(frames[-1]['end'] - t0),
            'helper_entry': ms(st['handoff']['clock'] - t0), 'tcon_enable': ms(enable - t0),
            'backlight_on': ms(backlight - t0), 'bl31_entry': ms(st['bl31']['clock'] - t0)},
        checks_passed=['decoded GPIO SPI frames equal the generated init stream', 'reset-step minimum waits',
                       'every stream delay honoured', 'TCON enable >= 50 ms after the stream',
                       'backlight >= 100 ms after TCON enable', 'no RTC writes; one SPL watchdog reload',
                       'SPL branch x0=0x4a300000 with DAIF masked', 'helper reached BL31 with x0=0x4a300400'],
        model='Compiled SPL and helper code; card, DRAM training, PMIC, SPL cache setup, EL3 system '
              'registers, PLL lock and scanline modelled. Time = counter reads (1 us each) + card '
              'transfer. Helper computation (pixel, kernel unpack) costs almost no modelled time, so '
              'times after helper_entry show the display waits only.')


if __name__ == '__main__':
    main()
