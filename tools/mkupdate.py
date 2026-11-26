#!/usr/bin/env python3
"""Build a BaseOS `.bosupd` system-update payload from a composed image.

The payload is the active rootfs slot of a finished image, gzipped, plus a
manifest. Applying it writes those exact bytes into the *inactive* slot of an
installed card and flips the GPT, so the updated device ends up byte-identical
to a freshly flashed one — the same filesystem verified by the same hash.

Integrity is a SHA-256 over the decompressed slot image, printed here so it can
be published next to the download. There is no signature: a signing key would
be a single point of failure for every update, and these images carry no
secrets.

Usage: mkupdate.py IMAGE TARGET VERSION BUILD OUTPUT
"""
import gzip
import argparse
import hashlib
import io
import re
import shutil
import struct
import subprocess
import sys
import tarfile
import tempfile
import uuid
from pathlib import Path

import boot_payload

SECTOR = 512
CHUNK = 4 * 1024 * 1024
ROOTFS_INDEX = 4


def die(msg):
    sys.exit(f"mkupdate: {msg}")


def rootfs_extent(image: Path) -> tuple[int, int]:
    """Return (start_sector, sector_count) of the `rootfs` partition."""
    with image.open("rb") as handle:
        handle.seek(SECTOR)
        header = handle.read(SECTOR)
        if header[:8] != b"EFI PART":
            die("no primary GPT in the image")
        entries_lba = struct.unpack_from("<Q", header, 72)[0]
        count, size = struct.unpack_from("<II", header, 80)
        handle.seek(entries_lba * SECTOR)
        entry = handle.read(count * size)[ROOTFS_INDEX * size : (ROOTFS_INDEX + 1) * size]
    name = entry[56:128].decode("utf-16-le").rstrip("\0")
    if name != "rootfs":
        die(f"partition 5 is {name!r}, expected 'rootfs'")
    start, end = struct.unpack_from("<QQ", entry, 32)
    return start, end - start + 1


class RootPolicy:
    """Read boot policy through ext4 inodes, never by searching image bytes."""
    def __init__(self, slot):
        self.slot = slot
        self.debugfs = shutil.which("debugfs")
        if not self.debugfs:
            die("debugfs is required to inspect rootfs boot compatibility (use build-update.sh)")

    def inode(self, path):
        result = subprocess.run(
            [self.debugfs, "-R", f"stat {path}", str(self.slot)], capture_output=True, check=False
        )
        output = result.stdout.decode("utf-8", "replace")
        error = result.stderr.decode("utf-8", "replace")
        if result.returncode:
            die(f"cannot inspect rootfs {path}: {error.strip()}")
        match = re.search(r"^Inode:\s+\d+\s+Type:\s+(\S+)\s+Mode:\s+([0-7]+)", output, re.M)
        if not match:
            if "File not found by ext2_lookup" in error:
                return None
            die(f"cannot inspect rootfs {path}: {error.strip() or output.strip()}")
        size = re.search(r"^User:.*?\bSize:\s+(\d+)", output, re.M)
        if not size:
            die(f"cannot read rootfs inode size for {path}")
        return match[1], int(match[2], 8), int(size[1])

    def read(self, path, size=None):
        record = self.inode(path)
        if record is None:
            return None
        if record[0] != "regular" or (size is not None and record[2] != size):
            die(f"rootfs {path} must be a regular " + (f"raw{size}-byte file" if size else "file"))
        result = subprocess.run([self.debugfs, "-R", f"cat {path}", str(self.slot)], capture_output=True, check=False)
        if result.returncode or len(result.stdout) != record[2]:
            die(f"cannot read rootfs {path}")
        return result.stdout

    def helper(self):
        for path in ("/usr", "/usr/sbin"):
            record = self.inode(path)
            if record is None or record[0] != "directory":
                die(f"native helper path is not a directory: {path}")
        helper = self.inode("/usr/sbin/native-health")
        if helper is None or helper[0] != "regular" or not helper[1] & 0o111 or not helper[2]:
            die("native rootfs must contain a regular executable /usr/sbin/native-health")


