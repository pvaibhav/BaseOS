#!/usr/bin/env python3
"""Prepare and verify USB audio modules for vendor kernels built without them.

Some vendor kernels set CONFIG_SND_USB=y but leave CONFIG_SND_USB_AUDIO unset,
so USB DACs and headsets get no driver. For those kernels BaseOS builds the
driver as modules from the pinned 4.9.170 source and the vendor configuration.
Kernels with the driver built in ship nothing.

Targets with the same vendor configuration share one build in
work/usb-audio-build/. Their kernels export the same sound and USB symbols
with the same versions, and differ only in board-specific exports. The first
target to build supplies the symbol versions. Each target then checks the
modules against its own kernel, so a kernel that disagrees fails verification
instead of reusing modules built for another kernel.
"""
import argparse
import gzip
import hashlib
import json
from pathlib import Path
import struct
import sys

import audio_module
import kernel_abi

ROOT = Path(__file__).resolve().parent.parent
# Load order: each module depends only on the kernel and on modules before it.
MODULES = ['snd-hwdep.ko', 'snd-usbmidi-lib.ko', 'snd-usb-audio.ko']
# Enabling the driver may only change these options. olddefconfig on the
# vanilla tree also drops vendor-only options, so the comparison is against
# the vendor configuration after the same olddefconfig without the driver.
ALLOWED_CONFIG_CHANGES = {'CONFIG_SND_HWDEP', 'CONFIG_SND_USB_AUDIO',
                          'CONFIG_SND_USB_AUDIO_USE_MEDIA_CONTROLLER'}
SHN_UNDEF, SHN_ABS = 0, 0xfff1


def digest(blob):
    return hashlib.sha256(blob).hexdigest()


def config_values(text):
    """{option: value}; options that are not set have the value 'n'."""
    values = {}
    for line in text.splitlines():
        if line.startswith('CONFIG_') and '=' in line:
            name, value = line.split('=', 1)
            values[name] = value
        elif line.startswith('# CONFIG_') and line.endswith(' is not set'):
            values[line[2:-len(' is not set')]] = 'n'
    return values


def inputs(target):
    kernel, modules = kernel_abi.load_inputs(target)
    start = kernel.index(b'IKCFG_ST') + 8
    config = gzip.decompress(kernel[start:kernel.index(b'IKCFG_ED', start)])
    return kernel, modules, config


def needed(config):
    values = config_values(config.decode())
    if values.get('CONFIG_SND_USB_AUDIO', 'n') == 'y':
        return False
    if values.get('CONFIG_SND_USB') != 'y' or values.get('CONFIG_MODULES') != 'y':
        raise ValueError('kernel has no USB sound or module support')
    return True


def tool_files():
    return [Path(__file__), ROOT / 'build-usb-audio-modules.sh',
            ROOT / 'tools/audio_module.py', ROOT / 'tools/kernel_abi.py']


def stamp(target):
    kernel, _, config = inputs(target)
    h = hashlib.sha256(kernel + config)
    for path in tool_files():
        h.update(path.name.encode() + b'\0' + path.read_bytes())
    return h.hexdigest()


def symvers(kernel, modules):
    _, _, exports = audio_module.vendor_contract(kernel, modules)
    return ''.join(f'0x{crc:08x}\t{name}\tvmlinux\tEXPORT_SYMBOL\n'
                   for name, crc in sorted(exports.items()))


def build_dir(config):
    h = hashlib.sha256(config)
    for path in tool_files():
        h.update(path.name.encode() + b'\0' + path.read_bytes())
    return ROOT / 'work/usb-audio-build' / h.hexdigest()[:16]


def symbols(sections):
    """[(name, section index, value)] from a module's symbol table."""
    strings = sections['.strtab']
    return [(strings[name:strings.index(b'\0', name)].decode(), index, value)
            for name, info, other, index, value, size
            in struct.iter_unpack('<IBBHQQ', sections['.symtab']) if name]


def validate_config(baseline, effective):
    before, after = config_values(baseline.decode()), config_values(effective.decode())
    changed = {name for name in before.keys() | after.keys()
               if before.get(name, 'n') != after.get(name, 'n')}
    if after.get('CONFIG_SND_USB_AUDIO') != 'm':
        raise ValueError('build configuration does not enable CONFIG_SND_USB_AUDIO=m')
    if changed - ALLOWED_CONFIG_CHANGES:
        raise ValueError('enabling USB audio changes other options: '
                         + ', '.join(sorted(changed - ALLOWED_CONFIG_CHANGES)))


