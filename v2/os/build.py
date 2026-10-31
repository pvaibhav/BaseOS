#!/usr/bin/env python3
"""Build the BaseOS 2 root image and boot payload.

  root:    work/v2/os/rootfs.sqfs  (written to card slot A, LBA 434176)
  payload: work/v2/os/image.bin    (kernel + DTB + first-stage initramfs with the boot logo, at p4+48MiB)

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
LOGO = Path('/Users/pvaibhav/Code/Me/BaseOS/work/rg28xx/bootlogo.bmp')  # the loader's logo
DIRS = ['data', 'mnt/sdcard', 'proc', 'sys', 'dev', 'run', 'tmp', 'var', 'media']


def sh(script):
    subprocess.run(['docker', 'exec', 'v2-kbuild', 'bash', '-euc', script], check=True)


MUSL = 'baseos-v2-musl:local'


def static(src, out):
    """Static musl build for aarch64 (OrbStack runs arm64 natively). The compiler
    image is built once, so builds need no network."""
    if subprocess.run(['docker', 'image', 'inspect', MUSL], capture_output=True).returncode:
        subprocess.run(['docker', 'build', '-q', '-t', MUSL, '-'], check=True,
                       input=b'FROM alpine:3.20\nRUN apk add --no-cache gcc musl-dev linux-headers libdrm-dev\n')
    subprocess.run(['docker', 'run', '--rm', '-v', f'{src.parent}:/s:ro', '-v', f'{out.parent}:/o', MUSL,
                    'gcc', '-static', '-Os', '-s', '-Wall', '-Werror', '-I/usr/include/libdrm',
                    '-o', f'/o/{out.name}', f'/s/{src.name}'], check=True)


def root():
    OUT.mkdir(parents=True, exist_ok=True)
    tools = OUT / 'bin'; tools.mkdir(exist_ok=True)
    static(R / 'v2/dev/src/feeder.c', tools / 'baseos-feeder')
    static(R / 'v2/dev/src/mem.c', tools / 'mem')
    static(R / 'v2/dev/src/pad.c', tools / 'baseos-pad')
    static(R / 'v2/dev/src/firstframe.c', tools / 'baseos-firstframe')
    static(R / 'v2/os/baseos-events.c', tools / 'baseos-events')
    static(R / 'v2/os/baseos-prefetch.c', tools / 'baseos-prefetch')
    static(R / 'v2/os/baseos-charger.c', tools / 'baseos-charger')
    drop = ' '.join(DROP); dirs = ' '.join(DIRS)
    sh(f'''
rm -rf /k/root && mkdir /k/root && cp -a /repo/{BASE}/. /k/root/
cd /k/root
rm -f {drop}
cp -a /repo/v2/os/overlay/. .
install -m 755 /repo/work/v2/os/bin/baseos-feeder /repo/work/v2/os/bin/mem /repo/work/v2/os/bin/baseos-pad /repo/work/v2/os/bin/baseos-firstframe /repo/work/v2/os/bin/baseos-events /repo/work/v2/os/bin/baseos-prefetch /repo/work/v2/os/bin/baseos-charger usr/sbin/
for d in {dirs}; do mkdir -p "$d"; done
# Graphics: Mesa (built with its shader cache, v2/graphics/mesa-cache.sh) is the
# system GL stack; BaseOS's KMS SDL lives in /usr/lib/baseos2.
M=usr/lib/baseos-mainline A=usr/lib/aarch64-linux-gnu G=/repo/work/v2/mesa-cache/runtime
for f in libEGL.so.1.0.0 libGLESv2.so.2.0.0 libgallium-26.2.5.so libgbm.so.1.0.0 libdrm.so.2.125.0; do cp $G/$f $A/; done
ln -sf libEGL.so.1.0.0 $A/libEGL.so.1; ln -sf libEGL.so.1 $A/libEGL.so
ln -sf libGLESv2.so.2.0.0 $A/libGLESv2.so.2; ln -sf libGLESv2.so.2 $A/libGLESv2.so
ln -sf libgbm.so.1.0.0 $A/libgbm.so.1; ln -sf libdrm.so.2.125.0 $A/libdrm.so.2
mkdir -p usr/lib/gbm usr/lib/baseos2 && cp $G/gbm/dri_gbm.so usr/lib/gbm/
cp /repo/work/mainline/u4g/devel/usr/lib/libSDL2-2.0.so.0.2800.5 usr/lib/baseos2/
ln -s libSDL2-2.0.so.0.2800.5 usr/lib/baseos2/libSDL2-2.0.so.0
rm -rf $M
ldconfig -r /k/root
printf 'BASEOS_VERSION=2.0.0-dev\n' > etc/baseos-release
mkdir -p usr/share/baseos && cp /repo/v2/CONTRACT.md usr/share/baseos/
# Audio routing: ALSA's default device follows /run/baseos/asound.conf (baseos audio).
rm -f etc/asound.conf && ln -s /run/baseos/asound.conf etc/asound.conf
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
        ('logo.bmp', stat.S_IFREG | 0o644, LOGO.read_bytes(), (0, 0)),
    ]), compresslevel=9, mtime=0)
    boot, _ = dev.payload.parse(dev.PROVEN.read_bytes(), dev.E1)
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
