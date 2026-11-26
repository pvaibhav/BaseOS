#!/usr/bin/env python3
"""RG SP boot-speed lab: build boot variants, write them, restart, measure.

This writes the bootloader and boot partition of the attached test device and
restarts it. Run only with explicit authorization. Everything is derived from a
backup of the device's own bytes, taken once with `backup`.

  lab.py backup
  lab.py build NAME [--ramdisk empty] [--cmdline TEXT] [--dtb NODE:PROP=VALUE ...] [--boot0-debug N]
  lab.py flash NAME | restore
  lab.py deploy [--mode 755] LOCAL REMOTE [LOCAL REMOTE ...]
  lab.py measure LABEL [-n COUNT]
  lab.py collect LABEL

VALUE for --dtb is u32:<number> or str:<text>; the replacement must be the
same size as the property it replaces, so no offset in the tree moves.
Artifacts go to $BOOT_LAB_WORK (default: work/lab in this checkout).

The defaults address the RG SP this was written on. For another device set
$BOOT_LAB_SERIAL (adb serial), $BOOT_LAB_TARGET (BaseOS target name), a
separate $BOOT_LAB_WORK, and $BOOT_LAB_REBOOT_NEXT (a local copy of NextUI's
reboot_next, for a device whose own card has no NextUI).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import statistics
import struct
import subprocess
import sys
import time
import zlib
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "tools"))
import fdt  # noqa: E402
from kernel_gzip import align, checksum, image_id, word  # noqa: E402

WORK = Path(os.environ.get("BOOT_LAB_WORK", ROOT / "work/lab")).resolve()
SERIAL = os.environ.get("BOOT_LAB_SERIAL", "ac001089c89588720d2")
TARGET = os.environ.get("BOOT_LAB_TARGET", "rgsp")
ADB = ["adb", "-s", SERIAL]
REBOOT_NEXT_SHA = "3f1b76894b84da54d2a2df14f8982f146bc43d4ab61e2e81edc1b0749e78c4b1"

# name -> (device, start sector, bytes kept in the backup)
REGIONS = {
    "boot0": ("/dev/mmcblk0", 16, 65536),
    "toc1": ("/dev/mmcblk0", 32800, 1310720),
    "boot": ("/dev/mmcblk0p4", 0, 64 * 1024 * 1024),
    "env": ("/dev/mmcblk0p3", 0, 16 * 1024 * 1024),
}
WRITABLE = ("boot0", "toc1", "boot", "env")
PINNED = ("toc1", "boot")
EMPTY_CPIO = (b"070701" + b"%08X" * 13 % (0, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0, 11, 0)
              + b"TRAILER!!!\0").ljust(512, b"\0")


def sha(blob: bytes) -> str:
    return hashlib.sha256(blob).hexdigest()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(f"lab: {message}")


def raw_shell(command: str, timeout: float):
    return subprocess.run(ADB + ["shell", "-n", command], capture_output=True, text=True, timeout=timeout)


def sh(command: str, timeout: float = 30) -> str:
    # This adbd ends the session, killing the command, as soon as nothing holds
    # its terminal open: BusyBox dd with if=/of= and a redirected stderr is cut
    # off after a few sectors. The trailing marker keeps the parent attached;
    # a subshell lets it report the command's status even after an explicit exit.
    marker = "__LAB_END__" + os.urandom(16).hex()
    wrapped = ("(\n" + command + "\n)\nlab_shell_status=$?\n"
               + "printf '\\n" + marker + ":%s\\n' \"$lab_shell_status\"")
    p = raw_shell(wrapped, timeout)
    out = p.stdout.replace("\r", "")
    completion = re.search(r"\n" + marker + r":([0-9]{1,3})\n\Z", out)
    if p.returncode or not completion or int(completion[1]) > 255:
        raise RuntimeError(f"device command did not finish: {command[:80]!r}: {p.stderr or out[-200:]}")
    output = out[:completion.start()].strip()
    if int(completion[1]):
        raise RuntimeError(f"device command exited with status {completion[1]}: {command[:80]!r}:"
                           f" {p.stderr or output[-200:]}")
    return output


def identity() -> None:
    out = sh("grep -c '^BASEOS_TARGET=%s$' /etc/baseos-release; grep -c androidboot.serialno=%s /proc/cmdline;"
             " cat /sys/class/block/mmcblk0p4/start /sys/class/block/mmcblk0p4/size" % (TARGET, SERIAL)).split()
    require(out == ["1", "1", "303104", "131072"], f"not the expected {TARGET} device and layout: {out}")


def read_region(name: str, size: int | None = None) -> bytes:
    device, sector, limit = REGIONS[name]
    size = limit if size is None else size
    require(0 < size <= limit and size % 512 == 0, "invalid read extent")
    sh(f"dd if={device} of=/tmp/lab-read.bin bs=512 skip={sector} count={size // 512} iflag=direct 2>/dev/null", 120)
    local = WORK / "tmp-read.bin"
    subprocess.run(ADB + ["pull", "/tmp/lab-read.bin", str(local)], check=True, capture_output=True, timeout=300)
    sh("rm -f /tmp/lab-read.bin")
    blob = local.read_bytes()
    local.unlink()
    require(len(blob) == size, f"short read of {name}")
    return blob


def device_sha(name: str, size: int, offset: int = 0) -> str:
    device, sector, limit = REGIONS[name]
    require(size % 512 == 0 and offset % 512 == 0 and 0 <= offset <= limit - size, "unaligned/out-of-range region")
    sector += offset // 512
    return sh(f"dd if={device} bs=512 skip={sector} count={size // 512} iflag=direct 2>/dev/null | sha256sum", 120).split()[0]


def write_region(name: str, blob: bytes, offset: int = 0, *, env_image: bytes | None = None) -> None:
    device, sector, limit = REGIONS[name]
    require(name in WRITABLE and len(blob) % 512 == 0 and offset % 512 == 0
            and 0 <= offset <= limit - len(blob), f"refusing write to {name}")
    if name == "env":
        whole = blob if env_image is None else env_image
        require(offset == 0 and len(whole) == 0x20000 and word(whole, 0) == zlib.crc32(whole[4:]),
                "refusing an invalid environment image")
        if env_image is None:
            require(len(blob) == 0x20000, "refusing an unproved partial environment write")
        else:
            require(len(blob) == 512 and blob == whole[:512]
                    and read_region("env", 0x20000)[512:] == whole[512:],
                    "environment tail differs; refusing partial write")
    sector += offset // 512
    local = WORK / "tmp-write.bin"
    local.write_bytes(blob)
    subprocess.run(ADB + ["push", str(local), "/tmp/lab-write.bin"], check=True, capture_output=True, timeout=300)
    local.unlink()
    require(sh("sha256sum /tmp/lab-write.bin").split()[0] == sha(blob), f"{name}: staged copy differs")
    sh(f"dd if=/tmp/lab-write.bin of={device} bs=512 seek={sector} conv=notrunc,fsync 2>/dev/null; sync", 180)
    sh("rm -f /tmp/lab-write.bin")
    require(device_sha(name, len(blob), offset) == sha(blob), f"{name}: readback differs — DO NOT RESTART, restore first")
    if env_image is not None:
        require(device_sha("env", len(env_image)) == sha(env_image), "full environment readback differs")
    print(f"  wrote and verified {name}: {len(blob)} bytes {sha(blob)[:16]}")


# ---------------------------------------------------------------- backup

def backup(_args) -> None:
    identity()
    dest = WORK / "backup"
    require(not dest.exists(), f"{dest} already exists; backups are never overwritten")
    dest.mkdir(parents=True)
    manifest = {}
    for name in REGIONS:
        blob = read_region(name)
        (dest / f"{name}.bin").write_bytes(blob)
        manifest[name] = {"bytes": len(blob), "sha256": sha(blob)}
        print(f"  {name}: {len(blob)} bytes {sha(blob)[:16]}")
    if os.environ.get("BOOT_LAB_REBOOT_NEXT"):
        (dest / "reboot_next").write_bytes(Path(os.environ["BOOT_LAB_REBOOT_NEXT"]).read_bytes())
    else:
        sh("mkdir -p /tmp/tf1 && mount -t vfat -o ro /dev/mmcblk0p7 /tmp/tf1 && cp /tmp/tf1/.system/h700/bin/reboot_next /tmp/reboot_next;"
           " umount /tmp/tf1; rmdir /tmp/tf1")
        subprocess.run(ADB + ["pull", "/tmp/reboot_next", str(dest / "reboot_next")], check=True, capture_output=True)
    require(sha((dest / "reboot_next").read_bytes()) == REBOOT_NEXT_SHA, "unexpected reboot_next")
    manifest["release"] = sh("cat /etc/baseos-release")
    (dest / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")


def original(name: str) -> bytes:
    blob = (WORK / "backup" / f"{name}.bin").read_bytes()
    require(sha(blob) == json.loads((WORK / "backup/manifest.json").read_text())[name]["sha256"], f"backup {name} changed")
    return blob


# ---------------------------------------------------------------- build

def split_boot(boot: bytes):
    require(boot[:8] == b"ANDROID!" and word(boot, 36) == 2048 and word(boot, 40) == 2, "unexpected boot image")
    sizes = [word(boot, at) for at in (8, 16, 24, 0x660, 0x670)]
    parts, pos = [], 2048
    for size in sizes:
        parts.append(boot[pos:pos + size])
        pos += align(size)
    require(boot[0x240:0x260] == image_id(parts), "boot image ID does not reproduce")
    return bytearray(boot[:2048]), parts


def join_boot(header: bytearray, parts: list[bytes]) -> bytes:
    for at, part in zip((8, 16, 24, 0x660, 0x670), parts):
        struct.pack_into("<I", header, at, len(part))
    header[0x240:0x260] = image_id(parts)
    return bytes(header) + b"".join(p + bytes(align(len(p)) - len(p)) for p in parts)


def parse_value(text: str) -> bytes:
    kind, _, raw = text.partition(":")
    if kind == "u32":
        return struct.pack(">I", int(raw, 0))
    if kind == "str":
        return raw.encode() + b"\0"
    raise SystemExit(f"lab: unknown value kind {kind!r}")


def build(args) -> None:
    out = WORK / "variants" / args.name
    require(not out.exists(), f"variant {args.name} already exists")
    files, notes = {}, []

    if args.ramdisk or args.cmdline is not None:
        header, parts = split_boot(original("boot"))
        if args.ramdisk == "empty":
            notes.append(f"ramdisk {len(parts[1])} -> {len(EMPTY_CPIO)} bytes (empty archive)")
            parts[1] = EMPTY_CPIO
        if args.cmdline is not None:
            encoded = args.cmdline.encode()
            require(len(encoded) < 512, "cmdline too long for the header field")
            header[0x40:0x240] = encoded.ljust(512, b"\0")
            notes.append(f"header cmdline {args.cmdline!r}")
        image = join_boot(header, parts)
        check_header, check_parts = split_boot(image)
        require(check_parts[0] == split_boot(original("boot"))[1][0], "kernel bytes changed")
        files["boot"] = image

    if args.dtb:
        package = bytearray(original("toc1"))
        require(checksum(package, 20) == word(package, 20), "TOC1 checksum does not reproduce")
        items = {}
        for i in range(word(package, 32)):
            pos = 64 + 368 * i
            name = bytes(package[pos:pos + 64]).split(b"\0")[0].decode()
            items[name] = struct.unpack_from("<2I", package, pos + 64)
        start, size = items["dtb"]
        tree = bytearray(package[start:start + size])
        for edit in args.dtb:
            target, _, value = edit.partition("=")
            node, _, prop = target.rpartition(":")
            old = fdt.set_same_size(tree, node, prop, parse_value(value))
            notes.append(f"dtb {node}:{prop} {old.hex()} -> {parse_value(value).hex()}")
        package[start:start + size] = tree
        struct.pack_into("<I", package, 20, checksum(package, 20))
        before = original("toc1")
        changed = [i for i in range(len(package)) if package[i] != before[i]]
        require(all(20 <= i < 24 or start <= i < start + size for i in changed), "TOC1 changed outside dtb/checksum")
        files["toc1"] = bytes(package)

    if args.boot0_debug is not None:
        notes.append(f"boot0 debug_mode -> {args.boot0_debug}")

    require(bool(files) or args.boot0_debug is not None, "nothing to build")
    out.mkdir(parents=True)
    for name, blob in files.items():
        (out / f"{name}.bin").write_bytes(blob)
    manifest = {"name": args.name, "notes": notes,
                "boot0_debug": 3 if args.boot0_debug is None else args.boot0_debug,
                "files": {n: {"bytes": len(b), "sha256": sha(b)} for n, b in files.items()}}
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps(manifest, indent=2))


# ---------------------------------------------------------------- flash

def set_boot0_debug(level: int) -> None:
    # boot0 rewrites its own header (memory training results and checksum) on
    # the card during boot, so there is no fixed copy to pin or restore. Edit
    # the one byte in whatever is on the card now.
    boot0 = bytearray(read_region("boot0"))
    require(boot0[4:12] == b"eGON.BT0" and word(boot0, 0x10) == len(boot0), "unexpected boot0")
    require(checksum(boot0, 12) == word(boot0, 12), "boot0 checksum does not reproduce")
    if boot0[0x34] == level:
        print(f"  boot0: debug_mode already {level}")
        return
    print(f"  boot0: debug_mode {boot0[0x34]} -> {level}")
    boot0[0x34] = level
    struct.pack_into("<I", boot0, 12, checksum(boot0, 12))
    write_region("boot0", bytes(boot0))


def flash(args) -> None:
    identity()
    out = WORK / "variants" / args.name
    manifest = json.loads((out / "manifest.json").read_text())
    for name in PINNED:
        path = out / f"{name}.bin"
        blob = path.read_bytes() if path.exists() else None
        if blob is None:
            # A variant only lists what it changes; everything else goes back
            # to the backup so variants never stack by accident.
            blob = original(name)
            if name == "boot":
                blob = blob[:20 * 1024 * 1024]
        else:
            require(sha(blob) == manifest["files"][name]["sha256"], f"{name} differs from its manifest")
        if device_sha(name, len(blob)) == sha(blob):
            print(f"  {name}: already in place")
            continue
        write_region(name, blob)
    set_boot0_debug(manifest.get("boot0_debug", 3))
    (WORK / "installed").write_text(args.name + "\n")
    print(f"installed {args.name}: {'; '.join(manifest['notes'])}\nNot restarted.")


def restore(_args) -> None:
    identity()
    for name in PINNED:
        blob = original(name)
        if device_sha(name, len(blob)) == sha(blob):
            print(f"  {name}: already original")
            continue
        write_region(name, blob)
    set_boot0_debug(3)
    (WORK / "installed").write_text("backup\n")
    print("backup restored. Not restarted.")


# ---------------------------------------------------------------- measure

BOOT_CRITICAL = ("/init", "/etc/init.d/rcS", "/etc/init.d/rcK", "/usr/bin/busybox")


def boot_critical_ok() -> None:
    # init runs these directly. One without its execute bit leaves the device
    # at the boot logo with no adb and no network: this is what froze the RG SP
    # on 2026-10-13, after a copy of an adb-pushed file (mode 0666) replaced
    # rcS and frontend-session without a chmod. The session script's name
    # depends on the release (nextui-session before 1.3.0), so take it from
    # inittab.
    sessions = sh("sed -n 's/^::respawn:\\(\\/[^ ]*\\).*/\\1/p' /etc/inittab").split()
    require(sessions, "inittab names no session to respawn")
    for path in BOOT_CRITICAL + tuple(sessions):
        state = sh(f"if [ -f {path} ] && [ -x {path} ] && [ -s {path} ]; then echo ok; else ls -l {path} 2>&1; fi")
        require(state == "ok", f"refusing to restart: {path} is not a non-empty executable file ({state})")
    require(sh("sh -n /etc/init.d/rcS && " + " && ".join(f"sh -n {s}" for s in sessions) + " && echo ok") == "ok",
            "refusing to restart: a startup script does not parse")


def deploy(args) -> None:
    """Install files on the device's root filesystem with an explicit mode."""
    identity()
    require(len(args.files) % 2 == 0 and args.files, "deploy takes LOCAL REMOTE pairs")
    pairs = list(zip(args.files[::2], args.files[1::2]))
    root_was = sh("grep ' / ' /proc/mounts | tail -1 | cut -d' ' -f4 | cut -d, -f1")
    if root_was == "ro":
        sh("mount -o remount,rw /")
    try:
        for local, remote in pairs:
            blob = Path(local).read_bytes()
            subprocess.run(ADB + ["push", local, "/tmp/lab-deploy"], check=True, capture_output=True)
            sh(f"cat /tmp/lab-deploy > {remote}.lab-new && chmod {args.mode} {remote}.lab-new"
               f" && mv -f {remote}.lab-new {remote}; rm -f /tmp/lab-deploy; sync")
            state = sh(f"sha256sum {remote} | cut -d' ' -f1; stat -c %a {remote}").split()
            require(state == [sha(blob), args.mode], f"{remote} did not install as intended: {state}")
            print(f"  installed {remote} ({len(blob)} bytes, mode {args.mode})")
    finally:
        if root_was == "ro":
            sh("sync; mount -o remount,ro /")
    boot_critical_ok()


