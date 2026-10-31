#!/usr/bin/env python3
"""Drive the RG28XX dev loop over adb.

  status            which OS is running (stock 1.5.3 or dev)
  install IMAGE     from stock: write IMAGE at p4+48MiB, arm the one-shot trial
  boot              from stock: restart through NextUI's reboot_next into the trial
  wait              wait until the dev system answers on adb
  heartbeat         keep the dev system's watchdog fed (runs until killed)
  stock             from dev: stop feeding; the watchdog returns the board to stock
  sh CMD            run CMD on whichever system is up

The E1 firmware and capsule at p4+40MiB and LBA16 must already be on the card;
`install` refuses otherwise. A failed trial falls back to stock by itself.
"""
import hashlib, re, subprocess, sys, time
from pathlib import Path

ADB = '/Users/pvaibhav/Library/Android/sdk/platform-tools/adb'
SERIAL = '0c00105562458931c52'
R = Path(__file__).resolve().parents[2]
FIRMWARE = R / 'work/v2/firmware/v2a'
FW = FIRMWARE / 'firmware.bin'
CAPSULE = FIRMWARE / 'capsule.egon.bin'
REBOOT_NEXT_SHA = '314ac6631c06081187f455c8044d2ca6df377d832bc1691db6c1c7eb614226f6'


def adb(*args, timeout=60, check=True):
    return subprocess.run([ADB, '-s', SERIAL, *args], capture_output=True, text=True,
                          timeout=timeout, check=check).stdout


def sh(cmd, timeout=60):
    """Run cmd; return output, raise on non-zero status (this adbd drops exit codes)."""
    out = adb('shell', '{ ' + cmd + '\n}; printf "\\n@RC=%s\\n" $?', timeout=timeout)
    m = re.search(r'\r?\n@RC=(\d+)\s*$', out)
    if not m:
        raise RuntimeError('lost output: ' + out[-500:])
    if m[1] != '0':
        raise RuntimeError(f'rc={m[1]}: ' + out[:m.start()][-2000:])
    return out[:m.start()].strip()


def which():
    try:
        r = sh('uname -r', timeout=10)
    except Exception:
        return None
    return 'stock' if r == '4.9.170' else 'dev:' + r


def range_sha(dev, sector, nbytes):
    return sh(f'dd if={dev} bs=512 skip={sector} count={nbytes // 512} 2>/dev/null | sha256sum').split()[0]


def install(image):
    data = Path(image).read_bytes()
    assert which() == 'stock', 'install runs from stock 1.5.3'
    assert len(data) % 512 == 0 and len(data) <= 16 << 20
    for path, dev, sector in ((FW, '/dev/mmcblk0p4', 40 * 2048), (CAPSULE, '/dev/mmcblk0', 16)):
        b = path.read_bytes()
        assert range_sha(dev, sector, len(b)) == hashlib.sha256(b).hexdigest(), f'{path.name} not on card'
    stage = '/tmp/baseos-dev-image'
    adb('push', str(image), stage)
    try:
        assert sh(f'sha256sum {stage}').split()[0] == hashlib.sha256(data).hexdigest()
        sh('/usr/sbin/native-health --hold-stock')
        sh(f'dd if={stage} of=/dev/mmcblk0p4 bs=512 seek={48 * 2048} oflag=direct conv=notrunc,fsync 2>/dev/null')
        assert range_sha('/dev/mmcblk0p4', 48 * 2048, len(data)) == hashlib.sha256(data).hexdigest()
        sh('/usr/sbin/native-health --release-stock')
        print('latch:', sh('/usr/sbin/native-health --stock-latch'))
    finally:
        sh(f'rm -f {stage}')


def boot():
    assert which() == 'stock'
    assert sh('/usr/sbin/native-health --stock-latch').endswith(' idle'), 'trial not armed'
    pid = sh('pidof nextui.elf')
    stage = '/tmp/baseos-dev-entry'
    script = f'''set -e
mkdir -p {stage}
cleanup() {{ mount -o remount,rw / || :; mount -o remount,rw /data || :; kill -CONT {pid} || :; rm -rf {stage}; }}
trap cleanup EXIT HUP INT TERM
cp /mnt/sdcard/.system/h700/bin/reboot_next {stage}/reboot_next
[ "$(sha256sum {stage}/reboot_next | cut -d ' ' -f 1)" = {REBOOT_NEXT_SHA} ]
kill -STOP {pid}
sync
mount -o remount,ro /data
mount -o remount,ro /
grep -q '/dev/root / ext4 ro,' /proc/mounts
sync
unset SHARED_USERDATA_PATH
LD_LIBRARY_PATH=/usr/lib:/usr/lib/aarch64-linux-gnu {stage}/reboot_next
exit 1'''
    t0 = time.time()
    try:
        adb('shell', script, timeout=20, check=False)
    except subprocess.TimeoutExpired:
        pass
    print(f'restart issued at {time.strftime("%H:%M:%S")}')
    return t0


