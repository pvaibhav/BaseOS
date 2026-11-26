#!/usr/bin/env python3
"""Exercise the real update packager against disposable ext4 rootfs images."""
import gzip
import hashlib
import shutil
import struct
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GENERATION = bytes(range(32))
UPDATER = b"#!/bin/sh\n# the slot's own updater\n"


class UpdatePackage(unittest.TestCase):
    def setUp(self):
        for tool in ("mke2fs", "debugfs"):
            self.assertIsNotNone(shutil.which(tool), f"test requires {tool}")
        self.temporary = tempfile.TemporaryDirectory(prefix="mkupdate-test-")
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.root = self.directory / "root"
        (self.root / "etc").mkdir(parents=True)
        (self.root / "etc/baseos-release").write_text(
            "BASEOS_VERSION=9.1.2\nBASEOS_BUILD=fixture\nBASEOS_TARGET=rg40xxv\n"
        )
        self.image = self.directory / "card.img"
        self.output = self.directory / "system.bosupd"

    def native(self):
        (self.root / "etc/baseos").mkdir()
        (self.root / "etc/baseos/native-generation").write_bytes(GENERATION)
        (self.root / "usr/sbin").mkdir(parents=True)
        helper = self.root / "usr/sbin/native-health"
        helper.write_bytes(b"#!/bin/sh\nexit 0\n")
        helper.chmod(0o755)
        (self.root / "usr/sbin/baseos-update").write_bytes(UPDATER)
        (self.root / "usr/sbin/baseos-update").chmod(0o755)

    def package(self):
        slot = self.directory / "rootfs.img"
        with slot.open("wb") as handle:
            handle.truncate(8 * 1024 * 1024)
        subprocess.run(
            ["mke2fs", "-q", "-F", "-t", "ext4", "-d", str(self.root), str(slot)],
            check=True, capture_output=True,
        )
        header = bytearray(2048 * 512)
        header[512:520] = b"EFI PART"
        struct.pack_into("<QII", header, 512 + 72, 2, 8, 128)
        entry = 2 * 512 + 4 * 128
        struct.pack_into("<QQ", header, entry + 32, 2048, 2048 + 16384 - 1)
        name = "rootfs".encode("utf-16le")
        header[entry + 56:entry + 56 + len(name)] = name
        with self.image.open("wb") as handle:
            handle.write(header)
            handle.write(slot.read_bytes())
        return subprocess.run(
            [sys.executable, str(ROOT / "tools/mkupdate.py"), str(self.image),
             "rg40xxv", "9.1.2", "fixture", str(self.output)],
            capture_output=True, text=True,
        ), slot

    def accepted(self, generation):
        result, slot = self.package()
        self.assertEqual(result.returncode, 0, result.stderr)
        with tarfile.open(self.output) as archive:
            # Native payloads carry their own updater last, after what older updaters read.
            self.assertEqual(archive.getnames(), ["manifest", "rootfs.img.gz"] + (["baseos-update"] if generation else []))
            if generation:
                self.assertEqual(archive.extractfile("baseos-update").read(), UPDATER)
            manifest = dict(line.split("=", 1) for line in archive.extractfile("manifest").read().decode().splitlines())
            payload = gzip.decompress(archive.extractfile("rootfs.img.gz").read())
        self.assertEqual(payload, slot.read_bytes())
        self.assertEqual(manifest["image-sha256"], hashlib.sha256(payload).hexdigest())
        self.assertEqual(manifest.get("native-generation"), generation)

    def refused(self, reason):
        result, _ = self.package()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(reason, result.stderr)
        self.assertFalse(self.output.exists())

    def test_legacy_rootfs_stays_legacy_despite_unrelated_generation_bytes(self):
        (self.root / "etc/decoy").write_bytes(GENERATION + b"native-generation=" + GENERATION.hex().encode())
        self.accepted(None)

    def test_native_raw_generation_and_helper_are_read_from_ext4(self):
        self.native()
        self.accepted(GENERATION.hex())

    def test_native_rootfs_without_updater_refused(self):
        self.native()
        (self.root / "usr/sbin/baseos-update").unlink()
        self.refused("must contain /usr/sbin/baseos-update")

    def test_empty_short_long_or_hex_marker_refused(self):
        for data in (b"", GENERATION[:-1], GENERATION + b"\0", GENERATION.hex().encode()):
            with self.subTest(length=len(data)):
                if not (self.root / "etc/baseos").exists():
                    self.native()
                (self.root / "etc/baseos/native-generation").write_bytes(data)
                self.refused("regular raw32-byte")

    def test_marker_symlink_refused(self):
        self.native()
        marker = self.root / "etc/baseos/native-generation"
        marker.rename(marker.with_name("real-generation"))
        marker.symlink_to("real-generation")
        self.refused("regular raw32-byte")

    def test_marker_directory_refused(self):
        self.native()
        marker = self.root / "etc/baseos/native-generation"
        marker.unlink()
        marker.mkdir()
        self.refused("regular raw32-byte")

    def test_policy_parent_symlink_refused(self):
        self.native()
        parent = self.root / "etc/baseos"
        parent.rename(parent.with_name("real-baseos"))
        parent.symlink_to("real-baseos")
        self.refused("not a directory: /etc/baseos")

    def test_legacy_etc_symlink_refused(self):
        (self.root / "etc").rename(self.root / "real-etc")
        (self.root / "etc").symlink_to("real-etc")
        self.refused("not a directory: /etc")

    def test_native_helper_missing_nonexecutable_or_symlink_refused(self):
        self.native()
        helper = self.root / "usr/sbin/native-health"
        helper.unlink()
        self.refused("regular executable")
        helper.write_bytes(b"helper")
        helper.chmod(0o644)
        self.refused("regular executable")
        helper.rename(helper.with_name("real-helper"))
        helper.symlink_to("real-helper")
        self.refused("regular executable")

    def test_native_helper_parent_symlink_refused(self):
        self.native()
        parent = self.root / "usr/sbin"
        parent.rename(parent.with_name("real-sbin"))
        parent.symlink_to("real-sbin")
        self.refused("not a directory: /usr/sbin")


if __name__ == "__main__":
    unittest.main(verbosity=2)
