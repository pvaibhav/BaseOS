#!/usr/bin/env python3
"""Build the RG28XX boot firmware for BaseOS 2: the capsule and firmware.bin.

    python3 v2/firmware/build.py e1     rebuild the running E1 firmware; fails unless
                                        every hash matches the files on the card
    python3 v2/firmware/build.py v2a    build v2a; v2a-ab is v2a without the panel change
    python3 v2/firmware/build.py all    every variant, E1 first
    python3 v2/firmware/build.py restamp IN OUT --variant v2a
                                        copy a BOS3 payload (image.bin) and set its
                                        generation to the variant's

Outputs go to work/v2/firmware/<variant>/, intermediates to work/v2/firmware/build/.
A stage is skipped when its inputs have not changed since the last run. Each
variant compiles a private copy of the sources in TREE_FILES with its patches
from v2/firmware/patches applied (VARIANTS below). Host checks: tests/run.py.

Boot chain and where each piece comes from:

  BROM loads 128 KiB from card LBA 16: the capsule (eGON image).
    capsule = A32 dispatcher + stock boot0 (64 KiB) + U-Boot SPL (48 KiB).
    The dispatcher reads the RTC GP14/GP15 pair. Nonzero: boot stock boot0.
    Zero: arm the watchdog (16 s), write cookie + pending, run the SPL.
  SPL = U-Boot v2026.10 SPL plus experiments/h700-boot-speed/spl/*.c.
    It trains DRAM, powers the panel, loads the logo, then reads firmware.bin
    (p4 + 40 MiB; size and FNV-1a are built into the SPL) and image.bin
    (p4 + 48 MiB; BOS3 envelope whose generation must equal the SPL's).
  firmware.bin = BL31 at offset 0 (loaded to 0x40000000) + helper at the next
    512-byte boundary (41472; loaded to 0x4a000000), padded to 512 bytes.
  helper (experiments/h700-mainline/helper, relinked with u3-join/wrap.c and
    u3/simplefb.c) shows the logo, unpacks the kernel, writes the FDT, enters BL31.
  BL31 = TF-A v2.15.0 + firmware/tf-a/patches, then Linux.

Generation: a 32-byte value built into the helper, SPL and capsule (cookie =
FNV-1a32 of it). E1's is pinned: it came from the old build's input identity.
Other variants hash their own inputs (sources after patching, TF-A pin and
patches, U-Boot archive, stock boot0, model policy, toolchains, and the recipe
below), so any input change gives a new generation and needs a new payload stamp.

Host tools: Docker with the pinned h700 toolchain image (TF-A and SPL), host
clang and the rustup ld.lld (helper and capsule), git. No device access.
"""
import argparse
import hashlib
import importlib.util
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import tarfile
from pathlib import Path

if sys.version_info < (3, 10):  # build-early-mmc.py, imported for the DRAM edits, needs 3.10
    newer = sorted(Path('/opt/homebrew/bin').glob('python3.1[0-9]'))
    if not newer:
        raise SystemExit('Python 3.10 or newer is required')
    os.execv(newer[-1], [str(newer[-1]), *sys.argv])

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
WORK = ROOT / 'work/v2/firmware'
PREPARED = Path('/Users/pvaibhav/Code/Me/BaseOS/work')
INVENTORY = PREPARED / 'h700-loader/native-logo-v1/fleet-v1/inventories/rg28xx'
UBOOT_ARCHIVE = PREPARED / 'h700-loader/mainline/u-boot-v2026.10.tar.gz'
UBOOT_SHA = 'f943a88389c8fd70a6b0d061a88267465e143c7ca6ea2b75837ab20f5b60c739'
HANDOFF_PACKAGE = PREPARED / 'rg28xx/boot-gzip/boot-package.bin'
TFA_GIT = ROOT / 'work/mainline/firmware/source'
TFA_PIN = json.loads((ROOT / 'firmware/tf-a/source.json').read_text())
IMAGE = 'ghcr.io/loveretro/h700-toolchain@' + TFA_PIN['toolchain_image'].split('@')[1]
TARGET = 'rg28xx'
OUTPUT_NAME = None
CONTAINER_CLANG = None

# Compiled sources, copied into a private tree per variant and patched there.
TREE_FILES = [
    'experiments/h700-boot-speed/spl/' + n for n in (
        'early-mmc.c', 'native-boot.c', 'native-handoff.h', 'native-logo.h', 'native-display.h',
        'native-panel.h', 'native-cache.c', 'native-cache-off.S', 'dram-select.c', 'dram-retry64.S')
] + [
    'experiments/h700-boot-speed/native/' + n for n in (
        'early-display.c', 'lz4-stream.c', 'lz4-stream.h', 'runtime.c', 'native-checks.h',
        'panel-probe.h', 'panel-spi9.h', 'boot-logo-pixels.h', 'boot-logo-file.h')
] + [
    'experiments/h700-boot-speed/native/capsule/' + n for n in (
        'entry.S', 'capsule.c', 'payloads.S', 'include/string.h')
] + [
    'experiments/h700-mainline/helper/' + n for n in (
        'entry64.S', 'helper.c', 'helper.h', 'fdt-final.c', 'panel.c', 'link.ld')
] + [
    'experiments/h700-mainline/u3-join/wrap.c', 'experiments/h700-mainline/u3/simplefb.c',
    'experiments/h700-mainline/u3/simplefb.h', 'experiments/h700-mainline/include/baseos-bl31-handoff.h',
    'src/native-attempt.h',
]
NATIVE = 'experiments/h700-boot-speed/native'
SPL = 'experiments/h700-boot-speed/spl'
HELPER = 'experiments/h700-mainline/helper'

