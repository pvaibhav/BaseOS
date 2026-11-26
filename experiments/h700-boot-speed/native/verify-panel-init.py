#!/usr/bin/env python3
"""Recover panel command/data streams by executing each pinned vendor driver."""
from pathlib import Path
import argparse, hashlib, importlib.util, json, struct, sys
import capstone as c
import unicorn as u
from unicorn import arm_const as a

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.dont_write_bytecode = True
sys.path.insert(0, str(HERE.parent))
import fdt
BASE, STOP, STACK = 0x4a000000, 0x7000f000, 0x4a0ff000
SIGNATURE = bytes.fromhex('5f4af0b5136853b95e490123')
md = c.Cs(c.CS_ARCH_ARM, c.CS_MODE_THUMB)
md.detail = True
sha = lambda b: hashlib.sha256(b).hexdigest()


def machine(blob):
    m = u.Uc(u.UC_ARCH_ARM, u.UC_MODE_THUMB)
    m.mem_map(BASE, 0x100000)
    m.mem_write(BASE, blob)
    m.mem_write(BASE + 0xe0000, bytes(0x20000))
    m.mem_map(0x0300b000, 0x1000)
    m.mem_map(0x70000000, 0x10000)
    m.reg_write(a.UC_ARM_REG_SP, STACK)
    return m


def leaves(blob):
    found = [off for off in range(0x262e4, len(blob), 2)
             if blob[off:off + len(SIGNATURE)] == SIGNATURE]
    assert len(found) == 2 and found[1] - found[0] == 0x190, found
    return found


