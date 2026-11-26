#!/usr/bin/env python3
"""Prepare and verify the out-of-tree H700 modules against pinned vendor kernels.

Two modules are built in one kernel-build run: the speaker-amplifier fix and
the one that starts the USB host controllers the boot files hold back.

An import CRC check alone cannot validate private ASoC layouts. profiles.json
is a reviewed allowlist of exact Images; refreshing firmware requires auditing
those layouts and the codec callbacks before adding a new Image hash. The USB
module uses exported calls only, so its import CRCs are its whole contract.
"""
import argparse
import gzip
import hashlib
import json
from pathlib import Path
import shutil
import struct
import sys

import kernel_abi

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / 'src/h700-speaker-amp'
LATE_USB = ROOT / 'src/h700-late-usb'
# The first name is the audio module; every name is built, checked and shipped.
MODULES = ('h700_speaker_amp', 'h700_late_usb', 'h700_late_sdc1')


def digest(blob):
    return hashlib.sha256(blob).hexdigest()


def inputs(target):
    kernel, modules = kernel_abi.load_inputs(target)
    profiles = json.loads((SOURCE / 'profiles.json').read_text())
    profile = profiles.get(target)
    if not profile or profile['image_sha256'] != digest(kernel):
        raise ValueError(f'{target}: unaudited audio kernel; update the audited profile first')
    start = kernel.index(b'IKCFG_ST') + 8
    end = kernel.index(b'IKCFG_ED', start)
    config = gzip.decompress(kernel[start:end])
    if digest(config) != profile['config_sha256']:
        raise ValueError(f'{target}: kernel config differs from audited profile')
    return kernel, modules, config, profile


def stamp(target):
    kernel, _, config, _ = inputs(target)
    h = hashlib.sha256(kernel + config)
    for path in (sorted(SOURCE.rglob('*')) + sorted(LATE_USB.rglob('*'))
                 + [Path(__file__), ROOT / 'build-audio-module.sh', ROOT / 'tools/kernel_abi.py']):
        if path.is_file():
            h.update(path.name.encode() + b'\0' + path.read_bytes())
    return h.hexdigest()


def vendor_contract(kernel, modules):
    """Derive the contract from current vendor inputs, never cached metadata."""
    known = {}
    identities = set()
    for sections in modules.values():
        known.update(kernel_abi.modversions(sections.get('__versions', b'')))
        identities.add((kernel_abi.modinfo(sections['.modinfo'])['vermagic'],
                        len(sections['.gnu.linkonce.this_module'])))
    if len(identities) != 1:
        raise ValueError('vendor modules disagree on kernel/module identity')
    vermagic, module_size = identities.pop()
    return vermagic, module_size, kernel_abi.exported_crcs(kernel, known)


def prepare(target, output):
    kernel, modules, config, profile = inputs(target)
    _, _, exports = vendor_contract(kernel, modules)
    output.mkdir(parents=True, exist_ok=True)
    (output / 'kernel.config').write_bytes(config)
    (output / 'Module.symvers').write_text(''.join(f'0x{crc:08x}\t{name}\tvmlinux\tEXPORT_SYMBOL\n' for name, crc in sorted(exports.items())))
    module_dir = output / 'module'
    module_dir.mkdir(exist_ok=True)
    for name in ['Makefile', 'h700_speaker_amp.c']:
        shutil.copyfile(SOURCE / name, module_dir / name)
    # One kbuild directory and one prepared kernel tree serve all modules.
    shutil.copyfile(LATE_USB / 'h700_late_usb.c', module_dir / 'h700_late_usb.c')
    shutil.copyfile(LATE_USB / 'h700_late_sdc1.c', module_dir / 'h700_late_sdc1.c')
    with (module_dir / 'Makefile').open('a') as makefile:
        makefile.write((LATE_USB / 'Makefile').read_text())
    start = kernel.index(b'Linux version ')
    banner = kernel[start:kernel.index(b'\0', start)].decode()
    names = {'linux_banner': 'LINUX_BANNER', 'sunxi_spk_event': 'SPK_EVENT',
             'sunxi_lineout_event': 'LINEOUT_EVENT', 'client_mutex': 'CLIENT_MUTEX',
             'codec_list': 'CODEC_LIST', 'allen_spk_state': 'JACK_STATE',
             'sunxi_pinctrl_gpio_set': 'GPIO_SET',
             'sunxi_pinctrl_gpio_direction_output': 'GPIO_DIRECTION_OUTPUT'}
    version = banner[banner.index('#'):].rstrip('\n')
    release = banner.split()[2]
    header = '/* Generated from the audited vendor Image. */\n'
    for name, value in [('BANNER', banner), ('RELEASE', release), ('VERSION', version)]:
        header += '#define H700_KERNEL_' + name + ' ' + json.dumps(value) + '\n'
    for name, macro in names.items():
        header += f'#define H700_{macro}_OFFSET {profile["symbol_offsets"][name]}UL\n'
    header += ('static inline unsigned long h700_kernel_address(unsigned long offset)\n'
               '{ return (unsigned long)&kallsyms_lookup_name + offset; }\n')
    (module_dir / 'h700_kernel_profile.h').write_text(header)
    (output / 'expected.json').write_text(json.dumps({'stamp': stamp(target)}))


