#!/usr/bin/env python3
"""Check the staged libraries against the BaseOS root."""
import hashlib
import json
import re
import subprocess
from pathlib import Path

W = Path('/src/work/v2/mesa-cache')
ROOT = Path('/src/work/mainline/alpha1/os/root')
OLD = ROOT / 'usr/lib/baseos-mainline'
NAMES = ['libEGL.so.1.0.0', 'libGLESv2.so.2.0.0',
         'libgallium-26.2.5.so', 'libgbm.so.1.0.0',
         'libdrm.so.2.125.0', 'gbm/dri_gbm.so']

def target(path):
    # Absolute links in a root filesystem refer to that root.
    for _ in range(40):
        if not path.is_symlink():
            return path
        link = path.readlink()
        path = ROOT / str(link).lstrip('/') if link.is_absolute() else path.parent / link
    raise RuntimeError('Too many symlinks: ' + str(path))

def versions(path, defined):
    result = set()
    for line in readelf(path, '--dyn-syms').splitlines():
        if (' UND ' not in line) == defined:
            result.update(re.findall(r'(?:GLIBC|ZLIB)_[0-9.]+', line))
    return result

def exports(path):
    result = set()
    for line in readelf(path, '--dyn-syms').splitlines():
        fields = line.split()
        if len(fields) >= 8 and fields[4] in ('GLOBAL', 'WEAK') and fields[5] in ('DEFAULT', 'PROTECTED') and fields[6] != 'UND':
            result.add(fields[7])
    return result

# Wide output keeps long versioned symbol names intact.
def readelf(path, flag):
    return subprocess.check_output(['readelf', '--wide', flag, str(path)], text=True)

def highest(items, prefix):
    values = [v for v in items if v.startswith(prefix)]
    return max(values, key=lambda v: tuple(map(int, v[len(prefix):].split('.'))), default=None)

providers = {}
for name in ('libc.so.6', 'libz.so.1'):
    path = target(ROOT / 'usr/lib/aarch64-linux-gnu' / name)
    providers[name] = {'path': str(path), 'versions': sorted(versions(path, True)),
                       'highest_glibc': highest(versions(path, True), 'GLIBC_'),
                       'highest_zlib': highest(versions(path, True), 'ZLIB_')}
available = set().union(*(set(p['versions']) for p in providers.values()))
report = {'providers': providers, 'files': {}, 'option_differences': {}}
errors = []
search = [ROOT / p for p in ('usr/lib/aarch64-linux-gnu', 'usr/lib/baseos-mainline', 'usr/lib', 'lib/aarch64-linux-gnu', 'lib')]
for name in NAMES:
    out = W / 'runtime' / name
    old = OLD / name
    elf = readelf(out, '-h')
    if 'AArch64' not in elf:
        errors.append(name + ': not AArch64')
    needed = re.findall(r'\(NEEDED\).*?\[(.*?)\]', readelf(out, '-d'))
    resolved = {}
    for dep in needed:
        matches = [target(d / dep) for d in search if target(d / dep).is_file()]
        matches = [p for p in matches if 'AArch64' in readelf(p, '-h')]
        if not matches:
            errors.append(name + ': missing root dependency ' + dep)
        resolved[dep] = [str(p) for p in matches]
    required = versions(out, False)
    missing = sorted(required - available)
    if missing:
        errors.append(name + ': missing symbol versions ' + str(missing))
    entry = {'bytes': out.stat().st_size, 'sha256': hashlib.sha256(out.read_bytes()).hexdigest(),
             'needed': resolved, 'required_versions': sorted(required), 'missing_versions': missing,
             'highest_glibc': highest(required, 'GLIBC_'), 'highest_zlib': highest(required, 'ZLIB_'),
             'root_matches_u4g': hashlib.sha256(old.read_bytes()).digest() == hashlib.sha256((Path('/src/work/mainline/u4g/devel/usr/lib') / name).read_bytes()).digest()}
    if name in ('libEGL.so.1.0.0', 'libGLESv2.so.2.0.0', 'libgbm.so.1.0.0'):
        before, after = exports(old), exports(out)
        entry['exports'] = {'old': len(before), 'new': len(after), 'missing': sorted(before - after)}
        if before - after:
            errors.append(name + ': missing exports')
    if not entry['root_matches_u4g']:
        errors.append(name + ': root differs from u4g')
    dynamic = readelf(out, '-d')
    entry['rpath'] = re.findall(r'\((?:RUNPATH|RPATH)\).*?\[(.*?)\]', dynamic)
    if any('/src/' in value for value in entry['rpath']):
        errors.append(name + ': build path in runtime search path')
    report['files'][name] = entry
report['cache_dir_string'] = b'MESA_SHADER_CACHE_DIR' in (W / 'runtime/libgallium-26.2.5.so').read_bytes()
if not report['cache_dir_string']:
    errors.append('Cache directory string missing')
def options(path):
    return {x['name']: x['value'] for x in json.loads(path.read_text())}
before = options(Path('/src/work/mainline/u4g/build-mesa/meson-info/intro-buildoptions.json'))
after = options(W / 'build/meson-info/intro-buildoptions.json')
report['option_differences'] = {k: {'old': before[k], 'new': after[k]} for k in before if before[k] != after.get(k)}
expected = {'pkg_config_path', 'shader-cache', 'zlib'}
if set(report['option_differences']) != expected:
    errors.append('Unexpected Meson option differences')
report['build_seconds'] = int((W / 'build-seconds.txt').read_text())
# Use the root's loader and disable its system cache for this check.
loader = target(ROOT / 'usr/lib/aarch64-linux-gnu/ld-linux-aarch64.so.1')
library_path = ':'.join(map(str, [W / 'runtime', W / 'stage/usr/lib', *search]))
loader_checks = {}
for name in NAMES:
    result = subprocess.run([str(loader), '--inhibit-cache', '--library-path', library_path,
                             '--list', str(W / 'runtime' / name)], text=True, capture_output=True)
    loader_checks[name] = {'returncode': result.returncode, 'stdout': result.stdout, 'stderr': result.stderr}
    if result.returncode:
        errors.append(name + ': root loader failed')
report['root_loader_checks'] = loader_checks
zlib_exports = {v.split('@')[0] for v in exports(target(ROOT / 'usr/lib/aarch64-linux-gnu/libz.so.1'))}
imports = set()
for line in readelf(W / 'runtime/libgallium-26.2.5.so', '--dyn-syms').splitlines():
    fields = line.split()
    if len(fields) >= 8 and fields[6] == 'UND':
        imports.add(fields[7].split('@')[0])
report['zlib_imports_provided_by_root'] = sorted(imports & zlib_exports)
report['errors'] = errors
(W / 'verification.json').write_text(json.dumps(report, indent=2) + '\n')
print(json.dumps(report, indent=2))
raise SystemExit(bool(errors))