def _restart_shell(restart_command: str | None = None) -> str:
    """Quiesce only writable mounted filesystems; undo our changes on refusal."""
    command = r"""cd /;
mount_mode() { awk -v target="$1" '$2 == target { n=split($4, opts, ","); for (i=1; i<=n; i++) if (opts[i] == "rw" || opts[i] == "ro") { print opts[i]; exit } }' /proc/mounts; };
root_mode=$(mount_mode /); data_mode=$(mount_mode /data); card_mode=$(mount_mode /mnt/sdcard);
case "$root_mode:$data_mode:$card_mode" in rw:rw:|rw:ro:|ro:rw:|ro:ro:|rw:rw:rw|rw:rw:ro|rw:ro:rw|rw:ro:ro|ro:rw:rw|ro:rw:ro|ro:ro:rw|ro:ro:ro) ;; *) echo RESTART_FAILED_MOUNT_STATE; exit 1;; esac;
root_changed=0; data_changed=0; card_changed=0;
paused=$(ps | grep -E '[S]ystem/slot|[n]extui.elf|[m]inarch.elf' | awk '{print $1}');
resume() { for pid in $paused; do kill -CONT "$pid" 2>/dev/null; done; };
restore_mounts() {
    if test "$root_changed" = 1; then /bin/mount -o remount,rw / || echo RESTART_FAILED_RESTORE_ROOT; fi;
    if test "$data_changed" = 1; then /bin/mount -o remount,rw /data || echo RESTART_FAILED_RESTORE_DATA; fi;
    if test "$card_changed" = 1; then /bin/mount -o remount,rw /mnt/sdcard || echo RESTART_FAILED_RESTORE_CARD; fi;
};
failed() { restore_mounts; resume; echo "RESTART_FAILED_$1"; exit 1; };
quiesce() {
    test "$2" = rw || return 0;
    /bin/mount -o remount,ro "$1"; mount_status=$?;
    current_mode=$(mount_mode "$1");
    if test "$current_mode" = ro; then
        case "$1" in /) root_changed=1;; /data) data_changed=1;; /mnt/sdcard) card_changed=1;; esac;
    fi;
    test "$mount_status" = 0 && test "$current_mode" = ro;
};
for pid in $paused; do kill -STOP "$pid" 2>/dev/null; done;
if test "$data_mode" = rw; then /bin/dd if=/dev/urandom of=/data/random-seed bs=512 count=1 2>/dev/null || failed SEED; fi;
/bin/sync || failed SYNC;
quiesce /mnt/sdcard "$card_mode" || failed CARD;
quiesce /data "$data_mode" || failed DATA;
quiesce / "$root_mode" || failed ROOT;
unset SHARED_USERDATA_PATH;
LD_LIBRARY_PATH=/usr/lib:/usr/lib/aarch64-linux-gnu /tmp/reboot_next;
failed COMMAND_RETURNED"""
    if restart_command is not None:
        command = command.replace("/tmp/reboot_next;", restart_command + ";")
    return command


