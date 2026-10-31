#!/usr/bin/env python3
"""Build the BaseOS 2 root image and boot payload.

  root:    work/v2/os/rootfs.sqfs  (written to card slot A, LBA 434176)
  payload: work/v2/os/image.bin    (kernel + DTB + first-stage initramfs, at p4+48MiB)

The root starts from the alpha1 staging tree (BaseOS 1.5.3 userspace plus the
Mesa/SDL graphics stack); its health and rescue machinery is replaced by
v2/os/overlay. Builds run in the v2-kbuild container (Linux tools, case-sensitive).
"""
import argparse, gzip, json, stat, subprocess, sys
from pathlib import Path

R = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(R / 'v2/dev')]
import build as dev  # payload helpers: write_newc, bol4, payload, android, G, PROVEN

OUT = R / 'work/v2/os'
BASE = 'work/mainline/alpha1/os/root'
DROP = ['usr/sbin/baseos-alpha-health', 'usr/sbin/baseos-alpha-usb', 'usr/sbin/baseos-rescue',
        'usr/sbin/baseos-rescue-usb', 'usr/sbin/baseos-rescue-monitor', 'usr/sbin/baseos-rescue-watchdog',
        'etc/baseos-phase.sh', 'etc/baseos-usb-cleanup.sh', 'etc/baseos-usb-development-serial']
DIRS = ['data', 'mnt/sdcard', 'proc', 'sys', 'dev', 'run', 'tmp', 'var', 'media']


def sh(script):
    subprocess.run(['docker', 'exec', 'v2-kbuild', 'bash', '-euc', script], check=True)


def static(src, out):
    """Static musl build for aarch64 (OrbStack runs arm64 natively)."""
    subprocess.run(['docker', 'run', '--rm', '-v', f'{src.parent}:/s:ro', '-v', f'{out.parent}:/o',
                    'alpine:3.20', 'sh', '-c',
                    f'apk add -q gcc musl-dev linux-headers libdrm-dev >/dev/null && '
                    f'gcc -static -Os -s -Wall -Werror -I/usr/include/libdrm -o /o/{out.name} /s/{src.name}'], check=True)


def root():
    OUT.mkdir(parents=True, exist_ok=True)
    tools = OUT / 'bin'; tools.mkdir(exist_ok=True)
    static(R / 'v2/dev/src/feeder.c', tools / 'baseos-feeder')
    static(R / 'v2/dev/src/mem.c', tools / 'mem')
    static(R / 'v2/dev/src/pad.c', tools / 'baseos-pad')
    static(R / 'v2/dev/src/firstframe.c', tools / 'baseos-firstframe')
    drop = ' '.join(DROP); dirs = ' '.join(DIRS)
    sh(f'''
rm -rf /k/root && mkdir /k/root && cp -a /repo/{BASE}/. /k/root/
cd /k/root
rm -f {drop}
cp -a /repo/v2/os/overlay/. .
install -m 755 /repo/work/v2/os/bin/baseos-feeder /repo/work/v2/os/bin/mem /repo/work/v2/os/bin/baseos-pad /repo/work/v2/os/bin/baseos-firstframe usr/sbin/
for d in {dirs}; do mkdir -p "$d"; done
test -x usr/sbin/adbd && test -x usr/bin/busybox
mksquashfs /k/root /k/rootfs.sqfs -noappend -all-root -comp lz4 -Xhc -b 131072 -quiet
cp /k/rootfs.sqfs /repo/work/v2/os/rootfs.sqfs
''')


def payload(cmdline_extra, dtb_path=None):
    static(R / 'v2/os/firststage.c', OUT / 'firststage')
    D = stat.S_IFDIR | 0o755
    initrd = gzip.compress(dev.write_newc([
        ('dev', D, b'', (0, 0)),
        ('dev/console', stat.S_IFCHR | 0o600, b'', (5, 1)),
        ('init', stat.S_IFREG | 0o755, (OUT / 'firststage').read_bytes(), (0, 0)),
    ]), compresslevel=9, mtime=0)
    boot, _ = dev.payload.parse(dev.PROVEN.read_bytes(), dev.G)
    segs = dev.android.split(boot)[0]
    dtb = Path(dtb_path or R / 'work/v2/kernel/rg28xx.dtb').read_bytes()
    if cmdline_extra:
        dtb = set_bootargs_extra(dtb, cmdline_extra)
    kernel = dev.bol4((R / 'work/v2/kernel/Image').read_bytes())
    image = dev.payload.pack(dev.android.join(boot[:2048], [kernel, initrd, b'', b'', segs[4]], b''), dtb, dev.G)
    (OUT / 'image.bin').write_bytes(image)
    print(json.dumps(dict(image=len(image), kernel=len(kernel), initrd=len(initrd), sha256=dev.sha(image))))


def set_bootargs_extra(dtb, extra):
    """Append to /chosen/bootargs (fdtget/fdtput: no shell quoting involved)."""
    tmp = OUT / 'tmp.dtb'; tmp.write_bytes(dtb)
    path = '/repo/work/v2/os/tmp.dtb'
    run = lambda *a: subprocess.run(['docker', 'exec', 'v2-kbuild', *a], check=True,
                                    capture_output=True, text=True).stdout.strip()
    run('fdtput', '-t', 's', path, '/chosen', 'bootargs', run('fdtget', path, '/chosen', 'bootargs') + ' ' + extra)
    out = tmp.read_bytes(); tmp.unlink()
    return out


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('what', choices=['root', 'payload', 'all'])
    ap.add_argument('--dev', action='store_true', help='host-heartbeat watchdog (baseos.dev)')
    ap.add_argument('--args', default='', help='extra kernel arguments')
    ap.add_argument('--dtb', help='DTB instead of work/v2/kernel/rg28xx.dtb')
    a = ap.parse_args()
    if a.what in ('root', 'all'):
        root()
    if a.what in ('payload', 'all'):
        payload(' '.join(x for x in ('baseos.dev' if a.dev else '', a.args) if x), a.dtb)