def wait(limit=90):
    t0 = time.time()
    while time.time() - t0 < limit:
        w = which()
        if w and w.startswith('dev'):
            sh('touch /run/hb')
            print(f'{w} after {time.time() - t0:.1f}s; uptime', sh('cat /proc/uptime'))
            return True
        if w == 'stock' and time.time() - t0 > 5:
            print(f'stock after {time.time() - t0:.1f}s (trial fell back)')
            return False
        time.sleep(0.5)
    print('nothing answered')
    return False


def quiesce():
    """Release the card and /data before a reset. rcK freezes the frontend group
    before killing it (NextUI's launcher powers off when NextUI exits)."""
    sh('if [ -x /etc/init.d/rcK ]; then /etc/init.d/rcK; else '
       'for m in /mnt/root/mnt/sdcard /mnt/root/data /mnt/card; do mountpoint -q $m && umount $m; done; fi; sync; true')


def reload(image):
    """From dev: install IMAGE, re-arm the one-shot trial and reset now."""
    data = Path(image).read_bytes()
    assert (which() or '').startswith('dev')
    quiesce()
    adb('push', str(image), '/tmp/next.bin')
    assert sh('sha256sum /tmp/next.bin').split()[0] == hashlib.sha256(data).hexdigest()
    sh(f'dd if=/tmp/next.bin of=/dev/mmcblk0p4 bs=512 seek={48 * 2048} conv=notrunc,fsync 2>/dev/null; sync')
    assert range_sha('/dev/mmcblk0p4', 48 * 2048, len(data)) == hashlib.sha256(data).hexdigest()
    rearm_and_reset()


def rearm_and_reset():
    # RTC GP14/GP15 pair zero = the loader tries the trial once; then reset in 0.5 s.
    sh('mem 0x07000138 0 && mem 0x0700013c 0 && sync')
    try:
        adb('shell', 'mem 0x030090b4 0x16aa0001; mem 0x030090b8 0x16aa0001; mem 0x030090b0 0x14af', timeout=5, check=False)
    except subprocess.TimeoutExpired:
        pass
    print(f'reset at {time.strftime("%H:%M:%S")}')


def write_root(image):
    """Write a root image to card slot A (LBA 434176; stock 1.5.3 runs from slot B)."""
    data = Path(image).read_bytes()
    assert len(data) <= 512 << 20 and len(data) % 4096 == 0
    adb('push', str(image), '/tmp/root.sqfs', timeout=300)
    assert sh('sha256sum /tmp/root.sqfs').split()[0] == hashlib.sha256(data).hexdigest()
    assert sh('cat /sys/class/block/mmcblk0p5/start') != '434176', 'slot A is the live root'
    sh('dd if=/tmp/root.sqfs of=/dev/mmcblk0 bs=1M seek=212 conv=notrunc,fsync 2>/dev/null; rm /tmp/root.sqfs; sync', timeout=300)
    assert range_sha('/dev/mmcblk0', 434176, len(data)) == hashlib.sha256(data).hexdigest()
    print('root written', len(data))


SHELL = R / 'work/v2/dev/shell.bin'


def deploy(root, image):
    """Write ROOT to slot A from the RAM-only dev shell, then boot IMAGE."""
    w = which()
    if w == 'stock':
        write_root(root); install(image); boot(); return wait()
    if sh('[ -e /etc/init.d/rcS ] && echo root || echo shell') == 'root':
        reload(SHELL); time.sleep(2); assert wait()
    write_root(root)
    reload(image); time.sleep(2); return wait()


FSCK = Path('/private/tmp/b2/fsck')


def fsck():
    """Check and repair the card's FAT from the RAM-only shell (card unmounted)."""
    if (which() or '').startswith('dev') and sh('[ -e /etc/init.d/rcS ] && echo root || echo shell') == 'root':
        reload(SHELL); time.sleep(2); assert wait()
    for f in ('fsck.fat', 'ld-musl-aarch64.so.1'):
        adb('push', str(FSCK / f), '/tmp/' + f)
    print(sh('chmod 755 /tmp/fsck.fat /tmp/ld-musl-aarch64.so.1; ! grep -q mmcblk0p7 /proc/mounts; '
             '/tmp/ld-musl-aarch64.so.1 /tmp/fsck.fat -a -w /dev/mmcblk0p7 2>&1 | tail -3; '
             'ls /tmp/*.REC 2>/dev/null; rm -f /tmp/fsck.fat /tmp/ld-musl-aarch64.so.1; sync; true', timeout=300))


