#!/usr/bin/env python3
"""Build the BaseOS 2 dev-shell payload for the RG28XX E1 loader.

Kernel, DTB and the busybox/adbd binaries come unchanged from the interactive
payload that booted to adb and ran a game on hardware. Only the initramfs is ours.
Output: work/v2/dev/image.bin (write it at p4 + 48 MiB).
"""
import gzip, hashlib, json, os, stat, sys
from pathlib import Path

R = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(R / 'tools')]
import mainline_payload as payload, kernel_gzip as android, mainline_kernel_codec as codec
import argparse

G = bytes.fromhex('e1a64fac986574dbe2a1885805d11afef570a020c84a7022a18088ef567e7b38')
PROVEN = R / 'work/mainline/interactive/media/payload/interactive-final/image.bin'
PROVEN_SHA = '481641cbc34d5cd29fcbdcb95f67fdf5c25e31b11fb058fd76a300f750232696'
OUT = R / 'work/v2/dev'


def sha(b): return hashlib.sha256(b).hexdigest()


def bol4(image):
    """Loader kernel format: 'BOL4', block size, Image size, FNV-1a, one LZ4 HC block."""
    import ctypes, struct
    payload.kernel_check(image)
    lz4 = ctypes.CDLL('/opt/homebrew/opt/lz4/lib/liblz4.dylib')
    lz4.LZ4_compress_HC.argtypes = [ctypes.c_void_p, ctypes.c_void_p] + [ctypes.c_int] * 3
    cap = lz4.LZ4_compressBound(len(image))
    buf = ctypes.create_string_buffer(cap)
    n = lz4.LZ4_compress_HC(image, buf, len(image), cap, 12)
    assert n > 0
    out = struct.pack('<4I', 0x344C4F42, n, len(image), payload.fnv(buf.raw[:n])) + buf.raw[:n]
    assert codec.decode(out) == image, 'loader decoder disagrees'
    return out


def read_newc(blob):
    files, i = {}, 0
    while True:
        f = [int(blob[i + 6 + 8 * k:i + 14 + 8 * k], 16) for k in range(13)]
        mode, size, namesize = f[1], f[6], f[11]
        name = blob[i + 110:i + 110 + namesize - 1].decode()
        i = (i + 110 + namesize + 3) & ~3
        data = blob[i:i + size]
        i = (i + size + 3) & ~3
        if name == 'TRAILER!!!':
            return files
        files[name] = (mode, data)


def write_newc(entries):
    out, ino = bytearray(), 0
    for name, mode, data, rdev in entries + [('TRAILER!!!', 0, b'', (0, 0))]:
        ino += 1
        n = name.encode() + b'\0'
        fields = [ino, mode, 0, 0, 2 if stat.S_ISDIR(mode) else 1, 0, len(data), 0, 0,
                  rdev[0], rdev[1], len(n), 0]
        out += b'070701' + b''.join(b'%08x' % x for x in fields) + n
        out += bytes(-len(out) % 4) + data
        out += bytes(-len(out) % 4)
    return bytes(out + bytes(-len(out) % 512))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--kernel', help='arm64 Image (default: proven U4-K)')
    ap.add_argument('--dtb', help='DTB (default: proven U5)')
    ap.add_argument('--out', default=str(OUT / 'image.bin'))
    a = ap.parse_args()
    proven = PROVEN.read_bytes()
    assert sha(proven) == PROVEN_SHA, 'proven payload changed'
    boot, dtb = payload.parse(proven, G)
    segs = android.split(boot)[0]
    old = read_newc(gzip.decompress(segs[1]))
    src = Path(__file__).resolve().parent / 'initramfs'
    D, F, X = stat.S_IFDIR | 0o755, stat.S_IFREG | 0o644, stat.S_IFREG | 0o755
    entries = [(d, D, b'', (0, 0)) for d in
               ('bin', 'dev', 'etc', 'mnt', 'proc', 'run', 'sbin', 'sys', 'tmp')]
    entries += [('dev/console', stat.S_IFCHR | 0o600, b'', (5, 1)),
                ('dev/null', stat.S_IFCHR | 0o666, b'', (1, 3)),
                ('bin/busybox', X, old['bin/busybox'][1], (0, 0)),
                ('bin/sh', stat.S_IFLNK | 0o777, b'busybox', (0, 0)),
                ('sbin/adbd', X, old['sbin/adbd'][1], (0, 0))]
    for p in sorted(src.rglob('*')):
        name = p.relative_to(src).as_posix()
        if p.is_dir():
            if name not in {e[0] for e in entries}:
                entries.append((name, D, b'', (0, 0)))
        else:
            entries.append((name, X if os.access(p, os.X_OK) else F, p.read_bytes(), (0, 0)))
    entries.sort(key=lambda e: e[0])
    kernel = bol4(Path(a.kernel).read_bytes()) if a.kernel else segs[0]
    if a.dtb:
        dtb = Path(a.dtb).read_bytes()
    initrd = gzip.compress(write_newc(entries), compresslevel=9, mtime=0)
    image = payload.pack(android.join(boot[:2048], [kernel, initrd, b'', b'', segs[4]], b''), dtb, G)
    out = Path(a.out); out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(image)
    info = dict(image_sha256=sha(image), image_bytes=len(image), initrd_bytes=len(initrd),
                kernel=a.kernel or PROVEN_SHA, dtb=a.dtb or PROVEN_SHA, files=[e[0] for e in entries])
    out.with_suffix('.json').write_text(json.dumps(info, indent=1) + '\n')
    print(json.dumps({k: info[k] for k in ('image_sha256', 'image_bytes', 'initrd_bytes')}))


if __name__ == '__main__':
    main()
