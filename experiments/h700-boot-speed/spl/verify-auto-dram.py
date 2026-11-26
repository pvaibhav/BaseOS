#!/usr/bin/env python3
"""Replay each real bank as winner, with actual reset/PHY stores and unwind.

Training result, detected geometry, voltage writes, waits and delays are model
boundaries. No DRAM is mapped. Existing artifacts are never written.
"""
import argparse
import hashlib
import importlib.util
import itertools
import json
from pathlib import Path
import re
import struct

import unicorn as u
from unicorn import arm64_const as x

HERE = Path(__file__).resolve().parent
MODELS = ('rg28xx', 'rg34xx', 'rg34xxsp', 'rg35xxh', 'rg35xxplus', 'rg35xxpro',
          'rg35xxsp', 'rg40xxh', 'rg40xxv', 'rgcubexx', 'rgsp')
SID_VALUES = (0x33806c00, 0x2000, 0x2800, 0x5000, 0x5400, 0x5c00, 0x5d00, 0x5200, 0x7200)
X = [getattr(x, f'UC_ARM64_REG_X{i}') for i in range(31)]
RESET_REGISTERS = (0x03001540, 0x0300180c, 0x03001010, 0x03001800)
RESET_PREFIX = ((RESET_REGISTERS[0], 0x7fffffff), (RESET_REGISTERS[0], 0x3fffffff),
                (RESET_REGISTERS[1], 0xfffffffe), (RESET_REGISTERS[1], 0xfffefffe),
                (RESET_REGISTERS[2], 0x7fffffff), (RESET_REGISTERS[3], 0xbfffffff))