def firmware(image):
    """From stock: install the boot firmware in FIRMWARE plus IMAGE (v2a INSTALL.md order)."""
    assert which() == 'stock', 'firmware installs run from stock 1.5.3'
    files = {'firmware.bin': FW.read_bytes(), 'image.bin': Path(image).read_bytes(), 'capsule.bin': CAPSULE.read_bytes()}
    assert len(files['capsule.bin']) == 131072 and len(files['firmware.bin']) == 81920
    stage = '/tmp/baseos-firmware'
    sh(f'rm -rf {stage}; mkdir {stage}')
    try:
        for name, data in files.items():
            tmp = Path('/private/tmp/b2') / name; tmp.write_bytes(data)
            adb('push', str(tmp), f'{stage}/{name}')
            assert sh(f'sha256sum {stage}/{name}').split()[0] == hashlib.sha256(data).hexdigest(), name
        sh('/usr/sbin/native-health --hold-stock')
        assert sh('/usr/sbin/native-health --stock-latch').endswith(' held')
        for name, dev, sector in (('firmware.bin', '/dev/mmcblk0p4', 40 * 2048), ('image.bin', '/dev/mmcblk0p4', 48 * 2048)):
            sh(f'dd if={stage}/{name} of={dev} bs=512 seek={sector} oflag=direct conv=notrunc,fsync 2>/dev/null')
            assert range_sha(dev, sector, len(files[name])) == hashlib.sha256(files[name]).hexdigest(), name
        cap = files['capsule.bin']
        sh(f'dd if={stage}/capsule.bin of=/dev/mmcblk0 bs=512 skip=1 seek=17 count=255 oflag=direct conv=notrunc,fsync 2>/dev/null')
        assert range_sha('/dev/mmcblk0', 17, 255 * 512) == hashlib.sha256(cap[512:]).hexdigest(), 'capsule body'
        sh(f'dd if={stage}/capsule.bin of=/dev/mmcblk0 bs=512 seek=16 count=1 oflag=direct conv=notrunc,fsync 2>/dev/null')
        assert range_sha('/dev/mmcblk0', 16, len(cap)) == hashlib.sha256(cap).hexdigest(), 'capsule'
        sh('/usr/sbin/native-health --release-stock')
        print('firmware installed; latch:', sh('/usr/sbin/native-health --stock-latch'))
    finally:
        sh(f'rm -rf {stage}')


def heartbeat():
    while True:
        try:
            if (which() or '').startswith('dev'):
                sh('touch /run/hb', timeout=10)
        except Exception:
            pass
        time.sleep(20)


def main():
    cmd, args = (sys.argv[1:2] or ['status'])[0], sys.argv[2:]
    if cmd == 'status':
        print(which())
    elif cmd == 'install':
        install(args[0])
    elif cmd == 'boot':
        boot(); wait()
    elif cmd == 'reload':
        reload(args[0]); time.sleep(2); wait()
    elif cmd == 'deploy':
        deploy(args[0], args[1])
    elif cmd == 'pad':
        # Virtual pad: started once, detached (adb kills its own children).
        sh('[ -p /run/pad ] || { setsid sh -c "/usr/sbin/baseos-pad </dev/null >/run/pad.log 2>&1 &" </dev/null >/dev/null 2>&1; sleep 1.5; }')
        for b in args:
            sh(f'echo {b.replace(":", " ")} > /run/pad')
    elif cmd == 'bg':
        sh('setsid sh -c ' + repr(' '.join(args) + ' </dev/null >/dev/null 2>&1 &') + ' </dev/null >/dev/null 2>&1')
    elif cmd == 'firmware':
        firmware(args[0])
    elif cmd == 'fsck':
        fsck()
    elif cmd == 'root':
        write_root(args[0])
    elif cmd == 'quiesce':
        quiesce()
    elif cmd == 'wait':
        wait()
    elif cmd == 'heartbeat':
        heartbeat()
    elif cmd == 'stock':
        assert (which() or '').startswith('dev')
        quiesce()
        # A nonzero RTC pair sends the loader to stock; then reset in 0.5 s.
        sh('mem 0x07000138 0x9f559c26 && mem 0x0700013c 0x42535031 && sync')
        try:
            adb('shell', 'mem 0x030090b4 0x16aa0001; mem 0x030090b8 0x16aa0001; mem 0x030090b0 0x14af', timeout=5, check=False)
        except subprocess.TimeoutExpired:
            pass
        print('reset to stock')
    elif cmd == 'sh':
        print(sh(' '.join(args), timeout=300))
    else:
        sys.exit(__doc__)


if __name__ == '__main__':
    main()
