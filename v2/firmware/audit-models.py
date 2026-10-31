#!/usr/bin/env python3
"""Read-only fleet input audit. Run in a container; does not build firmware."""
import argparse
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[2]
NATIVE = ROOT / 'experiments/h700-boot-speed/native'


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def digest(data):
    return hashlib.sha256(data).hexdigest()


def audit(prepared):
    profiles = json.loads((ROOT / 'devices.json').read_text())['targets']
    contracts = json.loads((NATIVE.parent / 'evidence/compatibility.json').read_text())['targets']
    panels = json.loads((NATIVE / 'panel-contract.json').read_text())['models']
    native = module('fleet_policy', NATIVE / 'build.py')
    native.INPUT_WORK = prepared
    dram = module('fleet_dram', NATIVE.parent / 'spl/build-early-mmc.py')
    display = module('fleet_display', NATIVE / 'build-display.py')
    layout = module('fleet_layout', ROOT / 'experiments/h700-mainline/u3-join/build-helper.py')
    fdt = module('fleet_fdt', NATIVE.parent / 'fdt.py')
    streams = json.loads((NATIVE / 'panel-init.json').read_text())['models']
    result = {}
    for profile in profiles:
        target = profile['id']
        contract = contracts[target]
        with (prepared / target / 'boot-prefix.img').open('rb') as handle:
            handle.seek(8192)
            boot0 = handle.read(65536)
        package = (prepared / target / 'boot-gzip/boot-package.bin').read_bytes()
        assert boot0[4:12] == b'eGON.BT0'
        assert digest(boot0) == contract['boot0_sha256'], target
        assert digest(package) == contract['package_sha256'], target
        candidates = dram.dram_candidates(boot0)
        vendor_tree = native.toc_items(package)['dtb']
        lcd = {name: int.from_bytes(value, 'big') if len(value) == 4 else value.hex()
               for path, name, _, value in fdt.walk(vendor_tree)
               if path.endswith('lcd0@01c0c000') and name.startswith('lcd_')}
        entry = dict(boot0_sha256=digest(boot0), package_sha256=digest(package),
                     board_header_hex=boot0[0xf0:0xf8].hex(),
                     dram_candidates=[dict(type=w[1], clock_mhz=w[0],
                                           words=[hex(x) for x in w]) for w in candidates],
                     logo=[profile['bootlogo_width'], profile['bootlogo_height']],
                     rotation_ccw=profile['panel_rotation_ccw'],
                     panel_rule=panels[target]['rule'], pull_up=panels[target]['pull_up'],
                     panel_high_low=contract['vendor_pe6_lcd_type'], vendor_lcd=lcd,
                     panel_streams={k: streams[target][k] for k in ('old', 'boe')})
        inventory = prepared / 'h700-loader/native-logo-v1/fleet-v1/inventories' / target
        if inventory.is_dir():
            policy = native.model_policy(target, inventory)
            display.contracts(target, policy)
            display.handoff(target, policy)
            entry.update(policy_and_display_contracts='pass', model_id=policy['model_id'],
                         boot_partition_lba=policy['boot_partition_lba'],
                         framebuffer_from_ram_end=policy['boot_logo']['framebuffer_from_ram_end'],
                         logo_partition_lba=policy['boot_logo']['partition_lba'])
            try:
                layout.layout_header({'model_policy': policy})
                entry['v2a_layout_generator'] = 'pass'
            except AssertionError:
                entry['v2a_layout_generator'] = 'FAIL: RG28XX framebuffer offset assertion'
        else:
            entry['policy_and_display_contracts'] = 'not checked: fleet inventory absent'
        result[target] = entry
    return dict(scope='Offline prepared-input checks only; no hardware qualification', models=result)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('prepared', type=Path)
    print(json.dumps(audit(parser.parse_args().prepared), indent=2))