def reboot(restart_command: str | None = None, *, require_frontend=True) -> None:
    boot_critical_ok()
    old = sh("cat /proc/sys/kernel/random/boot_id")
    subprocess.run(ADB + ["push", str(WORK / "backup/reboot_next"), "/tmp/reboot_next"], check=True, capture_output=True)
    require(sh("chmod 755 /tmp/reboot_next; sha256sum /tmp/reboot_next").split()[0] == REBOOT_NEXT_SHA, "reboot_next differs")
    command = _restart_shell(restart_command)
    try:
        result = raw_shell(command, 15).stdout
        require("RESTART_FAILED" not in result and "every shutdown method failed" not in result, result)
    except subprocess.TimeoutExpired:
        pass  # the USB link drops on a successful restart
    deadline = time.monotonic() + 150
    while time.monotonic() < deadline:
        try:
            check = ("cat /run/boot-frontend-exec" if require_frontend else
                     "test -e /dev/mali0 && test -e /dev/fb0 && test -d /sys/class/net/wlan0 && echo ready")
            state = sh("cat /proc/sys/kernel/random/boot_id; " + check, 4).split()
            if len(state) == 2 and state[0] != old and (float(state[1]) > 0 if require_frontend else state[1] == "ready"):
                return
        except (RuntimeError, ValueError, subprocess.TimeoutExpired):
            pass
        time.sleep(0.5)
    raise SystemExit("lab: no fresh boot with the required readiness after 150 s; stopping")