# How everything is compiled. Part of every computed generation.
RECIPE = {
    'tfa_make': {k: v for k, v in TFA_PIN['make'].items()},
    'tfa_source_date_epoch': TFA_PIN['source_date_epoch'],
    'helper_flags': ['--target=aarch64-none-elf', '-O2', '-mgeneral-regs-only', '-mstrict-align',
                     '-ffreestanding', '-fno-builtin', '-fno-stack-protector', '-fno-pic',
                     '-ffunction-sections', '-fdata-sections'],
    'helper_link': ['--gc-sections', '-T', 'link.ld', '--wrap=baseos_u2_fdt'],
    'capsule_flags': ['--target=armv7a-none-eabi', '-march=armv8-a', '-mcpu=cortex-a53', '-marm',
                      '-mfloat-abi=soft', '-Oz', '-ffunction-sections', '-fdata-sections',
                      '-fno-stack-protector', '-fno-unwind-tables', '-fno-asynchronous-unwind-tables',
                      '-ffreestanding', '-fno-builtin', '-fstack-usage', '-DRAW_CAPSULE_ARM_PROTOCOL=1'],
    'spl_defconfig': 'anbernic_rg35xx_h700_defconfig',
    'spl_config': ['--set-val SPL_BSS_START_ADDR 0x30000 --set-val SPL_BSS_MAX_SIZE 0x4000 --disable SPL_MMC_WRITE',
                   '--disable AXP_DISABLE_BOOT_ON_POWERON'],
    'spl_source_date_epoch': 1790899200,
    'firmware_offset_mib': 40, 'image_offset_mib': 48,
}

E1_GENERATION = 'e1a64fac986574dbe2a1885805d11afef570a020c84a7022a18088ef567e7b38'
VARIANTS = {
    # The firmware that runs on the card today. Rebuilt only as a self-check.
    'e1': dict(generation=E1_GENERATION, tfa_log_level='20', tfa_patches=[], patches=[], expect={
        'bl31.bin': '46848d664393e5cbe41d138721015619fb7fc88f527e118598db96663f91612a',
        'helper.bin': '6acdc5f2a8e64ee8bc434d9338ca95dee9681a78ca8a1c3009d4fedacc282d5b',
        'firmware.bin': 'a8284d91f6d524e31450307f15c2fd4fb35cffb000eaea110e841ee089cd6661',
        'spl.bin': '709da32434f13317e856032a596af20008b5711b29fea0c64c96d67321635ce8',
        'capsule.egon.bin': '9f578917db15002c0e40f5adab0944b5a73dc6f944b8cad1b36ba92a764ee367',
    }),
    # E1 plus: BL31 silent (LOG_LEVEL 0) and no PMIC ban in BL31 or the helper;
    # the SPL powers the panel before the logo read and sends the panel's init
    # stream while it reads the card; the helper only finishes it.
    'v2a': dict(generation=None, tfa_log_level='0', tfa_patches=['tfa-allow-pmic.patch'],
                patches=['helper-allow-pmic.patch', 'early-panel.patch'], expect={}),
    # v2a without the early panel stream: the fallback if v2a's display start
    # misbehaves on hardware. Panel handling is exactly E1's.
    'v2-rgsp': dict(generation=None, tfa_log_level='0', tfa_patches=['tfa-allow-pmic.patch'],
                    patches=['helper-allow-pmic.patch', 'rgsp-dtb.patch'], expect={}),
    'v2a-ab': dict(generation=None, tfa_log_level='0', tfa_patches=['tfa-allow-pmic.patch'],
                   patches=['helper-allow-pmic.patch'], expect={}),
}

TARGETS = json.loads((HERE / 'targets.json').read_text())
# RG SP's original generator identity remains fixed for byte-compatible rebuilds.
RGSP_GENERATOR_HASHES = {'build.py': '43cb78fb61ddc86074898b6419e08cac89ca4be1404e2dc6bea8faa16922bcf8', 'targets.json': 'f4c62197283725c32948029ef2e0b8a1e82f753927b3dd4056238392bb9b0f5c', 'layout.py': '0084865a52baa42db533b9760b58bfc8a61e1d1bdf6c7bd488e0e1ad178ac8a3', 'Containerfile': 'dad377a7d6cfbf240ce2de29cb438bf46009bbf3a90b1688765a37ec67dcb550'}
for target in TARGETS:
    if target not in ('rg28xx', 'rgsp'):
        VARIANTS['v2-' + target] = dict(generation=None, tfa_log_level='0',
            tfa_patches=['tfa-allow-pmic.patch'],
            patches=['helper-allow-pmic.patch', 'model-dtb.patch'], expect={})


def sha(data): return hashlib.sha256(data).hexdigest()
def sha_file(path): return sha(Path(path).read_bytes())


def fnv(data):
    value = 2166136261
    for byte in data:
        value = (value ^ byte) * 16777619 & 0xffffffff
    return value


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run(*cmd, **kw):
    if CONTAINER_CLANG and str(cmd[0]) in ('/usr/bin/clang-18', '/usr/bin/ld.lld-18'):
        cwd = kw.pop('cwd', ROOT)
        kw.pop('env', None)
        cmd = ['docker', 'run', '--rm', '--network=none', '-v', f'{ROOT}:{ROOT}',
               '-w', str(cwd), CONTAINER_CLANG, *cmd]
    subprocess.run([str(c) for c in cmd], check=True, **kw)