def native_policy(slot: Path, target: str) -> dict:
    policy = RootPolicy(slot)
    with slot.open("rb") as stream:
        stream.seek(boot_payload.TAIL_OFFSET)
        has_tail = stream.read(len(boot_payload.MAGIC)) == boot_payload.MAGIC
    for path in ("/etc", "/etc/baseos"):
        record = policy.inode(path)
        if record is None:
            if has_tail: die("sequential tail has no rootfs boot policy")
            return {}
        if record[0] != "directory":
            die(f"rootfs boot policy path is not a directory: {path}")
    # The unfinished sequential (format2) prototype is not packaged.
    if has_tail or any(policy.inode(path) is not None for path in
                       ("/etc/baseos/native-abi", "/etc/baseos/boot-payload-id", "/etc/baseos/boot-payload-format")):
        die("sequential (format2) rootfs boot policy is not supported")
    marker = "/etc/baseos/native-generation"
    record = policy.inode(marker)
    if record is None:
        return {}
    if record[0] != "regular" or record[2] != 32:
        die("rootfs native-generation must be a regular raw32-byte file")
    generation = policy.read(marker, 32)
    if generation is None:
        die("cannot read the rootfs raw32-byte native generation")
    policy.helper()
    # Shipped beside the slot (after it, so every earlier updater, which reads
    # only manifest and rootfs.img.gz, ignores it): a card whose installed
    # updater refuses this payload can apply it with the updater it carries.
    updater = policy.read("/usr/sbin/baseos-update")
    if not updater:
        die("native rootfs must contain /usr/sbin/baseos-update")
    return {"native-generation": generation.hex(), "_updater": updater}


def package_image(image, target, version, build):
    start, sectors = rootfs_extent(image)
    size = sectors * SECTOR
    print(f"rootfs slot at LBA {start}, {sectors} sectors ({size} bytes)")

    # Stream the slot once: hash it, compress it, and confirm the identity
    # baked into the filesystem matches what we are about to claim about it.
    # Both stamps matter: a manifest that claims a version the filesystem does
    # not carry would make the target apply it, boot, still disagree about its
    # own version, and apply it again.
    digest = hashlib.sha256()
    compressed = io.BytesIO()
    stamps = {
        f"BASEOS_VERSION={version}".encode(): False,
        f"BASEOS_BUILD={build}".encode(): False,
    }
    window = max(len(stamp) for stamp in stamps)
    tail = b""
    with tempfile.NamedTemporaryFile(prefix="baseos-update-rootfs-") as slot:
        with image.open("rb") as handle, gzip.GzipFile(
            fileobj=compressed, mode="wb", compresslevel=6, mtime=0
        ) as gz:
            handle.seek(start * SECTOR)
            remaining = size
            while remaining:
                chunk = handle.read(min(CHUNK, remaining))
                if not chunk:
                    die("image is shorter than the rootfs partition it declares")
                remaining -= len(chunk)
                digest.update(chunk)
                gz.write(chunk)
                slot.write(chunk)
                haystack = tail + chunk
                for stamp, seen in stamps.items():
                    if not seen and stamp in haystack:
                        stamps[stamp] = True
                tail = chunk[-window:]
        slot.flush()
        policy = native_policy(Path(slot.name), target)

    for stamp, seen in stamps.items():
        if not seen:
            die(f"the image does not contain {stamp.decode()} — rebuild the rootfs first")

    manifest = "".join(
        f"{key}={value}\n"
        for key, value in (
            ("format", "baseos-update/1"),
            ("target", target),
            ("version", version),
            ("build", build),
            ("slot-sectors", sectors),
            ("image-size", size),
            ("image-sha256", digest.hexdigest()),
        )
    )
    manifest += "".join(f"{key}={value}\n" for key, value in policy.items() if not key.startswith("_"))
    manifest = manifest.encode()
    return manifest, compressed.getvalue(), policy


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image", type=Path)
    for name in ("target", "version", "build"): parser.add_argument(name)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    output = args.output
    manifest, payload, policy = package_image(args.image, args.target, args.version, args.build)
    members = [("manifest", manifest), ("rootfs.img.gz", payload)]
    if "_updater" in policy:
        members.append(("baseos-update", policy["_updater"]))
    output.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(output, "w", format=tarfile.USTAR_FORMAT) as archive:
        for name, blob in members:
            info = tarfile.TarInfo(name)
            info.size = len(blob)
            info.mtime = 0
            info.uid = info.gid = 0
            info.uname = info.gname = "root"
            archive.addfile(info, io.BytesIO(blob))

    print(f"payload:  {output} ({output.stat().st_size} bytes)")
    print(f"target:   {args.target}")
    print(f"version:  {args.version} ({args.build})")
    for name, blob in members:
        if name == "manifest" or name.startswith("manifest-"):
            metadata = dict(line.split("=", 1) for line in blob.decode().splitlines())
            if "image-sha256" in metadata:
                print(f"sha256:   {metadata['image-sha256']}    ({name}; decompressed whole slot)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