def validate_modules(blobs, kernel, modules):
    """Check the modules, in load order, against the vendor kernel."""
    vermagic, module_size, exports = audio_module.vendor_contract(kernel, modules)
    provided = {}  # symbol -> (CRC, providing module)
    for filename in MODULES:
        name = filename[:-3]
        sections = kernel_abi.elf_sections(blobs[filename])
        info = kernel_abi.modinfo(sections['.modinfo'])
        if info.get('vermagic') != vermagic:
            raise ValueError(f'{filename}: vermagic differs from vendor modules')
        if len(sections['.gnu.linkonce.this_module']) != module_size:
            raise ValueError(f'{filename}: struct module differs from vendor modules')
        depends = {item for item in info.get('depends', '').split(',') if item}
        imports = kernel_abi.modversions(sections['__versions'])
        table = symbols(sections)
        undefined = {symbol for symbol, index, _ in table if index == SHN_UNDEF}
        if not undefined.issubset(imports):
            raise ValueError(f'{filename}: unversioned imports: '
                             + ', '.join(sorted(undefined - imports.keys())))
        if 'module_layout' not in imports:
            raise ValueError(f'{filename}: no module_layout symbol version')
        for symbol, crc in imports.items():
            if symbol in provided:
                provider_crc, provider = provided[symbol]
                if provider_crc != crc:
                    raise ValueError(f'{filename}: {symbol} version differs from {provider}')
                if provider not in depends:
                    raise ValueError(f'{filename}: does not depend on {provider}')
            elif exports.get(symbol) != crc:
                raise ValueError(f'{filename}: {symbol} is not exported by the kernel '
                                 'with this version')
        if not depends.issubset(provider for _, provider in provided.values()):
            raise ValueError(f'{filename}: depends on a module not loaded before it')
        for symbol, index, value in table:
            if index == SHN_ABS and symbol.startswith('__crc_'):
                symbol = symbol[len('__crc_'):]
                if symbol in exports or symbol in provided:
                    raise ValueError(f'{filename}: {symbol} is already exported')
                provided[symbol] = (value, name)
    return len(provided)


def prepare(target):
    """Print the build directory when a build is needed."""
    kernel, modules, config = inputs(target)
    if not needed(config):
        return
    build = build_dir(config)
    if (build / '.built').is_file():
        return
    build.mkdir(parents=True, exist_ok=True)
    (build / 'kernel.config').write_bytes(config)
    (build / 'Module.symvers').write_text(symvers(kernel, modules))
    print(build)


def verify(target, output):
    current_stamp = stamp(target)
    kernel, modules, config = inputs(target)
    blobs = {}
    if needed(config):
        build = build_dir(config)
        if not (build / '.built').is_file():
            raise ValueError(f'{build}: build did not finish')
        validate_config((build / 'baseline.config').read_bytes(),
                        (build / 'effective.config').read_bytes())
        blobs = {name: (build / name).read_bytes() for name in MODULES}
        exported = validate_modules(blobs, kernel, modules)
    output.mkdir(parents=True, exist_ok=True)
    for path in output.glob('*.ko'):
        path.unlink()
    # Publish bytes first, then their integrity record. Interrupted publication
    # is a cache miss; a later build never trusts an unsealed output.
    for name, blob in blobs.items():
        (output / name).write_bytes(blob)
    record = {'target': target, 'stamp': current_stamp,
              'modules': {name: digest(blob) for name, blob in blobs.items()}}
    temporary = output / '.artifact.json.tmp'
    temporary.write_text(json.dumps(record) + '\n')
    temporary.replace(output / '.artifact.json')
    if blobs:
        print(f'{target}: USB audio modules verified ({len(blobs)} modules, '
              f'{exported} module exports)')
    else:
        print(f'{target}: USB audio driver is built into the vendor kernel')


def current(target, output):
    try:
        kernel, modules, _ = inputs(target)
        record = json.loads((output / '.artifact.json').read_text())
        if record['target'] != target or record['stamp'] != stamp(target):
            return False
        if sorted(path.name for path in output.glob('*.ko')) != sorted(record['modules']):
            return False
        blobs = {name: (output / name).read_bytes() for name in record['modules']}
        if any(digest(blob) != record['modules'][name] for name, blob in blobs.items()):
            return False
        # An intact digest is not an ABI proof: check against the current kernel.
        if blobs:
            validate_modules(blobs, kernel, modules)
    except (OSError, ValueError, KeyError, TypeError, struct.error):
        return False
    print(f'{target}: USB audio modules are current')
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('command', choices=['prepare', 'verify', 'current'])
    parser.add_argument('target')
    args = parser.parse_args()
    output = ROOT / 'work' / args.target / 'usb-audio'
    if args.command == 'current':
        return 0 if current(args.target, output) else 1
    if args.command == 'prepare':
        return prepare(args.target)
    return verify(args.target, output)


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (ValueError, KeyError, OSError, TypeError, struct.error) as error:
        sys.exit(f'usb-audio-modules: {error}')
