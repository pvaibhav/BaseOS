#!/usr/bin/env python3
"""Host-only Mesa builder; run in this checkout, no devices or system installs."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import tarfile

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / 'work/mainline/graphics'
PINS = ROOT / 'graphics/mesa/pins.json'
CONTAINER = 'baseos-h700-mesa-build'

def run(*args):
    subprocess.run(args, check=True, cwd=ROOT)

def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def tree(path):
    h = hashlib.sha256()
    for p in sorted(path.rglob('*')):
        if p.is_symlink():
            value = 'link:' + str(p.readlink())
        elif p.is_file():
            value = digest(p)
        else:
            continue
        h.update((str(p.relative_to(path)) + '\0' + value + '\n').encode())
    return h.hexdigest()

def check_packages(actual):
    for line in (ROOT / 'graphics/mesa/container-packages.lock').read_text().splitlines():
        package, version = line.split('=', 1)
        if actual.get(package) != version:
            raise SystemExit('toolchain drift: ' + package)

def sources(pins):
    src = WORK / 'source'
    src.mkdir(parents=True, exist_ok=True)
    for pin in pins['sources']:
        name = pin['name']
        dest = src / name
        if not dest.exists():
            archive = WORK / (name + '.download')
            run('curl', '-fLsS', '--retry', '3', pin['url'], '-o', str(archive))
            assert digest(archive) == pin['sha256'], name + ' checksum mismatch'
            run('tar', '-xf', str(archive), '-C', str(src))
            assert dest.is_dir(), name
            archive.unlink()
        assert tree(dest) == pin['tree_sha256'], name + ' source drift'

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prepare', action='store_true', help='create isolated build container and install container-only tools')
    parser.add_argument('--rootfs', type=Path, default=Path('/Users/pvaibhav/Code/Me/BaseOS/work/rg28xx/rootfs.tar'))
    args = parser.parse_args()
    WORK.mkdir(parents=True, exist_ok=True)
    (WORK/'mesa-verification.json').unlink(missing_ok=True)
    pins = json.loads(PINS.read_text())
    sources(pins)
    if args.prepare:
        run('docker', 'run', '-d', '--name', CONTAINER, '-v', f'{ROOT}:/src', '-w', '/src', pins['container'], 'sleep', 'infinity')
        run('docker', 'exec', CONTAINER, 'sh', '/src/graphics/mesa/prepare.sh')
    info = json.loads(subprocess.check_output(['docker', 'inspect', CONTAINER]))[0]
    assert info['Image'] == pins['container_id'], 'unexpected base container'
    assert any(m['Destination'] == '/src' and Path(m['Source']).resolve() == ROOT and m['RW'] for m in info['Mounts']), 'container checkout mount mismatch'
    if not info['State']['Running']:
        run('docker', 'start', CONTAINER)
    run('docker', 'exec', CONTAINER, 'sh', '-c', 'dpkg-query -W > /src/work/mainline/graphics/container-packages.tsv')
    actual = dict(line.split('\t') for line in (WORK / 'container-packages.tsv').read_text().splitlines())
    check_packages(actual)
    frozen = subprocess.check_output(['docker', 'exec', CONTAINER, 'pip3', 'freeze'], text=True)
    assert set(frozen.splitlines()) == set((ROOT/'graphics/mesa/python-packages.lock').read_text().splitlines()), 'Python toolchain drift'
    (WORK/'python-packages.txt').write_text(frozen)
    run('docker', 'exec', CONTAINER, 'sh', '/src/graphics/mesa/build.sh')
    harvest = WORK / 'harvested'
    harvest.mkdir(exist_ok=True)
    # Only ABI providers, never the whole rootfs. Resolve tar links in-memory.
    harvested_members = {}
    with tarfile.open(args.rootfs) as archive:
        members = {m.name: m for m in archive}
        for name in ('libc.so.6', 'libm.so.6', 'libdl.so.2', 'libpthread.so.0', 'libgcc_s.so.1', 'libstdc++.so.6', 'ld-linux-aarch64.so.1'):
            member = members['./usr/lib/aarch64-linux-gnu/' + name]
            data = archive.extractfile(member).read()
            (harvest / name).write_bytes(data)
            harvested_members[member.name] = hashlib.sha256(data).hexdigest()
    (WORK/'harvested-abi.json').write_text(json.dumps({'archive':str(args.rootfs.resolve()),'members_sha256':harvested_members,'basis':'Harvested ABI, historical BaseOS 1.4.0; not fresh live-device proof'},indent=2)+'\n')
    run('docker', 'exec', CONTAINER, 'python3', '/src/graphics/mesa/verify.py')

if __name__ == '__main__':
    main()