def digest(data):
    return hashlib.sha256(data).hexdigest()


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def prove(directory, model, vendor_boot0, sid_values=SID_VALUES):
    inputs = {}

    def read(path):
        data = path.read_bytes()
        inputs[str(path.resolve())] = {'sha256': digest(data), 'bytes': len(data)}
        return data

    manifest = json.loads(read(directory / 'manifest.json'))
    assert model in MODELS and manifest['native_dram'], 'requires a named model and native A64 DRAM selector'
    embedded_model = manifest.get('target', manifest.get('native_loader', {}).get('target'))
    assert embedded_model is None or embedded_model == model, 'model differs from generated SPL identity'
    blob, stock = read(directory / 'spl.bin'), read(vendor_boot0)
    assert len(stock) == 65536 and stock[4:12] == b'eGON.BT0', 'vendor input must be exact 64KiB boot0'
    assert digest(blob) == manifest['spl_sha256']
    assert digest(stock) == manifest['restore_source_sha256']
    symbols = {name: int(address, 16) for address, name in re.findall(
        r'^([0-9a-f]+) [a-zA-Z] (\w+)$', read(directory / 'symbols.txt').decode(), re.M)}
    count = len(manifest['candidate_types'])
    para_base, vendor_base = symbols['dram_candidates'], symbols['dram_vendor_candidates']
    parameters = [blob[para_base - 0x20000 + i * 48:para_base - 0x20000 + (i + 1) * 48] for i in range(count)]
    banks = [blob[vendor_base - 0x20000 + i * 128:vendor_base - 0x20000 + (i + 1) * 128] for i in range(count)]
    source_banks = []
    for offset in (0x38, 0x9f4, 0xa74):
        bank = stock[offset:offset + 128]
        if bank not in source_banks: source_banks.append(bank)
    assert banks == source_banks, 'embedded candidate banks differ from pinned stock banks'
    field_indices = (0, 1, 2, 3, 4, 5, 22, 24, 26, 27, 28, 29)
    assert parameters == [struct.pack('<12I', *(struct.unpack('<32I', bank)[i] for i in field_indices))
                          for bank in banks], 'effective training parameters differ from pinned banks'
    oracle_path = HERE / 'verify-phy-map.py'
    read(oracle_path)
    read(Path(__file__))
    oracle = load('actual_vendor_phy', oracle_path)
    vendor_expected, vendor_maps = {}, {}
    for bank, sid in itertools.product(range(count), sid_values):
        actual = bytearray(stock)
        actual[0x38:0xb8] = banks[bank]
        vendor_expected[bank, sid] = oracle.vendor_values(bytes(actual), manifest['candidate_types'][bank], sid)
        kind = manifest['candidate_types'][bank]
        matching = []
        for variant in (0, 1):
            offset = symbols[f'phy_init_map{variant}'] - 0x20000 + kind * 27
            if list(blob[offset:offset + 27]) == vendor_expected[bank, sid]: matching.append(variant)
        assert len(matching) == 1, 'vendor bank requests a remap outside the two supported physical tables'
        vendor_maps[bank, sid] = matching[0]

    results = []
    for winner, failure_mode, sid in itertools.product(range(-1, count),
            ('rank-exhausted', 'timeout', 'final-core'), sid_values):
        machine = u.Uc(u.UC_ARCH_ARM64, u.UC_MODE_ARM)
        machine.mem_map(0x20000, 0x38000)
        machine.mem_write(0x20000, blob)
        machine.mem_map(0x03000000, 0x08000000)
        machine.mem_write(0x03006200, struct.pack('<I', sid))
        machine.mem_write(symbols['baseos_dram_power'], struct.pack('<I', 1))
        machine.reg_write(x.UC_ARM64_REG_SP, 0x52a00)
        machine.reg_write(X[30], 0x55ff0)
        for index in range(18, 30): machine.reg_write(X[index], 0xa50100000000 + index)
        checkpoints, unwinds, calls, voltages = [], [], [], []
        state = {'active': None, 'complete': False, 'unwinding': False, 'outcome': None}

        def word(address):
            return struct.unpack('<I', machine.mem_read(address, 4))[0]

        def returned(value):
            machine.reg_write(X[0], value)
            machine.reg_write(x.UC_ARM64_REG_PC, machine.reg_read(X[30]))

        def code(m, address, size, user):
            if state['complete']:
                call = state['active']
                assert tuple(call['reset_writes'][:6]) == RESET_PREFIX, 'core did not reassert complete reset prefix'
                assert call['phy'] == vendor_expected[call['bank'], sid], 'actual PHY stores differ from vendor instructions'
                state['complete'] = False
                if failure_mode == 'timeout' and call['bank'] != winner:
                    # Poison the in-flight core frame; real panic must recover
                    # the checkpoint's stack, platform reg and callee saves.
                    for index in range(18, 30): m.reg_write(X[index], 0xdead0000 + index)
                    m.reg_write(x.UC_ARM64_REG_PC, symbols['dram_panic'])
                    return
                # The remaining PHY training result/function epilogue is the
                # modeled boundary. Actual reset/timing/map writes ran above.
                for reg, value in call['callee'].items(): m.reg_write(reg, value)
                m.reg_write(x.UC_ARM64_REG_SP, call['sp'])
                m.reg_write(X[30], call['lr'])
                successful = call['bank'] == winner or (failure_mode == 'final-core' and
                    sum(previous['bank'] == call['bank'] for previous in calls) == 1)
                returned(int(successful))
                return
            if state['unwinding'] and address == checkpoints[-1]['lr']:
                saved = checkpoints[-1]
                assert m.reg_read(x.UC_ARM64_REG_SP) == saved['sp'], 'panic did not restore checkpoint SP'
                assert all(m.reg_read(reg) == value for reg, value in saved['callee'].items()), 'panic did not restore checkpoint registers'
                assert m.reg_read(X[0]) == 1, 'panic did not return checkpoint retry result'
                unwinds.append(saved['bank'])
                state['unwinding'] = False
            if address == symbols['dram_checkpoint']:
                checkpoints.append({'bank': len(checkpoints), 'sp': m.reg_read(x.UC_ARM64_REG_SP),
                    'lr': m.reg_read(X[30]), 'callee': {X[i]: m.reg_read(X[i]) for i in range(18, 30)}})
            elif address == symbols['dram_panic']:
                state['unwinding'] = True
            elif address == symbols['mctl_core_init']:
                pointer = m.reg_read(X[0])
                assert pointer >= para_base and (pointer - para_base) % 48 == 0
                bank = (pointer - para_base) // 48
                assert bank < count and bytes(m.mem_read(pointer, 48)) == parameters[bank]
                call = {'bank': bank, 'sp': m.reg_read(x.UC_ARM64_REG_SP), 'lr': m.reg_read(X[30]),
                        'callee': {X[i]: m.reg_read(X[i]) for i in range(18, 30)}, 'reset_writes': [], 'phy': []}
                calls.append(call)
                state['active'] = call
                for register in RESET_REGISTERS: m.mem_write(register, struct.pack('<I', 0xffffffff))
            elif address == symbols['mctl_auto_detect_dram_size']:
                m.mem_write(m.reg_read(X[1]), bytes((10, 15, 1, 1)))
                returned(0)
            elif address == symbols['axp_set_dcdc3']:
                voltages.append(m.reg_read(X[0]))
                returned(0)
            elif address == symbols['mctl_check_memory']:
                # The size arithmetic runs; backing DRAM existence is modeled.
                returned(1)
            elif address in {symbols[name] for name in ('mctl_await_completion', 'udelay', '__udelay')}:
                returned(0)
            elif address in (symbols['panic'], 0x55ff0):
                state['outcome'] = 'failed-all' if address == symbols['panic'] else 'selected'
                m.emu_stop()

        def write(m, access, address, size, value, user):
            call = state['active']
            if call is None: return
            if address in RESET_REGISTERS: call['reset_writes'].append((address, value))
            if 0x048000c0 <= address <= 0x04800128:
                assert size == 4 and address == 0x048000c0 + len(call['phy']) * 4
                call['phy'].append(value)
                if len(call['phy']) == 27: state['complete'] = True

        machine.hook_add(u.UC_HOOK_CODE, code)
        machine.hook_add(u.UC_HOOK_MEM_WRITE, write)
        def invalid(m, access, address, size, value, user):
            state['unmapped'] = {'address': hex(address), 'pc': hex(m.reg_read(x.UC_ARM64_REG_PC)),
                'winner': winner, 'failure_mode': failure_mode, 'sid': hex(sid), 'calls': [call['bank'] for call in calls]}
            return False
        machine.hook_add(u.UC_HOOK_MEM_INVALID, invalid)
        try: machine.emu_start(symbols['sunxi_dram_init'], 0x58000, count=500000)
        except u.UcError as error: raise AssertionError(state.get('unmapped', {'pc': hex(machine.reg_read(x.UC_ARM64_REG_PC))})) from error
        expected_attempts = count if winner < 0 else winner + 1
        assert state['outcome'] == ('failed-all' if winner < 0 else 'selected'), f'bank {winner} did not reach expected outcome'
        assert word(symbols['dram_probe_attempts']) == expected_attempts
        assert word(symbols['dram_probe_selected_type']) == (0 if winner < 0 else manifest['candidate_types'][winner])
        assert len(checkpoints) == expected_attempts
        assert unwinds == ([] if failure_mode == 'final-core' else
                           list(range(expected_attempts - (winner >= 0)))), unwinds
        assert voltages == [{3: 1500, 7: 1200, 8: 1100}[kind] for kind in manifest['candidate_types'][:expected_attempts]], 'candidate voltage/type sequence differs'
        assert bytes(machine.mem_read(para_base, 48 * count)) == b''.join(parameters)
        assert bytes(machine.mem_read(vendor_base, 128 * count)) == b''.join(banks)
        if winner >= 0:
            assert machine.reg_read(x.UC_ARM64_REG_SP) == 0x52a00
            assert machine.reg_read(X[0]) == 1 << 30
            expected = bytearray(banks[winner])
            struct.pack_into('<II', expected, 24, 0x30fa, 0x04000000)
            assert bytes(machine.mem_read(symbols['trained_vendor_params'], 128)) == expected, 'winning full vendor parameter block differs'
            for index in range(18, 30): assert machine.reg_read(X[index]) == 0xa50100000000 + index
        results.append({'winner_bank': winner, 'winner_type': manifest['candidate_types'][winner] if winner >= 0 else None,
            'failure_mode': failure_mode, 'sid': hex(sid), 'attempts': expected_attempts,
            'winner_physical_map': vendor_maps[winner, sid] if winner >= 0 else None,
            'core_bank_indices': [call['bank'] for call in calls], 'actual_reset_prefixes': len(calls),
            'actual_vendor_phy_matches': len(calls), 'verified_checkpoint_unwinds': unwinds,
            'selected_full_parameter_block_exact': winner >= 0, 'outcome': state['outcome']})
    report = {'passed': True, 'model': model, 'spl_sha256': digest(blob), 'vendor_boot0_sha256': digest(stock),
        'inputs': inputs,
        'proof_source_sha256': digest(Path(__file__).read_bytes()), 'candidate_types': manifest['candidate_types'],
        'parameter_bank_sha256': [digest(bank) for bank in banks], 'cases': results,
        'case_count': len(results), 'winner_bank_indices': list(range(count)),
        'same_type_bank_winner_coverage': True, 'sid_inputs': [hex(sid) for sid in sid_values],
        'physical_variants_covered': sorted(set(vendor_maps.values())),
        'vendor_oracle': {'remapper_entry': '0x255b1', 'actual_memcpy': '0x22620', 'modeled_boundaries': []},
        'geometry_model': {'cols': 10, 'rows': 15, 'ranks': 1, 'bus_full_width': True, 'bytes': 1 << 30},
        'dram_mapped': False, 'training_executed': False,
        'modeled_boundaries': ['training result and core epilogue after 27 real PHY stores',
            'detected geometry and backing-memory check', 'voltage write', 'delay', 'completion wait'],
        'scope': 'Exact generated native selector/core; each same-type bank wins by index.',
        'prior_evidence_limit': 'variant-probes-v1 all11 A32 tests model success by memory type and retain compile-time primary-type/map1 PHY tables. They cover retries/timings/protocol, not every same-type bank winner or combined runtime physical-map selection.'}
    encoded = json.dumps(report, sort_keys=True, separators=(',', ':')).encode()
    report['seal_sha256'] = digest(encoded)
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    parser.add_argument('--model', choices=MODELS, required=True)
    parser.add_argument('--vendor-boot0', type=Path, required=True,
                        help='exact pinned stock64KiB whose parameter banks generated this SPL')
    parser.add_argument('--sid', type=lambda value: int(value, 0), action='append',
                        help='repeat to override the default nine SID inputs')
    parser.add_argument('--report', type=Path, required=True, help='fresh report file; existing evidence is never overwritten')
    args = parser.parse_args()
    if args.report.exists(): parser.error('choose a fresh report file')
    sids = tuple(args.sid) if args.sid else SID_VALUES
    if not sids or len(set(sids)) != len(sids) or any(not 0 <= sid <= 0xffffffff for sid in sids):
        parser.error('SID inputs must be distinct unsigned32-bit values')
    result = prove(args.directory, args.model, args.vendor_boot0, sids)
    with args.report.open('x') as output: json.dump(result, output, indent=2)
    print(json.dumps({key: value for key, value in result.items() if key != 'cases'}, indent=2))
