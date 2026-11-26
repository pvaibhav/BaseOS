#!/usr/bin/env python3
"""Restore only this experiment's exact original boot partition and package.

Run as administrator with --device /dev/rdiskN. Refuses any card without the
expected BaseOS GPT and a package/image hash installed by trial.py. Does not
write boot0, filesystems, partition tables, frontend files, or saved games.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import plistlib
import re
import struct
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORK = ROOT / "work/h700-loader"
PACKAGE_OFFSET = 32800 * 512
BOOT_OFFSET = 303104 * 512


def require(value, message):
    if not value:
        raise SystemExit("restore-card: " + message)


def sha(blob):
    return hashlib.sha256(blob).hexdigest()


def read_at(handle, offset, length):
    handle.seek(offset)
    result = bytearray()
    while len(result) < length:
        block = handle.read(min(4 * 1024 * 1024, length - len(result)))
        require(block, "short card read")
        result += block
    return bytes(result)


def write_at(handle, offset, blob):
    handle.seek(offset)
    for start in range(0, len(blob), 4 * 1024 * 1024):
        part = memoryview(blob)[start:start + 4 * 1024 * 1024]
        while part:
            count = handle.write(part)
            require(count, "short card write; do not boot")
            part = part[count:]
    os.fsync(handle.fileno())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device", required=True)
    args = parser.parse_args()
    require(os.geteuid() == 0, "administrator privileges required")
    require(re.fullmatch(r"/dev/rdisk\d+", args.device), "expected a whole raw disk")
    disk = args.device.replace("/rdisk", "/disk")
    info = plistlib.loads(subprocess.check_output(["diskutil", "info", "-plist", disk]))
    require(info.get("WholeDisk") and info.get("Removable") and not info.get("Internal")
            and info.get("TotalSize") == 63864569856
            and info.get("DeviceBlockSize") == 512, "not the connected 63.9 GB removable lab card")
    boot = (WORK / "baseline-boot-partition.bin").read_bytes()
    backup = json.loads((WORK / "baseline-boot-partition.json").read_text())
    require(len(boot) == backup["bytes"] == 67108864 and sha(boot) == backup["sha256"], "boot backup differs")
    package = (WORK / "baseline-package.bin").read_bytes()
    require(len(package) == 1310720 and sha(package) ==
            "32f92785f3e0819acbd60776e2ef839c1fd1924b31960b98363a93c85300db7e", "package backup differs")
    packages = json.loads((WORK / "installed-packages.json").read_text())
    images = json.loads((WORK / "installed-images.json").read_text())
    known_images = []
    for digest, filename in images.items():
        blob = Path(filename).read_bytes()
        require(sha(blob) == digest, "installed image artifact changed")
        known_images.append(blob)
    subprocess.run(["diskutil", "unmountDisk", disk], check=True)
    with open(args.device, "r+b", buffering=0) as card:
        head = read_at(card, 0, 32768)
        require(head[512:520] == b"EFI PART", "card has no GPT")
        lba = struct.unpack_from("<Q", head, 584)[0]
        count, stride = struct.unpack_from("<II", head, 592)
        require(stride == 128 and count <= 128 and lba * 512 + count * stride <= len(head), "unexpected GPT table")
        parts = {}
        for index in range(count):
            entry = head[lba * 512 + index * stride:lba * 512 + (index + 1) * stride]
            first, last = struct.unpack_from("<QQ", entry, 32)
            parts[index + 1] = (entry[56:128].decode("utf-16le").rstrip("\0"), first, last - first + 1)
        require(parts[4] == ("boot", 303104, 131072) and parts[6] == ("UDISK", 2531328, 262144)
                and parts[7][0] == "primary", "card partition layout differs")
        current_package = read_at(card, PACKAGE_OFFSET, len(package))
        current_boot = read_at(card, BOOT_OFFSET, len(boot))
        require(sha(current_package) in packages, "unknown on-card package")
        require(current_boot == boot or any(current_boot == blob + boot[len(blob):] for blob in known_images),
                "on-card boot partition differs from all installed candidates")
        saved = WORK / "recovery-before"
        saved.mkdir(exist_ok=True)
        for name, blob in (("boot.bin", current_boot), ("package.bin", current_package)):
            path = saved / name
            require(not path.exists() or path.read_bytes() == blob, "previous recovery snapshot differs")
            path.write_bytes(blob)
            os.chown(path, WORK.stat().st_uid, WORK.stat().st_gid)
        print("Identity, GPT, on-card state and both original backups verified.", flush=True)
        write_at(card, BOOT_OFFSET, boot)
        require(read_at(card, BOOT_OFFSET, len(boot)) == boot, "boot readback differs; do not boot")
        write_at(card, PACKAGE_OFFSET, package)
        require(read_at(card, PACKAGE_OFFSET, len(package)) == package, "package readback differs; do not boot")
    print(f"RESTORED boot {sha(boot)}\nRESTORED package {sha(package)}", flush=True)
    subprocess.run(["diskutil", "eject", disk], check=True)


if __name__ == "__main__":
    main()