def compiler_hash(path):
    if not CONTAINER_CLANG:
        return sha_file(path)
    return subprocess.check_output(['docker', 'run', '--rm', '--network=none', CONTAINER_CLANG,
                                    'sha256sum', str(path)], text=True).split()[0]


def log(msg): print(msg, flush=True)


def apply_patch(patch, directory):
    # git apply as a plain patch tool: the ceiling keeps it from treating the
    # worktree around work/ as the repository.
    env = dict(os.environ, GIT_CEILING_DIRECTORIES=str(Path(directory).parent))
    run('git', 'apply', '--verbose', patch, cwd=directory, env=env)


def canonical(value): return json.dumps(value, sort_keys=True, separators=(',', ':')).encode()


def cached(directory, inputs, build):
    """Run build(directory) unless directory/.inputs already records these inputs."""
    stamp = directory / '.inputs'
    key = sha(canonical(inputs))
    if stamp.is_file() and stamp.read_text() == key:
        return False
    if directory.exists():
        shutil.rmtree(directory)
    directory.mkdir(parents=True)
    build(directory)
    stamp.write_text(key)
    return True


def host_tools():
    clang = Path(shutil.which('clang')).resolve()
    lld = sorted((Path.home() / '.rustup/toolchains').glob('*/lib/rustlib/*/bin/gcc-ld/ld.lld'))[-1].resolve()
    env = dict(os.environ)
    for parent in lld.parents:
        if (parent / 'lib/libLLVM.dylib').is_file():
            env['DYLD_LIBRARY_PATH'] = str(parent / 'lib')
            break
    return clang, lld, env


def docker(mounts, script, network=False):
    args = ['docker', 'run', '--rm'] + ([] if network else ['--network=none'])
    for host, guest, mode in mounts:
        args += ['-v', f'{host}:{guest}:{mode}']
    run(*args, IMAGE, 'sh', '-euc', script)


# ---------------------------------------------------------------------------
# Inputs

def stage_tree(variant, out):
    """Copy TREE_FILES into out/ (repo layout) and apply the variant's patches."""
    for rel in TREE_FILES:
        (out / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / rel, out / rel)
    for name in VARIANTS[variant]['patches']:
        apply_patch(HERE / 'patches' / name, out)


def prepare_inventory():
    """Extract absent inventory fixtures from pinned prepared inputs, locally only."""
    global INVENTORY
    if INVENTORY.is_dir():
        return
    INVENTORY = WORK / 'build/inputs' / TARGET
    INVENTORY.mkdir(parents=True, exist_ok=True)
    with (PREPARED / TARGET / 'boot-prefix.img').open('rb') as source:
        prefix = source.read(1048576)
        (INVENTORY / 'prefix.bin').write_bytes(prefix)
        (INVENTORY / 'boot0.bin').write_bytes(prefix[8192:8192+65536])
        table, count, stride = struct.unpack_from('<QII', prefix, 584)
        for i in range(count):
            entry = prefix[table*512+i*stride:table*512+(i+1)*stride]
            if entry[56:128].decode('utf-16-le').rstrip('\0') == 'env':
                source.seek(struct.unpack_from('<Q', entry, 32)[0]*512)
                (INVENTORY / 'env.bin').write_bytes(source.read(131072))
                break
        else:
            raise ValueError('stock environment partition absent')


def model_policy():
    native = load('v2fw_native_policy', ROOT / NATIVE / 'build.py')
    native.INPUT_WORK = PREPARED
    policy = native.model_policy(TARGET, INVENTORY)
    spec = json.loads((HERE / 'targets.json').read_text())[TARGET]
    assert sha_file(INVENTORY / 'boot0.bin') == spec['boot0_sha256']
    assert policy['package_sha256'] == spec['package_sha256']
    assert policy['model_id'] == spec['model_id']
    assert [policy['boot_logo'][k] for k in ('width', 'height')] == spec['logo']
    assert policy['boot_logo']['framebuffer_from_ram_end'] == spec['framebuffer_from_ram_end']
    assert (policy['panel_rule'], policy['panel_pull_up']) == (spec['panel_rule'], spec['pull_up'])
    return policy


def string_shim():
    text = (ROOT / NATIVE / 'build.py').read_text()
    start = text.index('#ifndef BASEOS_STRING_H')
    return text[start:text.index('""")', start)]


def generation_for(variant, tree, policy, clang, lld):
    fixed = VARIANTS[variant]['generation']
    if fixed:
        return bytes.fromhex(fixed)
    identity = {
        'variant': variant, 'target': TARGET, 'recipe': RECIPE,
        'tfa_log_level': VARIANTS[variant]['tfa_log_level'],
        'sources': {rel: sha_file(tree / rel) for rel in TREE_FILES},
        'tfa': {'commit': TFA_PIN['commit'], 'tree': TFA_PIN['tree'],
                'patches': {n: sha_file(p) for n, p in tfa_patches(variant)}},
        'display_tables': {n: sha_file(ROOT / NATIVE / n) for n in
                           ('build-display.py', 'panel-init.json', 'display-engine.json', 'display-handoff.json')},
        'model_policy': policy, 'uboot_archive': UBOOT_SHA,
        'stock_boot0': sha_file(INVENTORY / 'boot0.bin'), 'handoff_package': sha_file(HANDOFF_PACKAGE),
        'spl_dram_edits': sha_file(ROOT / SPL / 'build-early-mmc.py'),
        'toolchains': {'image': IMAGE, 'clang': compiler_hash(clang), 'lld': compiler_hash(lld)},
    }
    if TARGET != 'rg28xx':
        identity['target_generator'] = (RGSP_GENERATOR_HASHES if TARGET == 'rgsp' else
            {n: sha_file(HERE / n) for n in ('build.py', 'targets.json', 'layout.py', 'Containerfile')})
        identity['compiler_image'] = CONTAINER_CLANG
        identity['policy_generators'] = {n: sha_file(ROOT / n) for n in
            ('devices.json', 'tools/device_profile.py', NATIVE + '/build.py',
             NATIVE + '/panel-contract.json', NATIVE + '/vendor-handoff.json',
             'experiments/h700-boot-speed/evidence/compatibility.json')}
    generation = hashlib.sha256(canonical(identity)).digest()
    assert fnv(generation), 'zero cookie: change any input'
    return generation