def bootevent(text: str) -> dict:
    rows = {}
    for line in text.splitlines():
        m = re.match(r"\s*([\d.]+) :\s+\S+\s+: initcall: (\S+)(?: \[\S+\])?\s+([\d.]+)ms", line)
        if m:
            rows[m.group(2)] = float(m.group(3))
        m = re.match(r"\s*([\d.]+) :.*Kernel_init_done", line)
        if m:
            rows["Kernel_init_done"] = float(m.group(1))
    return rows


def collect(label: str, *, require_frontend=True) -> dict:
    early = sh("cat /proc/uptime; dmesg -r", 20)
    while float(sh("cat /proc/uptime").split()[0]) < 14:
        time.sleep(1)
    handoff = list(map(float, sh("cat /run/boot-clock-handoff 2>/dev/null || true").split()))
    if not handoff and require_frontend:
        # Releases before 1.3.0 have no boot-clock. Its pre-kernel figure, the
        # hardware counter minus the kernel's raw uptime, is the same at any
        # moment of a boot, so run the tool now and add the handoff uptime.
        subprocess.run(ADB + ["push", str(ROOT / "work/tools/boot-clock"), "/tmp/boot-clock"], check=True, capture_output=True)
        legacy = float(sh("cat /run/boot-frontend-exec"))
        pre = float(sh("chmod 755 /tmp/boot-clock; /tmp/boot-clock").split()[1])
        handoff = [legacy, pre, legacy, round(pre + legacy, 3)]
    if not handoff and not require_frontend: handoff = [None] * 4
    require(len(handoff) == 4, "no boot-clock record")
    events = sh("cat /proc/bootevent")
    health = sh("test -e /dev/mali0 && echo GPU_OK; test -d /sys/class/net/wlan0 && echo WIFI_OK;"
                " test -e /dev/fb0 && echo FB_OK; grep -c . /proc/asound/cards; ls /dev/input | grep -c event;"
                " ls /sys/bus/usb/devices | grep -c '^usb'; cat /sys/class/power_supply/axp2202-battery/capacity;"
                " ps | grep -c -E '[S]ystem/slot$|[n]extui.elf$' || true")
    data = {
        "label": label, "installed": (WORK / "installed").read_text().strip() if (WORK / "installed").exists() else "backup",
        "boot_id": sh("cat /proc/sys/kernel/random/boot_id"),
        "frontend_required": require_frontend,
        "legacy_handoff_s": handoff[0], "pre_kernel_s": handoff[1], "post_kernel_s": handoff[2], "combined_s": handoff[3],
        "cmdline": sh("cat /proc/cmdline"), "root_mount": sh("grep ' / ' /proc/mounts"),
        # Which card became the frontend card: a faster boot can reach the card
        # mount before the second slot has finished detecting its card.
        "frontend_card": sh("grep ' /mnt/sdcard ' /proc/mounts | cut -d' ' -f1,3"),
        "initcalls_ms": bootevent(events), "health": health.split(),
        "warnings": [l for l in early.splitlines() if re.search(r"recover|EXT4-fs error|panic|Oops|not properly unmounted|\bVFS:", l, re.I)],
    }
    runs = WORK / "runs"
    runs.mkdir(exist_ok=True)
    n = 1
    while (runs / f"{label}-{n}.json").exists():
        n += 1
    (runs / f"{label}-{n}.json").write_text(json.dumps(data, indent=2) + "\n")
    (runs / f"{label}-{n}.dmesg").write_text(early + "\n")
    (runs / f"{label}-{n}.bootevent").write_text(events + "\n")
    timing = (f"pre {handoff[1]:.3f}  post {handoff[2]:.3f}  combined {handoff[3]:.3f}"
              if handoff[1] is not None else "OS control; no frontend benchmark")
    print(f"  {label}-{n}: {timing}"
          f"  kernel {data['initcalls_ms'].get('Kernel_init_done', 0) / 1000:.3f}  card {data['frontend_card']}"
          f"  health {' '.join(data['health'])}", flush=True)
    return data