def validate_module(blob, kernel, modules):
    vermagic, module_size, exports = vendor_contract(kernel, modules)
    sections = kernel_abi.elf_sections(blob)
    info = kernel_abi.modinfo(sections['.modinfo'])
    if info.get('vermagic') != vermagic:
        raise ValueError('module vermagic differs from vendor modules')
    if len(sections['.gnu.linkonce.this_module']) != module_size:
        raise ValueError('struct module differs from vendor modules')
    imports = kernel_abi.modversions(sections['__versions'])
    strings = sections['.strtab']
    undefined = {strings[name:strings.index(b'\0', name)].decode()
                 for name, info, other, index, value, size
                 in struct.iter_unpack('<IBBHQQ', sections['.symtab'])
                 if name and index == 0}
    if not undefined.issubset(imports):
        raise ValueError('module has unversioned imports: ' + ', '.join(sorted(undefined - imports.keys())))
    if 'module_layout' not in imports:
        raise ValueError('module has no module_layout symbol version')
    if any(exports.get(name) != crc for name, crc in imports.items()):
        raise ValueError('module symbol versions differ from vendor kernel')
    return len(imports)


def verify(target, output):
    # Recheck source/kernel freshness, including after a long-running build.
    expected = json.loads((output / 'expected.json').read_text())
    current_stamp = stamp(target)
    if expected['stamp'] != current_stamp:
        raise ValueError('module inputs changed during compilation')
    kernel, modules, _, _ = inputs(target)
    blobs = {name: (output / f'module/{name}.ko').read_bytes() for name in MODULES}
    imported = {name: validate_module(blob, kernel, modules) for name, blob in blobs.items()}
    # Publish bytes first, then their integrity record. Interrupted publication
    # is a cache miss; a later build never trusts an unsealed output.
    for name, blob in blobs.items():
        (output / f'{name}.ko').write_bytes(blob)
    record = {'target': target, 'stamp': current_stamp, 'sha256': digest(blobs[MODULES[0]]),
              'modules': {name: digest(blob) for name, blob in blobs.items()}}
    temporary = output / '.artifact.json.tmp'
    temporary.write_text(json.dumps(record) + '\n')
    temporary.replace(output / '.artifact.json')
    for name, blob in blobs.items():
        print(f'{target}: {name} verified ({len(blob)} bytes, {imported[name]} symbol versions)')


def current(target, output):
    kernel, modules, _, _ = inputs(target)
    try:
        record = json.loads((output / '.artifact.json').read_text())
        if record['target'] != target or record['stamp'] != stamp(target):
            return False
        for name in MODULES:
            blob = (output / f'{name}.ko').read_bytes()
            if record['modules'][name] != digest(blob):
                return False
            # An intact digest is not an ABI proof: check against the actual
            # current kernel, including if cached metadata was modified.
            validate_module(blob, kernel, modules)
        if record['sha256'] != record['modules'][MODULES[0]]:
            return False
    except (OSError, ValueError, KeyError, TypeError, struct.error):
        return False
    print(f'{target}: kernel modules are current')
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['prepare', 'verify', 'stamp', 'current'])
    parser.add_argument('target')
    args = parser.parse_args()
    output = ROOT / 'work' / args.target / 'audio-module'
    if args.command == 'current':
        return 0 if current(args.target, output) else 1
    if args.command == 'stamp':
        print(stamp(args.target))
    else:
        globals()[args.command](args.target, output)


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (ValueError, KeyError, OSError, TypeError, struct.error) as error:
        sys.exit(f'audio-module: {error}')