def tfa_patches(variant):
    base = [(n, ROOT / 'firmware/tf-a/patches' / n) for n in TFA_PIN['patches']]
    return base + [(n, HERE / 'patches' / n) for n in VARIANTS[variant]['tfa_patches']]


# ---------------------------------------------------------------------------
# Stages

def build_bl31(variant, tree, build):
    """TF-A BL31 in the pinned toolchain image."""
    make = dict(TFA_PIN['make'], LOG_LEVEL=VARIANTS[variant]['tfa_log_level'])
    inputs = {'commit': TFA_PIN['commit'], 'make': make,
              'patches': {n: sha_file(p) for n, p in tfa_patches(variant)},
              'handoff_h': sha_file(tree / 'experiments/h700-mainline/include/baseos-bl31-handoff.h'),
              'image': IMAGE}

    def stage(out):
        src = out / 'cache/source-u2'
        src.mkdir(parents=True)
        archive = subprocess.run(['git', '-C', TFA_GIT, 'archive', TFA_PIN['commit']],
                                 check=True, capture_output=True).stdout
        run('tar', '-x', '-C', src, input=archive)
        for name, path in tfa_patches(variant):
            apply_patch(path, src)
        shutil.copytree(tree / 'experiments/h700-mainline/include', out / 'include')
        args = ' '.join(f"'{k}={v}'" for k, v in make.items())
        docker([(out, '/out', 'rw')], f'''
unset CC AS LD AR OC OD NM OBJCOPY OBJDUMP CFLAGS LDFLAGS CPPFLAGS MAKEFLAGS
export SOURCE_DATE_EPOCH={TFA_PIN['source_date_epoch']} LC_ALL=C TZ=UTC
make -C /out/cache/source-u2 -j8 BUILD_BASE=/out/build CROSS_COMPILE=aarch64-linux-gnu- \
  BASEOS_INCLUDE=/out/include {args} bl31 > /out/build.log 2>&1''')
        built = out / 'build/sun50i_h616/release'
        shutil.copyfile(built / 'bl31.bin', out / 'bl31.bin')
        shutil.copyfile(built / 'bl31/bl31.elf', out / 'bl31.elf')

    if cached(build / 'tfa', inputs, stage):
        log(f'  bl31.bin built (LOG_LEVEL={make["LOG_LEVEL"]})')
    return build / 'tfa'


def build_helper(tree, build, policy, generation, clang, lld, env):
    """Helper: EL3 stage at 0x4a000000. Host clang, rustup ld.lld."""
    h, nat, spl = tree / HELPER, tree / NATIVE, tree / SPL
    inputs = {'sources': {rel: sha_file(tree / rel) for rel in TREE_FILES}, 'generation': generation.hex(),
              'policy': policy, 'recipe': RECIPE, 'clang': compiler_hash(clang), 'lld': compiler_hash(lld)}

    def stage(out):
        lib = out / 'libfdt'
        lib.mkdir()
        with tarfile.open(UBOOT_ARCHIVE) as tar:
            for m in tar.getmembers():
                if m.isfile() and m.name.startswith('u-boot-2026.10/scripts/dtc/libfdt/'):
                    (lib / Path(m.name).name).write_bytes(tar.extractfile(m).read())
        shim = out / 'include'
        shim.mkdir()
        (shim / 'string.h').write_text(string_shim())
        (shim / 'stdlib.h').write_text('#include <stddef.h>\n')
        display = load('v2fw_display', ROOT / NATIVE / 'build-display.py')
        display.build(out, TARGET, policy, generation)
        (out / 'native-generated.h').write_text('#include "early-display-generated.h"\n'
                                                f'#define BASEOS_MODEL_ID {policy["model_id"]}u\n')
        if TARGET not in ('rg28xx', 'rgsp'):
            with (out / 'native-generated.h').open('a') as header:
                header.write('#define BASEOS_DTB_COMPATIBLE "' + TARGETS[TARGET]['compatible'] + '"\n')
        u3 = load('v2fw_u3', HERE / 'layout.py')
        (out / 'u3-layout.h').write_text(u3.layout_header({'model_policy': policy}))
        include = tree / 'experiments/h700-mainline/include'
        sources = [h / 'entry64.S', h / 'helper.c', h / 'fdt-final.c', h / 'panel.c', nat / 'early-display.c',
                   nat / 'lz4-stream.c', nat / 'runtime.c']
        sources += [lib / n for n in ('fdt.c', 'fdt_check.c', 'fdt_ro.c', 'fdt_rw.c', 'fdt_sw.c', 'fdt_wip.c',
                                      'fdt_empty_tree.c', 'fdt_strerror.c', 'fdt_addresses.c')]
        dirs = [out, shim, lib, h, nat, spl, include]
        dirs_u3 = [out, shim, lib, h, tree / 'experiments/h700-mainline/u3', nat, spl, include]
        objects = []
        for i, src in enumerate(sources):
            objects.append(out / f'{i}.o')
            run(clang, *RECIPE['helper_flags'], *[f'-I{d}' for d in dirs], '-c', src, '-o', objects[-1])
        for name, src in (('u3-wrap', tree / 'experiments/h700-mainline/u3-join/wrap.c'),
                          ('u3-simplefb', tree / 'experiments/h700-mainline/u3/simplefb.c')):
            objects.append(out / f'{name}.o')
            run(clang, *RECIPE['helper_flags'], *[f'-I{d}' for d in dirs_u3], '-c', src, '-o', objects[-1])
        link = [lld, *(h / a if a == 'link.ld' else a for a in RECIPE['helper_link'])]
        run(*link, *objects, '-o', out / 'helper.elf', env=env)
        run(*link, '--oformat=binary', *objects, '-o', out / 'helper.bin', env=env)
        elf = (out / 'helper.elf').read_bytes()
        assert struct.unpack_from('<Q', elf, 24)[0] == 0x4a000000, 'helper entry'
        phoff = struct.unpack_from('<Q', elf, 32)[0]
        stride, count = struct.unpack_from('<HH', elf, 54)
        for at in range(phoff, phoff + stride * count, stride):
            kind, _, _, _, pa, _, memsz, _ = struct.unpack_from('<II6Q', elf, at)
            assert kind != 1 or 0x4a000000 <= pa <= pa + memsz <= 0x4a0e0000, 'helper outside its arena'

    if cached(build / 'helper', inputs, stage):
        log('  helper.bin built')
    return build / 'helper'


