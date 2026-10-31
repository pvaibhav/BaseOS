#!/usr/bin/env python3
"""Run in v2-kbuild after build.sh; optional stock DTB directory argument."""
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile


def read_fdt(path):
    data = Path(path).read_bytes()
    magic, total, off, strings = struct.unpack_from('>4I', data)
    assert magic == 0xd00dfeed and total <= len(data), path
    nodes, stack = {}, []
    while True:
        token, = struct.unpack_from('>I', data, off)
        off += 4
        if token == 1:
            end = data.index(0, off)
            stack.append(data[off:end].decode())
            nodes['/'.join(stack) or '/'] = {}
            off = (end + 4) & ~3
        elif token == 2:
            stack.pop()
        elif token == 3:
            length, name = struct.unpack_from('>2I', data, off)
            off += 8
            end = data.index(0, strings + name)
            nodes['/'.join(stack) or '/'][data[strings + name:end].decode()] = data[off:off+length]
            off = (off + length + 3) & ~3
        elif token == 9:
            return nodes
        else:
            assert token == 4, (path, token)


def cells(value):
    return struct.unpack('>' + 'I' * (len(value) // 4), value)


def text(value):
    return value.rstrip(b'\0').decode()


def inputs(nodes, vendor=False):
    result = set()
    for name, props in nodes.items():
        parent = name.rsplit('/', 1)[0]
        if parent not in nodes or nodes[parent].get('label') != b'ANBERNIC-keys\0':
            continue
        gpio = cells(props['gpios'])
        result.add((gpio[1], gpio[2], cells(props['linux,code'])[0],
                    cells(props.get('linux,input-type', b'\0\0\0\1'))[0],
                    cells(props.get('linux,input-value', b'\0\0\0\0'))[0]))
        assert vendor or gpio[3] == 17, (name, gpio)
    return result


repo = Path('/repo')
kbuild = Path('/k/build')
stock = Path(sys.argv[1]) if len(sys.argv) > 1 else None
original = Path('/k/models-original-sha.json')
for model in json.loads((repo/'devices.json').read_text())['targets']:
    ident = model['id']
    dtb = repo/'work/v2/kernel'/f'{ident}.dtb'
    nodes = read_fdt(dtb)
    assert text(nodes['/']['compatible']).split('\0')[0] == f'anbernic,{ident}'
    with tempfile.TemporaryDirectory(prefix='baseos-dt-') as temp:
        merged = Path(temp)/'merged.dtb'
        subprocess.run(['fdtoverlay', '-i', str(dtb), '-o', str(merged),
                        str(kbuild/'drivers/of'/f'baseos-{ident}.dtbo')], check=True)
        subprocess.run(['dtc', '-q', '-I', 'dtb', '-O', 'dts', '-o', '/dev/null', str(merged)], check=True)
        tree = read_fdt(merged)
    assert any(p.get('compatible') == b'x-powers,axp717\0' for p in tree.values())
    assert any(p.get('dr_mode') == b'peripheral\0' for p in tree.values())
    if ident not in ('rg28xx',):
        rtcpath = text(tree['/aliases']['rtc0'])
        assert tree[rtcpath]['compatible'] == b'nxp,pcf8563\0'
        panel = next(p for p in tree.values() if p.get('compatible') == f'baseos,{ident}-panel\0'.encode())
        assert panel['status'] == b'okay\0'
    if ident not in ('rg28xx', 'rgsp'):
        plan = json.loads((repo/'kernel/boards/input-abi.json').read_text())['models'][ident]
        lid = [p for p in tree.values() if p.get('linux,input-type') == bytes.fromhex('00000005')]
        assert len(lid) == int(plan['lid']), ident
        if lid:
            assert cells(lid[0]['gpios'])[1:] == (4, 7, 17)
            assert cells(lid[0]['linux,code']) == (0,)
        if plan['axes']:
            joy = tree['/adc-joystick']
            assert len(cells(joy['io-channels'])) == len(plan['axes']) * 2
            for axis in plan['axes']:
                number = axis['mux_channel']
                entry = tree[f'/adc-joystick/axis@{number}']
                assert cells(entry['abs-range']) == ((4096, 0) if axis['inverted'] else (0, 4096))
        if stock:
            vendor_tree = read_fdt(stock/f'{ident}.dtb')
            capacity = next(cells(p['pmu_battery_cap'])[0] for p in vendor_tree.values() if 'pmu_battery_cap' in p)
            assert cells(tree['/battery']['charge-full-design-microamp-hours']) == (capacity * 1000,)
            has_reset = any('wlan_regon' in p for p in vendor_tree.values())
            assert ('reset-gpios' in tree['/wifi-pwrseq']) == has_reset
            motor = next(cells(p['moto_pwm_gpio'])[1:3] for p in vendor_tree.values() if 'moto_pwm_gpio' in p)
            assert cells(tree['/vibrator']['enable-gpios'])[1:3] == motor
    count = len(inputs(tree))
    if stock:
        expected = inputs(read_fdt(stock/f'{ident}.dtb'), vendor=True)
        # RG40XX V stock duplicates H, including an unpopulated right click.
        if ident == 'rg40xxv':
            expected = {x for x in expected if x[:2] != (4, 9)}
        assert inputs(tree) == expected, (ident, inputs(tree) ^ expected)
    if ident in ('rg28xx', 'rgsp') and original.exists() and stock:
        assert hashlib.sha256(dtb.read_bytes()).hexdigest() == json.loads(original.read_text())[ident]
    print(f'{ident}: decoded, overlay applied, {count} stock GPIO input tuples checked' if stock else f'{ident}: decoded, overlay applied')