def summary(label: str) -> None:
    rows = [json.loads(p.read_text()) for p in sorted((WORK / "runs").glob(f"{label}-*.json"))]
    if not rows:
        return
    line = [f"{label} (n={len(rows)})"]
    for key in ("pre_kernel_s", "post_kernel_s", "combined_s"):
        values = [r[key] for r in rows]
        line.append(f"{key[:-2]} {statistics.median(values):.3f} ({min(values):.3f}-{max(values):.3f})")
    kernel = [r["initcalls_ms"].get("Kernel_init_done", 0) / 1000 for r in rows]
    line.append(f"kernel {statistics.median(kernel):.3f}")
    print("  ".join(line))


def measure(args) -> None:
    identity()
    for _ in range(args.n):
        reboot()
        collect(args.label)
    summary(args.label)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("backup").set_defaults(run=backup)
    b = sub.add_parser("build")
    b.add_argument("name")
    b.add_argument("--ramdisk", choices=("empty",))
    b.add_argument("--cmdline")
    b.add_argument("--dtb", action="append", default=[])
    b.add_argument("--boot0-debug", type=int)
    b.set_defaults(run=build)
    f = sub.add_parser("flash")
    f.add_argument("name")
    f.set_defaults(run=flash)
    sub.add_parser("restore").set_defaults(run=restore)
    m = sub.add_parser("measure")
    m.add_argument("label")
    m.add_argument("-n", type=int, default=3)
    m.set_defaults(run=measure)
    d = sub.add_parser("deploy")
    d.add_argument("--mode", default="755")
    d.add_argument("files", nargs="+")
    d.set_defaults(run=deploy)
    c = sub.add_parser("collect")
    c.add_argument("label")
    c.set_defaults(run=lambda a: collect(a.label))
    s = sub.add_parser("summary")
    s.add_argument("label", nargs="+")
    s.set_defaults(run=lambda a: [summary(l) for l in a.label])
    args = parser.parse_args()
    WORK.mkdir(parents=True, exist_ok=True)
    args.run(args)


if __name__ == "__main__":
    main()