def build_firmware(bl31, helper):
    """firmware.bin: BL31 then helper, each padded to 512 bytes."""
    blob, regions = bytearray(), []
    for name, address, data in (('bl31', 0x40000000, bl31), ('helper', 0x4a000000, helper)):
        assert 0 < len(data) <= (0x40000 if name == 'bl31' else 0xe0000)
        regions.append(dict(name=name, offset=len(blob), bytes=len(data), destination=address))
        blob += data + bytes(-len(data) % 512)
    assert len(blob) <= 0x100000
    return bytes(blob), regions


def spl_header(policy, generation, firmware, regions):
    """native-generated.h for the SPL: model, logo, firmware size/FNV, generation."""
    logo = policy['boot_logo']
    values = {'MODEL_ID': policy['model_id'], 'PANEL_RULE': policy['panel_rule'],
              'PANEL_PULL_UP': int(policy['panel_pull_up']),
              'LOGO_PARTITION_SECTOR': logo['partition_lba'], 'LOGO_PARTITION_SECTORS': logo['partition_sectors'],
              'LOGO_WIDTH': logo['width'], 'LOGO_HEIGHT': logo['height'],
              'LOGO_RAM_END_OFFSET': logo['framebuffer_from_ram_end'], 'IMAGE_BYTES': 0, 'IMAGE_FNV': 0,
              'FIRMWARE_BYTES': len(firmware), 'FIRMWARE_FNV': fnv(firmware)}
    text = '#define BASEOS_MAINLINE 1\n#define BASEOS_OPEN_FIRMWARE 1\n'
    text += ''.join(f'#define BASEOS_{k} {v}u\n' for k, v in values.items())
    text += 'static const unsigned char baseos_generation[32] = {' + ','.join(map(str, generation)) + '};\n'
    text += 'struct baseos_firmware_extent { unsigned int offset, bytes, destination; };\n'
    text += 'static const struct baseos_firmware_extent baseos_firmware_extents[2] = {\n'
    text += ''.join(f"{{{r['offset']}u,{r['bytes']}u,0x{r['destination']:x}u}},\n" for r in regions) + '};\n'
    lba = policy['boot_partition_lba']
    text += (f"\n#define BASEOS_FIRMWARE_SECTOR {lba + RECIPE['firmware_offset_mib'] * 2048}ul\n"
             f"#define BASEOS_IMAGE_SECTOR {lba + RECIPE['image_offset_mib'] * 2048}ul\n")
    return text


def panel_stream_header(helper_dir, policy):
    """The panel init stream(s) for the SPL, cut from the helper's generated header."""
    text = (helper_dir / 'early-display-generated.h').read_text()
    names = ('old', 'boe') if policy['panel_rule'] == 2 else ('boe' if policy['panel_rule'] else 'old',)
    lines = [l for l in text.splitlines() if any(l.startswith(f'static const uint16_t baseos_panel_{n}[]')
                                                 for n in names)]
    assert len(lines) == len(names)
    return '/* Generated by v2/firmware/build.py from panel-init.json. */\n' + '\n'.join(lines) + '\n'