def run(target, level):
    contract = json.loads((HERE / 'panel-contract.json').read_text())['models'][target]
    package = (ROOT / f'work/{target}/boot-gzip/boot-package.bin').read_bytes()
    assert sha(package) == contract['package_sha256']
    items = {}
    for index in range(struct.unpack_from('<I', package, 32)[0]):
        at = 64 + index * 368
        name = package[at:at + 64].split(b'\0', 1)[0].decode()
        offset, size = struct.unpack_from('<II', package, at + 64)
        assert name not in items and offset + size <= len(package)
        items[name] = package[offset:offset + size]
    blob = items['u-boot']
    assert sha(blob) == contract['vendor_uboot_sha256']
    inventory = json.loads((ROOT / f'experiments/h700-boot-speed/uboot-trace-notes/delays/inventory-{target}.json').read_text())
    panel, = [p for p in inventory['panels'] if p['selected_by_dt']]
    assert fdt.get(items['dtb'], 'lcd0@01c0c000', 'lcd_driver_name').rstrip(b'\0').decode() == panel['name']
    table = int(panel['struct'], 16)
    assert blob[table:table + 32].rstrip(b'\0').decode() == panel['name']
    assert struct.unpack_from('<3I', blob, table + 32) == tuple(BASE + int(panel[k], 16) + 1 for k in ('cfg_panel_info', 'open_flow', 'close_flow'))
    entry = int(panel['steps'][1]['func'], 16)
    command, data = leaves(blob)
    # The panel body only calls byte leaves, the display delay wrapper,
    # environment setter and its model-specific console formatter.
    branch_targets, seen, queue = set(), set(), [entry]
    while queue:
        off = queue.pop()
        while off not in seen:
            seen.add(off)
            ins = next(md.disasm(blob[off:off + 4], BASE + off), None)
            assert ins is not None, hex(off)
            if ins.mnemonic == 'bl':
                branch_targets.add(int(ins.op_str[1:], 16) - BASE)
            elif c.CS_GRP_JUMP in ins.groups:
                if ins.mnemonic == 'bx':
                    assert ins.op_str == 'lr'
                    break
                destination = ins.operands[-1].imm - BASE
                queue.append(destination)
                if ins.mnemonic in ('b', 'b.w'):
                    break
            elif ins.mnemonic.startswith('pop') and 'pc' in ins.op_str:
                break
            off += ins.size
    external = branch_targets - {command, data, 0x2621c, 0xe620, 0xe4e4}
    assert all(t > command + 0x300 for t in external), external
    assert len(external) == 1, external
    formatter, = external
    m = machine(blob)
    flow = []
    def register_flow(uc, pc, size, context):
        flow.append((uc.reg_read(a.UC_ARM_REG_R1), uc.reg_read(a.UC_ARM_REG_R2)))
        uc.reg_write(a.UC_ARM_REG_R0, 0)
        uc.reg_write(a.UC_ARM_REG_PC, uc.reg_read(a.UC_ARM_REG_LR))
    handle = m.hook_add(u.UC_HOOK_CODE, register_flow, begin=BASE + 0x21180, end=BASE + 0x21180)
    m.reg_write(a.UC_ARM_REG_R0, 0)
    m.reg_write(a.UC_ARM_REG_LR, STOP | 1)
    m.emu_start(BASE + int(panel['open_flow'], 16) | 1, STOP, count=10000)
    assert m.reg_read(a.UC_ARM_REG_PC) == STOP
    assert flow == [(BASE + int(step['func'], 16) + 1, step['delay_ms']) for step in panel['steps']]
    m.hook_del(handle)
    m.reg_write(a.UC_ARM_REG_SP, STACK)
    registers, writes, stream, environment = {}, [], [], []
    def text(address):
        return bytes(m.mem_read(address, 128)).split(b'\0', 1)[0].decode()
    def rd(uc, access, address, size, value, context):
        assert size == 4
        value = (0x40 if level else 0) if address == 0x0300b0a0 else registers.get(address, 0)
        uc.mem_write(address, struct.pack('<I', value))
    def wr(uc, access, address, size, value, context):
        assert size == 4 and address in (0x0300b124, 0x0300b130, 0x0300b090, 0x0300b0ac)
        registers[address] = value
        writes.append([hex(address), hex(value)])
    def hook(uc, pc, size, context):
        offset = pc - BASE
        value = uc.reg_read(a.UC_ARM_REG_R0)
        if offset in (command, data):
            assert value <= 255
            stream.append(['command' if offset == command else 'data', value])
        elif offset == 0x2621c:
            stream.append(['delay_ms', value])
        elif offset == 0xe620:
            environment.append([text(value), text(uc.reg_read(a.UC_ARM_REG_R1))])
        elif offset == 0xe4e4:
            assert value == 3
            argv = uc.reg_read(a.UC_ARM_REG_R1)
            words = [text(struct.unpack('<I', uc.mem_read(argv + i * 4, 4))[0]) for i in range(3)]
            assert words[0] == 'setenv'
            environment.append(words[1:])
        else:
            assert offset == formatter
        uc.reg_write(a.UC_ARM_REG_R0, 0)
        uc.reg_write(a.UC_ARM_REG_PC, uc.reg_read(a.UC_ARM_REG_LR))
    m.hook_add(u.UC_HOOK_MEM_READ, rd, begin=0x0300b000, end=0x0300bfff)
    m.hook_add(u.UC_HOOK_MEM_WRITE, wr, begin=0x0300b000, end=0x0300bfff)
    for offset in (command, data, 0x2621c, 0xe620, 0xe4e4, formatter):
        m.hook_add(u.UC_HOOK_CODE, hook, begin=BASE + offset, end=BASE + offset)
    m.reg_write(a.UC_ARM_REG_R0, 0)
    m.reg_write(a.UC_ARM_REG_LR, STOP | 1)
    m.emu_start(BASE + entry | 1, STOP, count=2000000)
    assert m.reg_read(a.UC_ARM_REG_PC) == STOP
    expected_panel = contract['high' if level else 'low']
    assert environment == [['lcd_type', expected_panel]], environment
    assert stream[:3] == [['delay_ms', 15], ['delay_ms', 50], ['delay_ms', 100]]
    assert any(op[0] == 'command' and op[1] == 0x11 for op in stream)
    return dict(target=target, pe6='high' if level else 'low', selected_panel=expected_panel,
                uboot_sha256=sha(blob), entry=hex(entry), byte_leaves=[hex(command), hex(data)],
                formatter=hex(formatter), reset_and_strap_writes=writes,
                stream=stream, stream_sha256=sha(json.dumps(stream, separators=(',', ':')).encode()),
                gpio_byte_leaves_modeled=True, device_access=False, passed=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--contract', type=Path, default=HERE / 'panel-init.json')
    args = parser.parse_args()
    assert not args.output.exists(), 'use a fresh proof directory'
    args.output.mkdir(parents=True)
    policy = json.loads((HERE / 'panel-contract.json').read_text())['models']
    derived = {'version': 1, 'encoding': 'uint16: 0..255 command; 0x100..0x1ff data; 0x8000|milliseconds delay',
               'initial_reset_delays_ms': [15, 50, 100], 'models': {}, 'streams': {}}
    rows = []
    for target in policy:
        for level in (True, False):
            row = run(target, level)
            ops = [value | {'command': 0, 'data': 0x100, 'delay_ms': 0x8000}[kind]
                   for kind, value in row['stream'][3:]]
            digest = sha(b''.join(value.to_bytes(2, 'little') for value in ops))
            derived['streams'][digest] = ops
            model = derived['models'].setdefault(target, {'package_sha256': policy[target]['package_sha256'],
                    'vendor_uboot_sha256': row['uboot_sha256'], 'old': None, 'boe': None})
            assert model[row['selected_panel']] in (None, digest)
            model[row['selected_panel']] = digest
            (args.output / f'{target}-{row["pe6"]}.json').write_text(json.dumps(row, indent=2) + '\n')
            rows.append({k: v for k, v in row.items() if k not in ('stream', 'reset_and_strap_writes')})
    assert derived == json.loads(args.contract.read_text()), 'panel initialization contract differs from actual vendor execution'
    report = dict(passed=True, case_count=len(rows), cases=rows, contract_sha256=sha(args.contract.read_bytes()),
                  script_sha256=sha(Path(__file__).read_bytes()), device_access=False, hardware_qualified=False,
                  scope='actual vendor panel body; command/data, delay, env and console boundaries modeled')
    (args.output / 'verification.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({'passed': True, 'cases': len(rows), 'unique_streams': len(derived['streams'])}))


if __name__ == '__main__':
    main()