def build_spl(variant, tree, build, policy, generation, firmware, regions, helper_dir):
    """U-Boot v2026.10 SPL with the BaseOS DRAM, card-read and handoff code."""
    boot0 = (INVENTORY / 'boot0.bin').read_bytes()
    assert len(boot0) == 65536 and boot0[4:12] == b'eGON.BT0'
    package = HANDOFF_PACKAGE.read_bytes()
    assert len(package) == 0x140000
    cookie = fnv(generation)
    header = spl_header(policy, generation, firmware, regions)
    stream = panel_stream_header(helper_dir, policy) if 'early-panel.patch' in VARIANTS[variant]['patches'] else None
    flags = (f'-DBASEOS_BOOT0_FNV=0x{fnv(boot0):08x}u -DBASEOS_DRAM_CONTROL=1 -DBASEOS_VENDOR_HANDOFF=1 '
             f'-DBASEOS_TOC_FNV=0x{fnv(package):08x}u -DBASEOS_OPEN_FIRMWARE=1 -DBASEOS_RAW_CAPSULE=1')
    inputs = {'sources': {rel: sha_file(tree / rel) for rel in TREE_FILES}, 'header': header, 'stream': stream,
              'flags': flags, 'cookie': cookie, 'recipe': RECIPE, 'boot0': sha(boot0), 'image': IMAGE,
              'dram_edits': sha_file(ROOT / SPL / 'build-early-mmc.py')}
    debs = deb_cache()

    def stage(out):
        assert sha_file(UBOOT_ARCHIVE) == UBOOT_SHA
        with tarfile.open(UBOOT_ARCHIVE) as tar:  # pinned by hash above
            tar.extractall(out, **({'filter': 'data'} if hasattr(tarfile, 'data_filter') else {}))
        src = out / 'u-boot-2026.10'
        arch = src / 'arch/arm/mach-sunxi'
        makefile = arch / 'Makefile'
        spl = tree / SPL
        # Same edits, in the same order, as experiments/h700-boot-speed/spl/build-early-mmc.py
        # with --dram --handoff-package --native-loader --raw-capsule --open-firmware.
        shutil.copyfile(spl / 'early-mmc.c', arch / 'baseos_early_mmc.c')
        makefile.write_text(makefile.read_text() + '\nifdef CONFIG_XPL_BUILD\nobj-y += baseos_early_mmc.o\nendif\n')
        edits = load('v2fw_spl_edits', ROOT / SPL / 'build-early-mmc.py')
        edits.HERE = spl  # dram-select.c and dram-retry64.S come from the variant tree
        edits.native_dram(src, boot0)
        if TARGET != 'rg28xx':
            dram = arch / 'dram_sun50i_h616.c'
            text = dram.read_text()
            loop = 'while ((readl(SUNXI_DRAM_PHY0_BASE + 0x184) & val) != val) {'
            assert text.count(loop) == 2
            text = text.replace(loop, 'for (unsigned int budget=1000000; '
                '(readl(SUNXI_DRAM_PHY0_BASE + 0x184) & val) != val;) {\n'
                '\t\tif (!--budget) dram_panic("read calibration timeout");')
            dram.write_text(text)
        makefile.write_text(makefile.read_text() + '\nifdef CONFIG_XPL_BUILD\nobj-y += baseos_vendor_handoff.o '
                            'native-cache.o native-cache-off.o\nendif\n')
        shutil.copyfile(spl / 'native-boot.c', arch / 'baseos_vendor_handoff.c')
        for name in ('native-handoff.h', 'native-logo.h', 'native-display.h', 'native-panel.h',
                     'native-cache.c', 'native-cache-off.S'):
            shutil.copyfile(spl / name, arch / name)
        shutil.copyfile(tree / NATIVE / 'boot-logo-file.h', arch / 'boot-logo-file.h')
        (arch / 'native-generated.h').write_text(header)
        if stream:
            (arch / 'native-panel-stream.h').write_text(stream)
            shutil.copyfile(tree / NATIVE / 'panel-spi9.h', arch / 'panel-spi9.h')
        shutil.copyfile(tree / 'src/native-attempt.h', arch / 'persistent-attempt.h')
        (arch / 'raw-capsule-generated.h').write_text(
            f'#define BASEOS_ATTEMPT_COOKIE 0x{cookie:08x}u\n'
            'static const unsigned char baseos_raw_board[8] = {' + ','.join(map(str, boot0[0xf0:0xf8])) + '};\n')
        board = arch / 'board.c'
        body = board.read_text()
        entry = 'void board_init_f(ulong dummy)\n{\n'
        assert body.count(entry) == body.count('\tspl_init();') == body.count('\tsunxi_board_init();') == 1
        body = body.replace(entry, entry + '\textern char __bss_start[], __bss_end[];\n\textern void baseos_early_mmc(void);\n'
                            '\textern void baseos_raw_adopt(void);\n\tbaseos_raw_adopt();\n'
                            '\tmemset(__bss_start, 0, __bss_end - __bss_start);\n')
        body = body.replace('\tspl_init();', '\tspl_early_init();')
        body = body.replace('\tsunxi_board_init();', '\tbaseos_early_mmc();\n\tsunxi_board_init();')
        board.write_text(body)
        config = '\n'.join(f'scripts/config {c}' for c in RECIPE['spl_config'])
        docker([(out, '/build', 'rw'), (debs, '/debs', 'ro')], f'''
dpkg -i /debs/*.deb > /build/deps.log 2>&1
unset PKG_CONFIG_SYSROOT_DIR PKG_CONFIG_PATH CC CXX LD AR AS CPP ARCH
export SOURCE_DATE_EPOCH={RECIPE['spl_source_date_epoch']}
cd /build/u-boot-2026.10
make CROSS_COMPILE=aarch64-linux-gnu- {RECIPE['spl_defconfig']} > ../configure.log 2>&1
{config}
make CROSS_COMPILE=aarch64-linux-gnu- olddefconfig >> ../configure.log 2>&1
make NO_PYTHON=1 HOSTCC=/usr/bin/gcc HOSTCXX=/usr/bin/g++ CROSS_COMPILE=aarch64-linux-gnu- KCFLAGS='{flags}' -j8 spl/u-boot-spl.bin > ../build.log 2>&1
aarch64-linux-gnu-nm -n spl/u-boot-spl > ../symbols.txt
aarch64-linux-gnu-objdump -d spl/u-boot-spl > ../spl.dis''')
        blob = (src / 'spl/sunxi-spl.bin').read_bytes()
        assert len(blob) == 0xc000 and blob[4:12] == b'eGON.BT0', 'SPL must be a 48 KiB eGON image'
        symbols = (out / 'symbols.txt').read_text()
        end = int(re.search(r'^([0-9a-f]+) [A-Za-z] __image_copy_end$', symbols, re.M)[1], 16)
        assert 0x2c000 - end >= 1024, f'SPL needs 1 KiB slack below 0x2c000 (ends at {end:#x})'
        (out / 'spl.bin').write_bytes(blob)

    if cached(build / 'spl', inputs, stage):
        log('  spl.bin built')
    return build / 'spl'


def deb_cache():
    """flex/bison/bc/... for the U-Boot host tools; downloaded once, then installed offline."""
    debs = WORK / 'build/debs'
    if not any(debs.glob('*.deb')):
        debs.mkdir(parents=True, exist_ok=True)
        docker([(debs, '/debs', 'rw')], 'apt-get update -qq && apt-get install -y -qq --download-only '
               'flex bison bc libelf-dev libssl-dev libgnutls28-dev > /dev/null && '
               'cp /var/cache/apt/archives/*.deb /debs/', network=True)
    return debs


def build_capsule(tree, build, spl_bin, generation, clang, lld, env):
    """128 KiB eGON capsule: dispatcher at 0x20400, stock boot0 at 0x21000, SPL at 0x31000."""
    stock = (INVENTORY / 'boot0.bin').read_bytes()
    cookie = fnv(generation)
    assert cookie, 'zero cookie'
    cap = tree / 'experiments/h700-boot-speed/native/capsule'
    inputs = {'sources': {rel: sha_file(tree / rel) for rel in TREE_FILES}, 'spl': sha(spl_bin),
              'stock': sha(stock), 'cookie': cookie, 'recipe': RECIPE, 'clang': compiler_hash(clang),
              'lld': compiler_hash(lld)}

    def stage(out):
        (out / 'stock.bin').write_bytes(stock)
        (out / 'native.bin').write_bytes(spl_bin)
        header = bytearray(stock[:0x400])
        struct.pack_into('<I', header, 0, 0xea0000fe)  # ARM branch to 0x20400
        (out / 'capsule-header.bin').write_bytes(header)
        (out / 'config-values.h').write_text(
            f'#define NATIVE_FNV 0x{fnv(spl_bin):08x}u\n#define STOCK_FNV 0x{fnv(stock):08x}u\n'
            f'#define CAPSULE_COOKIE 0x{cookie:08x}u\n')
        flags = RECIPE['capsule_flags'] + ['-I' + str(cap / 'include'), '-I' + str(tree / 'src'),
                                           '-include', str(out / 'config-values.h')]
        capsule_build = (ROOT / 'experiments/h700-boot-speed/native/capsule/build.py').read_text()
        linker = capsule_build[capsule_build.index('linker = """') + 12:capsule_build.index('"""\n    (output')]
        (out / 'capsule.ld').write_text(linker)
        objects = []
        for name in ('entry.S', 'capsule.c', 'payloads.S'):
            objects.append(out / (name + '.o'))
            run(clang, *flags, '-c', cap / name, '-o', objects[-1], cwd=out)
        for binary, name in ((False, 'capsule.elf'), (True, 'capsule.bin')):
            run(lld, '--gc-sections', '-T', out / 'capsule.ld', '-Map=' + str(out / 'capsule.map'),
                *(['--oformat=binary'] if binary else []), *objects, '-o', out / name, env=env)
        blob = bytearray((out / 'capsule.bin').read_bytes())
        assert len(blob) == 0x1d000
        blob += bytes(0x20000 - len(blob))
        struct.pack_into('<II', blob, 12, 0x5f0a6c39, len(blob))
        struct.pack_into('<I', blob, 12, sum(struct.unpack('<32768I', blob)) & 0xffffffff)
        (out / 'capsule.egon.bin').write_bytes(blob)

    if cached(build / 'capsule', inputs, stage):
        log('  capsule.egon.bin built')
    # The fields experiments/h700-boot-speed/native/capsule/verify.py reads.
    blob = (build / 'capsule/capsule.egon.bin').read_bytes()
    (build / 'capsule/manifest.json').write_text(json.dumps(dict(
        raw_image_sha256=sha(blob), generation=generation.hex(), generation_cookie=cookie,
        normal_protocol_compiled=True, diagnostic=False), indent=2) + '\n')
    return build / 'capsule'


# ---------------------------------------------------------------------------

def build_variant(variant):
    log(f'== {variant}')
    build = WORK / 'build' / variant
    tree = build / 'tree'
    if tree.exists():
        shutil.rmtree(tree)
    stage_tree(variant, tree)
    clang, lld, env = host_tools() if TARGET == 'rg28xx' else (Path('/usr/bin/clang-18'), Path('/usr/bin/ld.lld-18'), dict(os.environ))
    policy = model_policy()
    generation = generation_for(variant, tree, policy, clang, lld)
    cookie = fnv(generation)
    log(f'  generation {generation.hex()}  cookie 0x{cookie:08x}')
    tfa = build_bl31(variant, tree, build)
    helper = build_helper(tree, build, policy, generation, clang, lld, env)
    bl31 = (tfa / 'bl31.bin').read_bytes()
    firmware, regions = build_firmware(bl31, (helper / 'helper.bin').read_bytes())
    spl = build_spl(variant, tree, build, policy, generation, firmware, regions, helper)
    spl_bin = (spl / 'spl.bin').read_bytes()
    capsule = build_capsule(tree, build, spl_bin, generation, clang, lld, env)

    out = WORK / (OUTPUT_NAME or variant)
    out.mkdir(parents=True, exist_ok=True)
    files = {'bl31.bin': tfa / 'bl31.bin', 'helper.bin': helper / 'helper.bin', 'spl.bin': spl / 'spl.bin',
             'capsule.egon.bin': capsule / 'capsule.egon.bin'}
    for name, path in files.items():
        shutil.copyfile(path, out / name)
    (out / 'firmware.bin').write_bytes(firmware)
    lba = policy['boot_partition_lba']
    capsule_blob = (out / 'capsule.egon.bin').read_bytes()
    manifest = {
        'variant': variant, 'target': TARGET, 'generation': generation.hex(), 'cookie': f'0x{cookie:08x}',
        'files': {n: {'bytes': (out / n).stat().st_size, 'sha256': sha_file(out / n), 'fnv': f'0x{fnv((out / n).read_bytes()):08x}'}
                  for n in sorted([*files, 'firmware.bin'])},
        'firmware_regions': regions,
        'card': {'capsule': {'device': '/dev/mmcblk0', 'lba': 16, 'sectors': len(capsule_blob) // 512},
                 'firmware': {'device': '/dev/mmcblk0p4', 'sector': RECIPE['firmware_offset_mib'] * 2048,
                              'disk_lba': lba + RECIPE['firmware_offset_mib'] * 2048, 'sectors': len(firmware) // 512},
                 'image': {'device': '/dev/mmcblk0p4', 'sector': RECIPE['image_offset_mib'] * 2048,
                           'disk_lba': lba + RECIPE['image_offset_mib'] * 2048}},
        'p4_first_lba': lba,
    }
    (out / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    (out / 'generation.txt').write_text(generation.hex() + '\n')
    bad = {n: manifest['files'][n]['sha256'] for n, h in VARIANTS[variant]['expect'].items()
           if manifest['files'][n]['sha256'] != h}
    for name in manifest['files']:
        expected = VARIANTS[variant]['expect'].get(name)
        mark = '' if not expected else ('  MATCH' if expected == manifest['files'][name]['sha256'] else '  DIFFERENT')
        log(f"  {name:17} {manifest['files'][name]['bytes']:7} {manifest['files'][name]['sha256']}{mark}")
    if bad:
        raise SystemExit(f'{variant}: output differs from the pinned hashes: {sorted(bad)}')
    return manifest


def restamp(source, target, variant):
    """Rewrite a BOS3 payload's generation; kernel, initramfs and DTB bytes stay as they are."""
    sys.path.insert(0, str(ROOT / 'tools'))
    import mainline_payload as payload
    data = Path(source).read_bytes()
    android, dtb = payload.parse(data, data[32:64])
    generation = bytes.fromhex((WORK / variant / 'generation.txt').read_text().strip())
    out = payload.pack(android, dtb, generation)
    Path(target).write_bytes(out)
    log(f'{target}: {len(out)} bytes, generation {generation.hex()}, sha256 {sha(out)}')


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('what', choices=[*VARIANTS, 'all', 'restamp'])
    parser.add_argument('paths', nargs='*', help='restamp: IN OUT')
    parser.add_argument('--target', choices=list(TARGETS), default='rg28xx')
    parser.add_argument('--output-name', help='private comparison output directory')
    parser.add_argument('--variant', default='v2a', choices=list(VARIANTS))
    args = parser.parse_args()
    global TARGET, INVENTORY, HANDOFF_PACKAGE, OUTPUT_NAME, CONTAINER_CLANG
    TARGET = args.target
    OUTPUT_NAME = args.output_name
    if OUTPUT_NAME and not re.fullmatch(r'[a-zA-Z0-9-]+', OUTPUT_NAME):
        parser.error('output-name must be a simple directory name')
    INVENTORY = INVENTORY.parent / TARGET
    HANDOFF_PACKAGE = PREPARED / TARGET / 'boot-gzip/boot-package.bin'
    if TARGET != 'rg28xx':
        if args.what != 'v2-' + TARGET:
            parser.error(TARGET + ' requires variant v2-' + TARGET)
        CONTAINER_CLANG = subprocess.check_output(['docker', 'image', 'inspect',
            'baseos-v2-firmware-clang:local', '--format', '{{.Id}}'], text=True).strip()
    elif args.what.startswith('v2-'):
        parser.error(args.what + ' requires its matching --target')
    prepare_inventory()
    if args.what == 'restamp':
        if len(args.paths) != 2:
            parser.error('restamp needs IN and OUT')
        restamp(*args.paths, args.variant)
        return
    for variant in ([v for v in VARIANTS if not v.startswith('v2-')] if args.what == 'all' else [args.what]):
        build_variant(variant)


if __name__ == '__main__':
    main()
