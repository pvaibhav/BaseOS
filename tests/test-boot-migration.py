#!/usr/bin/env python3
"""Run inside Alpine: file-backed fault tests of the actual BusyBox migrator.

The card is two ordinary files (whole disk and partition 4) under a test root.
Every dd the migrator runs goes through a logging wrapper, so the tests can
assert not only the final bytes but which boot areas were read and written, in
what order, and can make any single write refuse, tear or silently corrupt.
"""
import hashlib
import os
from pathlib import Path
import random
import shutil
import struct
import subprocess
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "overlay/usr/sbin/baseos-boot-migrate"

# boot0 uses the real layout. The package sits at a sector that is not page
# aligned and every size is a whole number of sectors but not of pages, so the
# migrator's sector arithmetic is exercised rather than flattered.
BOOT0_OFFSET, BOOT0_SIZE, DEBUG_OFFSET, BODY_OFFSET = 8192, 65536, 52, 512
SPARE_WORD = 0x60  # a header word no check looks at; used to steer checksums
PACKAGE_OFFSET, PACKAGE_SIZE = 82432, 6656
IMAGE_SIZE = dict(original=43008, gzip=26624, fast=18432)
PARTITION_SIZE = 81920
STAMP = 0x5F0A6C39
SPLASH = "--important -1 OPTIMISING STARTUP: KEEP POWER ON"
FAILED_SPLASH = "--important -1 BOOT UPDATE FAILED: KEEP POWER ON"
FIRST_READS = ["read package", "read image", "read boot0"]
PATCH_BOOT0 = ["read boot0", "write boot0", "read boot0"]


def sha(data):
    return hashlib.sha256(data).hexdigest()


def boot0_checksum(blob):
    """Allwinner boot0 checksum, written independently of the migrator.

    Every little-endian word is added up, with the stored checksum word (at
    offset 12) counted as the stamp instead; the low 32 bits are the result.
    """
    total = STAMP
    for at in range(0, len(blob), 4):
        if at != 12:
            total += int.from_bytes(blob[at:at + 4], "little")
    return total & 0xFFFFFFFF


class Migration(unittest.TestCase):
    def test_native_package_is_left_to_the_native_step(self):
        """A card already carrying a native install: the pair step writes nothing."""
        native = self.root / "usr/share/baseos/native"
        native.mkdir(parents=True)
        for listed in ("NATIVE_TOC_SHA256", "NATIVE_PREVIOUS_TOC_SHA256"):
            with self.subTest(listed=listed):
                self.build()
                native.mkdir(parents=True, exist_ok=True)
                self.flash(self.packages["original"][::-1], "fast")
                other = "0" * 64
                package = sha(self.packages["original"][::-1])
                (native / "manifest").write_text(
                    f"NATIVE_TOC_SHA256={package if listed == 'NATIVE_TOC_SHA256' else other}\n"
                    f"NATIVE_PREVIOUS_TOC_SHA256=\"{other} {package}\"\n")
                self.run_migration()
                self.assert_untouched()
                self.assertEqual(self.ops(), ["read package", "read image"])
                self.assertEqual(self.marker(), "native boot files present")

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.build()

    def build(self, **manifest):
        """(Re)create the whole fake device. Loops call this between cases."""
        self.root = Path(self.temp.name) / "root"
        if self.root.exists():
            shutil.rmtree(self.root)
        self.assets = self.root / "usr/share/baseos/boot-gzip"
        self.state = self.root / "data/boot-pair-v2"
        self.first = self.root / "data/boot-gzip-v1"
        for name in ("dev", "etc", "run", "tmp", "bin", "data", "usr/bin",
                     "sys/class/block/mmcblk0p4", "sys/class/power_supply/battery",
                     str(self.assets.relative_to(self.root))):
            (self.root / name).mkdir(parents=True, exist_ok=True)
        (self.root / ".boot-migration-test").touch()
        (self.root / "etc/baseos-release").write_text("BASEOS_TARGET=rgsp\n")
        rng = random.Random(20261013)
        self.packages = {name: rng.randbytes(PACKAGE_SIZE) for name in IMAGE_SIZE}
        self.images = {name: rng.randbytes(size) for name, size in IMAGE_SIZE.items()}
        self.partition_base = rng.randbytes(PARTITION_SIZE)
        self.header = rng.randbytes(BODY_OFFSET)
        self.body = rng.randbytes(BOOT0_SIZE - BODY_OFFSET)
        self.head = rng.randbytes(BOOT0_OFFSET)
        self.gap = rng.randbytes(PACKAGE_OFFSET - BOOT0_OFFSET - BOOT0_SIZE)
        self.tail = rng.randbytes(4096)
        self.boot = self.root / "dev/mmcblk0p4"
        self.disk = self.root / "dev/mmcblk0"
        (self.assets / "boot.img").write_bytes(self.images["fast"])
        (self.assets / "boot-package.bin").write_bytes(self.packages["fast"])
        (self.root / "sys/class/block/mmcblk0p4/start").write_text("4096\n")
        (self.root / "sys/class/block/mmcblk0p4/size").write_text(f"{PARTITION_SIZE // 512}\n")
        (self.root / "sys/class/power_supply/battery/capacity").write_text("100\n")
        self.write_manifest(**manifest)
        splash = self.root / "usr/bin/baseos-splash"
        splash.write_text('#!/bin/sh\nprintf \'%s\\n\' "$*" >> "$BASEOS_BOOT_TEST_ROOT/splash.log"\n')
        splash.chmod(0o755)
        self.inject("")
        self.env = dict(os.environ, BASEOS_BOOT_TESTING="1", BASEOS_BOOT_TEST_ROOT=str(self.root),
                        PATH=f"{self.root}/bin:/usr/sbin:/usr/bin:/sbin:/bin")
        self.flash("gzip", "original", "gzip")

    def write_manifest(self, **override):
        """The manifest contract, generated here from the fixture's own bytes."""
        original = self.partition("original")
        fast = self.images["fast"]
        fields = dict(
            TARGET="rgsp", PACKAGE_OFFSET=PACKAGE_OFFSET, PACKAGE_SIZE=PACKAGE_SIZE,
            BOOT_START=4096, BOOT_SECTORS=PARTITION_SIZE // 512, BOOT_IMAGE_SIZE=len(fast),
            PACKAGE_SHA256=sha(self.packages["fast"]), BOOT_IMAGE_SHA256=sha(fast),
            ORIGINAL_PACKAGE_SHA256=sha(self.packages["original"]), ORIGINAL_BOOT_SHA256=sha(original),
            BOOT_PARTITION_SHA256=sha(fast + original[len(fast):]),
            KERNEL_SHA256=sha(b"kernel"), KERNEL_SIZE=6,
            ORIGINAL_BOOT_IMAGE_SIZE=IMAGE_SIZE["original"],
            ORIGINAL_BOOT_IMAGE_SHA256=sha(self.images["original"]),
            GZIP_PACKAGE_SHA256=sha(self.packages["gzip"]), GZIP_BOOT_IMAGE_SIZE=IMAGE_SIZE["gzip"],
            GZIP_BOOT_IMAGE_SHA256=sha(self.images["gzip"]),
            BOOT0_OFFSET=BOOT0_OFFSET, BOOT0_SIZE=BOOT0_SIZE, BOOT0_DEBUG_OFFSET=DEBUG_OFFSET,
            BOOT0_BODY_OFFSET=BODY_OFFSET, BOOT0_BODY_SHA256=sha(self.body),
            BOOT0_ORIGINAL_SHA256=sha(self.make_boot0()))
        fields.update(override)
        (self.assets / "manifest").write_text(
            "".join(f"BOOT_{k}={v}\n" for k, v in fields.items() if v is not None))

    # ------------------------------------------------------------ the card

    def make_boot0(self, debug=3, checksum=None, body=None, magic=b"eGON.BT0", length=BOOT0_SIZE):
        """A boot0 whose stored checksum is valid, optionally steered to a value."""
        blob = bytearray(self.header + (self.body if body is None else body))
        blob[4:12] = magic
        struct.pack_into("<I", blob, 16, length)
        blob[DEBUG_OFFSET] = debug
        if checksum is not None:
            struct.pack_into("<I", blob, SPARE_WORD, 0)
            struct.pack_into("<I", blob, SPARE_WORD, (checksum - boot0_checksum(blob)) & 0xFFFFFFFF)
        struct.pack_into("<I", blob, 12, boot0_checksum(blob))
        self.assertEqual(len(blob), BOOT0_SIZE)
        return bytes(blob)

    def partition(self, *history):
        """Partition 4 after writing each named image in turn over what was there."""
        data = self.partition_base
        for name in history:
            data = self.images[name] + data[len(self.images[name]):]
        return data

    def flash(self, package, *history, boot0=None):
        self.boot.write_bytes(self.partition(*history))
        package = self.packages.get(package, package)
        self.disk.write_bytes(self.head + (boot0 or self.make_boot0()) + self.gap + package + self.tail)
        self.before_disk, self.before_boot = self.disk.read_bytes(), self.boot.read_bytes()

    def package(self, disk=None):
        return (disk or self.disk.read_bytes())[PACKAGE_OFFSET:PACKAGE_OFFSET + PACKAGE_SIZE]

    def boot0(self, disk=None):
        return (disk or self.disk.read_bytes())[BOOT0_OFFSET:BOOT0_OFFSET + BOOT0_SIZE]

    def save_first_migration_backup(self):
        self.first.mkdir()
        (self.first / "boot-original.bin").write_bytes(self.partition("original"))
        (self.first / "package-original.bin").write_bytes(self.packages["original"])
        (self.first / "complete").write_text("installed\n")
        (self.first / "migration.log").write_text("boot-gzip: gzip pair verified by direct readback\n")
        return self.first_files()

    def first_files(self):
        return {path.name: path.read_bytes() for path in self.first.iterdir()}

    # ------------------------------------------------------- running it

    def run_migration(self, expected=0):
        result = subprocess.run(["/bin/busybox", "sh", str(SCRIPT)], env=self.env,
                                capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, expected, result.stdout + result.stderr + self.log())
        # An unexpected shell error ends as exit 0 by design; its message is
        # the only trace, so no run may print one.
        self.assertEqual(result.stdout + result.stderr, "", self.log())
        return result

    def marker(self):
        return (self.state / "complete").read_text().strip()

    def log(self):
        path = self.state / "migration.log"
        return path.read_text() if path.exists() else ""

    def splashes(self):
        path = self.root / "splash.log"
        return path.read_text().splitlines() if path.exists() else []

    def backups(self):
        return sorted(path.name for path in self.state.iterdir()
                      if path.name not in ("complete", "migration.log"))

    def inject(self, body):
        wrapper = self.root / "bin/dd"
        wrapper.write_text('#!/bin/sh\nprintf \'%s\\n\' "$*" >> "$BASEOS_BOOT_TEST_ROOT/dd.log"\n'
                           + body + '\nexec /bin/busybox dd "$@"\n')
        wrapper.chmod(0o755)

    def fault(self, source, mode, device=None, offset=0):
        """Wrapper text making the device write that copies SOURCE misbehave.

        refuse: fail without writing. torn: write, damage four bytes of DEVICE
        at OFFSET, report failure. silent: the same damage, reported as success.
        """
        if mode == "refuse":
            return f'case "$*" in *"/{source} of="*"/dev/mmcblk0"*) exit 1 ;; esac\n'
        return (f'case "$*" in *"/{source} of="*"/dev/mmcblk0"*)\n'
                ' /bin/busybox dd "$@" || exit 1\n'
                f" printf '\\245\\245\\245\\245' | /bin/busybox dd of={device} bs=1 seek={offset}"
                ' conv=notrunc 2>/dev/null\n'
                f' exit {1 if mode == "torn" else 0} ;;\nesac\n')

    def dd_calls(self):
        path = self.root / "dd.log"
        return [line.split() for line in path.read_text().splitlines()] if path.exists() else []

    def ops(self):
        """Boot-area reads and writes in the order the migrator made them."""
        found = []
        for words in self.dd_calls():
            args = dict(word.split("=", 1) for word in words if "=" in word)
            source, target = args.get("if"), args.get("of")
            if target in (str(self.boot), str(self.disk)):
                # Never truncate a device stand-in; always flush before returning.
                self.assertEqual(args["conv"], "notrunc,fsync", words)
            if target == str(self.boot):
                self.assertNotIn("seek", args, words)
                found.append("write image")
            elif target == str(self.disk) and "oflag" in args:
                # boot0: exactly one sector, sent past the block cache so that
                # the kernel does not rewrite the 4 KiB block around it.
                self.assertEqual((args["bs"], args["count"], args["seek"], args["iflag"], args["oflag"]),
                                 ("1M", "512", str(BOOT0_OFFSET), "count_bytes", "direct,seek_bytes"), words)
                found.append("write boot0")
            elif target == str(self.disk):
                self.assertEqual((args["bs"], args["seek"]), ("512", str(PACKAGE_OFFSET // 512)), words)
                found.append("write package")
            elif source in (str(self.boot), str(self.disk)):
                # Reads must really bypass the page cache: BusyBox dd quietly
                # stops using O_DIRECT for a block smaller than a page.
                self.assertEqual((args["bs"], args["iflag"]), ("1M", "direct,skip_bytes,count_bytes"), words)
                if source == str(self.boot):
                    self.assertEqual(args["skip"], "0", words)
                    found.append("read image")
                else:
                    found.append({str(PACKAGE_OFFSET): "read package",
                                  str(BOOT0_OFFSET): "read boot0"}[args["skip"]])
        return found

    def writes(self):
        return [op for op in self.ops() if op.startswith("write")]

    # ------------------------------------------------------- assertions

    def assert_untouched(self):
        self.assertEqual(self.disk.read_bytes(), self.before_disk)
        self.assertEqual(self.boot.read_bytes(), self.before_boot)

    def assert_fast_pair(self):
        fast, after = self.images["fast"], self.boot.read_bytes()
        self.assertEqual(after[:len(fast)], fast)
        # Bytes of partition 4 beyond the written image are not the migrator's.
        self.assertEqual(after[len(fast):], self.before_boot[len(fast):])
        self.assertEqual(len(after), PARTITION_SIZE)
        disk = self.disk.read_bytes()
        self.assertEqual(self.package(disk), self.packages["fast"])
        # Nothing but the package and boot0's first sector may differ on the disk.
        for start, end in ((0, BOOT0_OFFSET), (BOOT0_OFFSET + BODY_OFFSET, PACKAGE_OFFSET),
                           (PACKAGE_OFFSET + PACKAGE_SIZE, len(self.before_disk))):
            self.assertEqual(disk[start:end], self.before_disk[start:end])
        self.assertEqual(len(disk), len(self.before_disk))

    def assert_boot0_patched(self):
        expected = bytearray(self.boot0(self.before_disk))
        expected[DEBUG_OFFSET] = 0
        struct.pack_into("<I", expected, 12, boot0_checksum(expected))
        self.assertEqual(self.boot0(), bytes(expected))

    def assert_boot0_untouched(self):
        self.assertEqual(self.boot0(), self.boot0(self.before_disk))

    def assert_work_removed(self):
        self.assertEqual(os.listdir(self.root / "tmp"), [])

    # ------------------------------------------------- successful paths

    def test_original_to_fast(self):
        self.flash("original", "original")
        self.run_migration()
        self.assertEqual(self.marker(), "installed")
        self.assert_fast_pair()
        self.assert_boot0_patched()
        # Original halves do not boot beside fast ones: the two writes are
        # adjacent, with no readback lengthening the unbootable window.
        self.assertEqual(self.ops(), FIRST_READS + ["write image", "write package",
                                                    "read image", "read package"] + PATCH_BOOT0)
        self.assertEqual(self.backups(), ["boot-image-original.bin", "package-original.bin"])
        self.assertEqual((self.state / "boot-image-original.bin").read_bytes(), self.images["original"])
        self.assertEqual((self.state / "package-original.bin").read_bytes(), self.packages["original"])
        self.assertIn("continuing startup", self.log())
        self.assertEqual(self.splashes(), [SPLASH])
        self.assertFalse(self.first.exists())
        self.assert_work_removed()
        # Completed path must work with zero external utilities available.
        self.env["PATH"] = "/nonexistent"
        self.run_migration()
        self.assertEqual(self.marker(), "installed")

    def test_original_to_fast_keeps_an_existing_first_backup(self):
        self.flash("original", "original")
        saved = self.save_first_migration_backup()
        self.run_migration()
        self.assertEqual(self.marker(), "installed")
        self.assert_fast_pair()
        self.assert_boot0_patched()
        self.assertEqual(self.backups(), [])
        self.assertEqual(self.first_files(), saved)

    def test_gzip_to_fast_with_first_migration_backup(self):
        saved = self.save_first_migration_backup()
        self.run_migration()
        self.assertEqual(self.marker(), "installed")
        self.assert_fast_pair()
        self.assert_boot0_patched()
        # Every intermediate state boots, so the image is proven before the
        # package, which has no second copy on the card, is touched.
        self.assertEqual(self.ops(), FIRST_READS + ["write image", "read image", "write package",
                                                    "read image", "read package"] + PATCH_BOOT0)
        # The original pair on userdata is a sufficient recovery copy: nothing
        # is duplicated and the first migration's directory is not modified.
        self.assertEqual(self.backups(), [])
        self.assertEqual(self.first_files(), saved)
        self.assertEqual(self.splashes(), [SPLASH])
        self.assert_work_removed()

    def test_gzip_to_fast_without_first_migration_backup(self):
        self.run_migration()
        self.assertEqual(self.marker(), "installed")
        self.assert_fast_pair()
        self.assert_boot0_patched()
        # Image bytes only: not the whole partition.
        self.assertEqual(self.backups(), ["boot-image-gzip.bin", "package-gzip.bin"])
        self.assertEqual((self.state / "boot-image-gzip.bin").read_bytes(), self.images["gzip"])
        self.assertEqual((self.state / "package-gzip.bin").read_bytes(), self.packages["gzip"])
        self.assertFalse(self.first.exists())

    def test_damaged_first_migration_backup_is_not_trusted_or_modified(self):
        for name in ("boot-original.bin", "package-original.bin"):
            self.build()
            self.save_first_migration_backup()
            (self.first / name).write_bytes(b"damaged")
            saved = self.first_files()
            self.run_migration()
            self.assertEqual(self.marker(), "installed")
            self.assert_fast_pair()
            self.assertEqual(self.backups(), ["boot-image-gzip.bin", "package-gzip.bin"])
            self.assertEqual(self.first_files(), saved)

    def test_interrupted_states_are_completed(self):
        # What a power cut between the two writes leaves: one fast half.
        for package, history, expected in (
                ("gzip", ("original", "gzip", "fast"), ["write package"]),
                ("fast", ("original", "gzip"), ["write image", "read image"])):
            self.build()
            self.flash(package, *history)
            self.run_migration()
            self.assertEqual(self.marker(), "installed")
            self.assert_fast_pair()
            self.assert_boot0_patched()
            self.assertEqual(self.ops(), FIRST_READS + expected + ["read image", "read package"] + PATCH_BOOT0)
            image = "fast" if "fast" in history else "gzip"
            self.assertEqual(self.backups(), [f"boot-image-{image}.bin", f"package-{package}.bin"])
            self.assertEqual((self.state / f"boot-image-{image}.bin").read_bytes(), self.images[image])

    def test_killed_run_resumes_at_next_boot(self):
        # Kill the migrator itself between the verified image and the package.
        self.inject('case "$*" in *"/boot-package.bin of="*) kill -KILL "$PPID"; exit 1 ;; esac')
        # The pair step runs in its own shell; its death is reported, not hidden.
        result = subprocess.run(["/bin/busybox", "sh", str(SCRIPT)], env=self.env,
                                capture_output=True, text=True, timeout=60)
        self.assertEqual((result.returncode, result.stdout, result.stderr), (137, "", "Killed\n"))
        self.assertFalse((self.state / "complete").exists())
        self.assertEqual(self.boot.read_bytes()[:IMAGE_SIZE["fast"]], self.images["fast"])
        self.assertEqual(self.package(), self.packages["gzip"])
        self.assert_boot0_untouched()
        (self.root / "dd.log").unlink()
        self.inject("")
        self.run_migration()
        self.assertEqual(self.marker(), "installed")
        self.assert_fast_pair()
        self.assert_boot0_patched()
        self.assertEqual(self.writes(), ["write package", "write boot0"])

    def test_fast_pair_with_loud_boot0_patches_one_sector(self):
        self.flash("fast", "fast")
        self.run_migration()
        self.assertEqual(self.marker(), "installed")
        self.assert_fast_pair()
        self.assert_boot0_patched()
        self.assertEqual(self.ops(), FIRST_READS + PATCH_BOOT0)
        self.assertEqual(self.boot.read_bytes(), self.before_boot)
        self.assertEqual(self.backups(), [])
        self.assertEqual(self.splashes(), [SPLASH])
        self.assert_work_removed()

    def test_fast_pair_with_quiet_boot0_writes_nothing(self):
        for history in (("fast",), ("original", "gzip", "fast")):
            self.build()
            self.flash("fast", *history, boot0=self.make_boot0(debug=0))
            self.run_migration()
            self.assertEqual(self.marker(), "already-installed")
            self.assert_untouched()
            self.assertEqual(self.ops(), FIRST_READS)
            self.assertEqual(self.backups(), [])
            self.assertEqual(self.splashes(), [])
            self.assert_work_removed()

    def test_quiet_boot0_is_left_alone_while_the_pair_migrates(self):
        self.flash("gzip", "original", "gzip", boot0=self.make_boot0(debug=0))
        self.run_migration()
        self.assertEqual(self.marker(), "installed")
        self.assert_fast_pair()
        self.assert_boot0_untouched()
        self.assertEqual(self.writes(), ["write image", "write package"])

    def test_external_power_allows_low_battery(self):
        (self.root / "sys/class/power_supply/battery/capacity").write_text("10\n")
        usb = self.root / "sys/class/power_supply/usb"
        usb.mkdir()
        (usb / "online").write_text("1\n")
        self.run_migration()
        self.assertEqual(self.marker(), "installed")

    def test_signals_cannot_interrupt_the_writes(self):
        self.inject('case "$*" in *" of="*"/dev/mmcblk0"*) kill -TERM "$PPID"; kill -HUP "$PPID" ;; esac')
        self.run_migration()
        self.assertEqual(self.marker(), "installed")
        self.assert_fast_pair()
        self.assert_boot0_patched()

    # ------------------------------------------- guards: nothing written

    def assert_nothing_written(self):
        self.assert_untouched()
        self.assertEqual(self.writes(), [])
        self.assertEqual(self.backups(), [])
        self.assertEqual(self.splashes(), [])
        self.assert_work_removed()

    def test_unknown_firmware(self):
        unknown = bytes(PACKAGE_SIZE)
        for package, history in (
                (unknown, ("original", "gzip")),       # unrecognised package
                ("gzip", ()),                          # unrecognised image
                ("original", ("original", "gzip")),    # halves that never boot together
                ("fast", ("original",)),
                ("gzip", ("original",))):
            self.build()
            self.flash(package, *history)
            self.run_migration()
            self.assertIn("skipped: firmware differs", self.marker())
            self.assert_nothing_written()
            self.assertEqual(self.ops(), FIRST_READS[:2])

    def test_image_is_recognised_by_its_own_bytes_only(self):
        # One changed byte inside the image is unknown firmware; any change
        # beyond it is not the bootloader's concern, nor the migrator's.
        data = bytearray(self.partition("original", "gzip"))
        data[IMAGE_SIZE["gzip"] - 1] ^= 1
        self.boot.write_bytes(bytes(data))
        self.before_boot = bytes(data)
        self.run_migration()
        self.assertIn("skipped: firmware differs", self.marker())
        self.assert_nothing_written()
        self.build()
        data = bytearray(self.partition("original", "gzip"))
        data[IMAGE_SIZE["gzip"]:] = bytes(PARTITION_SIZE - IMAGE_SIZE["gzip"])
        self.boot.write_bytes(bytes(data))
        self.before_boot = bytes(data)
        self.run_migration()
        self.assertEqual(self.marker(), "installed")
        self.assert_fast_pair()

    def test_target_and_geometry(self):
        for path, content in (("etc/baseos-release", "BASEOS_TARGET=rg28xx\n"),
                              ("sys/class/block/mmcblk0p4/start", "4097\n"),
                              ("sys/class/block/mmcblk0p4/size", "131072\n")):
            self.build()
            (self.root / path).write_text(content)
            self.run_migration()
            self.assertTrue(self.marker().startswith("skipped:"))
            self.assert_nothing_written()
            self.assertEqual(self.ops(), [])

    def test_corrupt_assets(self):
        for name in ("boot.img", "boot-package.bin"):
            self.build()
            (self.assets / name).write_bytes(b"broken")
            self.run_migration()
            self.assertIn("assets failed", self.marker())
            self.assert_nothing_written()
        # An asset that matches its hash but not the size the manifest states.
        self.build(BOOT_IMAGE_SIZE=IMAGE_SIZE["fast"] + 512)
        self.run_migration()
        self.assertIn("assets failed", self.marker())
        self.assert_nothing_written()

    def test_incomplete_manifest_defers_without_marker(self):
        for change in (dict(GZIP_PACKAGE_SHA256=None), dict(BOOT0_BODY_SHA256=None),
                       dict(ORIGINAL_BOOT_IMAGE_SIZE=None), dict(BOOT0_OFFSET=""),
                       dict(BOOT_IMAGE_SHA256="0" * 63), dict(PACKAGE_SIZE="6656; reboot"),
                       dict(GZIP_BOOT_IMAGE_SIZE=IMAGE_SIZE["gzip"] + 1),
                       dict(ORIGINAL_BOOT_IMAGE_SIZE=PARTITION_SIZE + 512),
                       dict(PACKAGE_OFFSET=PACKAGE_OFFSET + 1)):
            self.build()
            manifest = self.assets / "manifest"
            lines = dict(line.split("=", 1) for line in manifest.read_text().splitlines())
            for key, value in change.items():
                if value is None:
                    del lines[f"BOOT_{key}"]
                else:
                    # Quoted so that sourcing the file cannot run the value.
                    lines[f"BOOT_{key}"] = f"'{value}'"
            manifest.write_text("".join(f"{k}={v}\n" for k, v in lines.items()))
            self.run_migration()
            self.assertFalse((self.state / "complete").exists(), change)
            self.assertIn("manifest", self.log())
            self.assert_nothing_written()
            self.assertEqual(self.ops(), [])

    def test_low_battery_and_storage_defer(self):
        battery = self.root / "sys/class/power_supply/battery/capacity"
        battery.write_text("10\n")
        self.run_migration()
        self.assertFalse((self.state / "complete").exists())
        self.assertIn("deferred", self.log())
        self.assert_nothing_written()
        self.assertEqual(self.ops(), [])
        battery.write_text("100\n")
        (self.root / "run/usb-storage-device").write_text("/dev/mmcblk1\n")
        self.run_migration()
        self.assertFalse((self.state / "complete").exists())
        self.assert_nothing_written()
        self.assertEqual(self.ops(), [])

    def test_real_path_refuses_nonpersistent_userdata(self):
        # Disposable container has no /dev/mmcblk0p6 mounted at /data. Exercise
        # the production guard (without TEST_ROOT), before any block access.
        assets = Path("/usr/share/baseos/boot-gzip")
        self.assertFalse(assets.exists())
        assets.mkdir(parents=True)
        manifest = assets / "manifest"
        manifest.touch()
        self.addCleanup(assets.rmdir)
        self.addCleanup(manifest.unlink)
        env = dict(self.env)
        env.pop("BASEOS_BOOT_TEST_ROOT")
        result = subprocess.run(["/bin/busybox", "sh", str(SCRIPT)], env=env,
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0)
        self.assertIn("persistent writable userdata is unavailable", result.stderr)
        self.assertFalse(Path("/data/boot-pair-v2").exists())

    def test_unwritable_state_defers(self):
        (self.root / "data/boot-pair-v2").write_text("not a directory\n")
        result = subprocess.run(["/bin/busybox", "sh", str(SCRIPT)], env=self.env,
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0)
        self.assertIn("cannot record progress", result.stderr)
        self.assert_untouched()
        self.assertEqual(self.ops(), [])

    def test_unexpected_failure_before_writing_does_not_block_startup(self):
        # Only an unverified boot area may produce a non-zero status.
        helper = self.root / "bin/mktemp"
        helper.write_text("#!/bin/sh\nexit 1\n")
        helper.chmod(0o755)
        self.run_migration()
        self.assertFalse((self.state / "complete").exists())
        self.assert_nothing_written()

    def test_signal_before_writing_ends_cleanly(self):
        self.inject('case "$*" in *" of="*"/package.cur"*) kill -TERM "$PPID" ;; esac')
        self.run_migration()
        self.assertFalse((self.state / "complete").exists())
        self.assert_nothing_written()

    def test_no_backup_space(self):
        wrapper = self.root / "bin/df"
        wrapper.write_text('#!/bin/sh\necho "disk 100 99 1 99% /data"\n')
        wrapper.chmod(0o755)
        self.run_migration()
        self.assertIn("insufficient", self.marker())
        self.assert_untouched()
        self.assertEqual(self.writes(), [])
        self.assertEqual(self.backups(), [])
        self.assert_work_removed()
        # With the first migration's copy intact nothing needs saving, so a
        # nearly full userdata partition is no obstacle.
        self.build()
        wrapper.parent.mkdir(exist_ok=True)
        wrapper.write_text('#!/bin/sh\necho "disk 100 99 1 99% /data"\n')
        wrapper.chmod(0o755)
        self.save_first_migration_backup()
        self.run_migration()
        self.assertEqual(self.marker(), "installed")
        self.assert_fast_pair()

    def test_backup_space_counts_only_what_must_be_saved(self):
        need = 4096 + PACKAGE_SIZE // 1024 + IMAGE_SIZE["gzip"] // 1024
        for free, installs in ((need - 1, False), (need, True)):
            self.build()
            wrapper = self.root / "bin/df"
            wrapper.write_text(f'#!/bin/sh\necho "disk 999999 1 {free} 1% /data"\n')
            wrapper.chmod(0o755)
            self.run_migration()
            self.assertEqual(self.marker() == "installed", installs, self.marker())

    def test_bad_backup_is_never_overwritten(self):
        for name in ("package-gzip.bin", "boot-image-gzip.bin"):
            self.build()
            self.state.mkdir()
            (self.state / name).write_bytes(b"not what the name says")
            self.run_migration()
            self.assertIn("backups", self.marker())
            self.assertEqual((self.state / name).read_bytes(), b"not what the name says")
            self.assert_untouched()
            self.assertEqual(self.writes(), [])

    def test_matching_backup_is_reused(self):
        self.state.mkdir()
        (self.state / "package-gzip.bin").write_bytes(self.packages["gzip"])
        (self.state / "boot-image-gzip.bin.tmp").write_bytes(b"left by an interrupted run")
        self.run_migration()
        self.assertEqual(self.marker(), "installed")
        self.assert_fast_pair()
        self.assertEqual(self.backups(), ["boot-image-gzip.bin", "package-gzip.bin"])
        self.assertEqual((self.state / "boot-image-gzip.bin").read_bytes(), self.images["gzip"])

    # ---------------------------------------------- failures and rollback

    def test_pair_write_failures_roll_back(self):
        garble = dict(image=("boot.img", "mmcblk0p4", 7),
                      package=("boot-package.bin", "mmcblk0", PACKAGE_OFFSET + 5))
        for start in ("original", "gzip"):
            for point, (source, device, offset) in garble.items():
                for mode in ("refuse", "torn", "silent"):
                    with self.subTest(start=start, point=point, mode=mode):
                        self.build()
                        if start == "original":
                            self.flash("original", "original")
                        self.inject(self.fault(source, mode, self.root / "dev" / device, offset))
                        self.run_migration()
                        self.assertEqual(self.marker(), "rolled-back")
                        # Verified rollback: every byte of both boot areas is
                        # as it was, and boot0 was never attempted.
                        self.assert_untouched()
                        self.assertNotIn("write boot0", self.ops())
                        self.assertIn("previous pair restored and verified", self.log())
                        self.assertEqual(self.splashes(), [SPLASH])
                        self.assert_work_removed()
                        writes = self.writes()
                        if mode == "refuse" and point == "image":
                            # Nothing changed, so nothing is rewritten.
                            self.assertEqual(writes, ["write image"])
                        elif point == "image" and (start == "gzip" or mode == "torn"):
                            # The package was never touched: it is not put at
                            # risk by a restore it does not need.
                            self.assertEqual(writes, ["write image", "write image"])
                        elif point == "image":
                            self.assertEqual(writes, ["write image", "write package",
                                                      "write package", "write image"])
                        elif mode == "refuse":
                            self.assertEqual(writes, ["write image", "write package", "write image"])
                        else:
                            self.assertEqual(writes, ["write image", "write package",
                                                      "write package", "write image"])

    def test_boot0_write_failures_leave_boot0_as_found(self):
        for mode, offset in (("refuse", 0), ("torn", 100), ("silent", 100), ("silent", 12), ("silent", DEBUG_OFFSET)):
            with self.subTest(mode=mode, offset=offset):
                self.build()
                self.inject(self.fault("boot0.new", mode, self.disk, BOOT0_OFFSET + offset))
                self.run_migration()
                self.assertEqual(self.marker(), "installed; boot0 left unchanged: "
                                 "the patch could not be verified and was undone")
                # The pair is not rolled back: it was complete and verified.
                self.assert_fast_pair()
                self.assert_boot0_untouched()
                self.assert_work_removed()
                expected = ["write image", "write package", "write boot0"]
                self.assertEqual(self.writes(), expected + ([] if mode == "refuse" else ["write boot0"]))

    def test_unrecoverable_rollback_blocks_startup(self):
        for start in ("original", "gzip"):
            with self.subTest(start=start):
                self.build()
                if start == "original":
                    self.flash("original", "original")
                self.inject(self.fault("boot-package.bin", "torn", self.disk, PACKAGE_OFFSET + 5)
                            + self.fault("package.cur", "refuse"))
                self.run_migration(expected=1)
                self.assertFalse((self.state / "complete").exists())
                self.assertIn("RESTORE FAILED", self.log())
                self.assertEqual(self.splashes(), [SPLASH, FAILED_SPLASH])
                self.assert_boot0_untouched()
                # The previous contents stay reachable over ADB while power is on.
                (work,) = (self.root / "tmp").iterdir()
                self.assertEqual((work / "package.cur").read_bytes(), self.package(self.before_disk))
                self.assertEqual((work / "image.cur").read_bytes()[:IMAGE_SIZE[start]], self.images[start])
                self.assertIn(str(work), self.log())

    def test_unrecoverable_image_restore_blocks_startup(self):
        self.inject(self.fault("boot.img", "silent", self.boot, 7) + self.fault("image.cur", "refuse"))
        self.run_migration(expected=1)
        self.assertFalse((self.state / "complete").exists())
        self.assertIn("RESTORE FAILED", self.log())
        self.assertEqual(self.splashes(), [SPLASH, FAILED_SPLASH])

    def test_unrecoverable_boot0_blocks_startup(self):
        # The sector write lands but boot0 then reads back wrong, and putting
        # the saved sector back does not make it right either.
        for restore in (self.fault("boot0.cur", "refuse"),
                        self.fault("boot0.cur", "silent", "PLACEHOLDER", BOOT0_OFFSET + 4096)):
            with self.subTest(restore=restore[:40]):
                self.build()
                restore = restore.replace("PLACEHOLDER", str(self.disk))
                self.inject(self.fault("boot0.new", "silent", self.disk, BOOT0_OFFSET + 100) + restore)
                self.run_migration(expected=1)
                self.assertFalse((self.state / "complete").exists())
                self.assertIn("BOOT0 RESTORE FAILED", self.log())
                self.assertEqual(self.splashes(), [SPLASH, FAILED_SPLASH])
                (work,) = (self.root / "tmp").iterdir()
                self.assertEqual((work / "boot0.cur").read_bytes(), self.boot0(self.before_disk))

    # ----------------------------------------------------------- boot0

    def test_unrecognised_boot0_is_untouched_while_the_pair_migrates(self):
        sound = self.make_boot0()
        other_body = bytearray(self.body)
        other_body[1000] ^= 0x40
        bad_sum = bytearray(sound)
        struct.pack_into("<I", bad_sum, 12, (boot0_checksum(sound) + 1) & 0xFFFFFFFF)
        truncated = self.make_boot0(length=BOOT0_SIZE - 512)
        cases = (
            ("unexpected magic", self.make_boot0(magic=b"eGON.BT1")),
            ("unexpected length", truncated),
            ("unexpected contents", self.make_boot0(body=bytes(other_body))),
            ("checksum does not reproduce", bytes(bad_sum)),
            ("unexpected debug value 1", self.make_boot0(debug=1)),
            ("unexpected debug value 255", self.make_boot0(debug=255)),
            ("unexpected magic", bytes(BOOT0_SIZE)),
        )
        for reason, boot0 in cases:
            for package, history in (("gzip", ("original", "gzip")), ("original", ("original",))):
                with self.subTest(reason=reason, package=package):
                    self.build()
                    self.flash(package, *history, boot0=boot0)
                    self.run_migration()
                    self.assertEqual(self.marker(), f"installed; boot0 left unchanged: {reason}")
                    self.assert_fast_pair()
                    self.assert_boot0_untouched()
                    self.assertEqual(self.writes(), ["write image", "write package"])
                    self.assertIn(f"boot0 left unchanged: {reason}", self.log())
        # The same boot0 beside a pair that is already fast: nothing to write.
        self.build()
        self.flash("fast", "fast", boot0=cases[0][1])
        self.run_migration()
        self.assertEqual(self.marker(), "already-installed; boot0 left unchanged: unexpected magic")
        self.assert_nothing_written()

    def test_boot0_layout_the_patch_does_not_cover(self):
        for change in (dict(BOOT0_DEBUG_OFFSET=53), dict(BOOT0_DEBUG_OFFSET=516),
                       dict(BOOT0_BODY_OFFSET=1024, BOOT0_BODY_SHA256=sha(self.body[512:]))):
            self.build(**change)
            self.run_migration()
            self.assertTrue(self.marker().startswith("installed; boot0 left unchanged: manifest"), self.marker())
            self.assert_fast_pair()
            self.assert_boot0_untouched()

    def test_boot0_patch_arithmetic(self):
        # Stored checksums chosen so that subtracting the debug value borrows
        # across each byte and wraps below zero. The expected result is the
        # full checksum recomputed in Python, not the migrator's shortcut.
        for stored in (0, 1, 2, 3, 4, 0x100, 0x102, 0x10000, 0x1000000, 0x01000002,
                       0x7FFFFFFF, 0x80000000, 0xFFFFFFFF, 0xDEADBEEF):
            with self.subTest(stored=f"{stored:#010x}"):
                self.build()
                before = self.make_boot0(checksum=stored)
                self.assertEqual(struct.unpack_from("<I", before, 12)[0], stored)
                self.flash("fast", "fast", boot0=before)
                self.run_migration()
                self.assertEqual(self.marker(), "installed")
                self.assert_boot0_patched()
                after = self.boot0()
                self.assertEqual(struct.unpack_from("<I", after, 12)[0], (stored - 3) & 0xFFFFFFFF)
                self.assertEqual([at for at in range(BOOT0_SIZE) if after[at] != before[at]],
                                 sorted({DEBUG_OFFSET} | {at for at in range(12, 16)
                                                          if after[at] != before[at]}))
                self.assertEqual(after[DEBUG_OFFSET], 0)
                self.assertEqual(self.ops(), FIRST_READS + PATCH_BOOT0)

    def test_boot0_checksum_is_exact_at_the_largest_sums(self):
        # All-ones content gives the largest word sum a boot0 can have; the
        # migrator's awk arithmetic must still agree with Python to the bit.
        body = b"\xff" * (BOOT0_SIZE - BODY_OFFSET)
        for stored in (None, 1):
            self.build(BOOT0_BODY_SHA256=sha(body))
            self.header = b"\xff" * BODY_OFFSET
            self.flash("fast", "fast", boot0=self.make_boot0(body=body, checksum=stored))
            self.run_migration()
            self.assertEqual(self.marker(), "installed")
            self.assert_boot0_patched()


    @unittest.skipUnless((REPO / "work/rgsp/boot-prefix.img").is_file(),
                         "prepared RG SP firmware is not in this checkout")
    def test_real_rgsp_assets_from_the_build_tool(self):
        """The files and manifest the build really ships, on a card laid out like a real one.

        Every other test uses a small fixture with its own manifest. This one
        takes the RG SP assets from tools/kernel_gzip.py and a card as release
        1.3.0 left it: gzip pair, boot0 at its stock debug level with a first
        sector boot0 has since rewritten.
        """
        import sys
        sys.path.insert(0, str(REPO / "tools"))
        import kernel_gzip
        prefix = REPO / "work/rgsp/boot-prefix.img"
        derived = kernel_gzip.derive("rgsp", prefix)
        kernel_gzip.prepare("rgsp", prefix, self.root / "prepared")
        for name in ("boot.img", "boot-package.bin", "manifest"):
            shutil.copy(self.root / "prepared" / name, self.assets / name)
        profile = derived["profile"]
        at, size = profile["package_offset"], profile["package_size"]
        boot0 = bytearray(derived["original_boot0"])
        boot0[0xA8] ^= 0x5A  # memory-training bytes differ from the prepared copy on every card
        struct.pack_into("<I", boot0, 12, boot0_checksum(boot0))
        disk = bytearray(at + size + 4096)
        disk[BOOT0_OFFSET:BOOT0_OFFSET + BOOT0_SIZE] = boot0
        disk[at:at + size] = derived["gzip_package"]
        partition = derived["gzip_boot"] + derived["original_boot"][len(derived["gzip_boot"]):]
        self.disk.write_bytes(disk)
        self.boot.write_bytes(partition)
        (self.root / "sys/class/block/mmcblk0p4/start").write_text(f"{profile['boot_partition_start']}\n")
        (self.root / "sys/class/block/mmcblk0p4/size").write_text(f"{profile['boot_partition_sectors']}\n")

        self.run_migration()
        self.assertEqual(self.marker(), "installed", self.log())
        after_disk, after_boot, fast = self.disk.read_bytes(), self.boot.read_bytes(), derived["boot"]
        self.assertEqual(after_boot[:len(fast)], fast)
        self.assertEqual(after_boot[len(fast):], partition[len(fast):])
        self.assertEqual(after_disk[at:at + size], derived["package"])
        quiet = after_disk[BOOT0_OFFSET:BOOT0_OFFSET + BOOT0_SIZE]
        self.assertEqual(quiet[DEBUG_OFFSET], 0)
        self.assertEqual(struct.unpack_from("<I", quiet, 12)[0], boot0_checksum(quiet))
        changed = {i for i in range(BOOT0_SIZE) if quiet[i] != boot0[i]}
        self.assertIn(DEBUG_OFFSET, changed)
        self.assertLessEqual(changed, {12, 13, 14, 15, DEBUG_OFFSET})
        self.assertEqual(quiet[BODY_OFFSET:], derived["boot0"][BODY_OFFSET:])
        untouched = bytearray(after_disk)
        untouched[BOOT0_OFFSET:BOOT0_OFFSET + 512] = disk[BOOT0_OFFSET:BOOT0_OFFSET + 512]
        untouched[at:at + size] = disk[at:at + size]
        self.assertEqual(bytes(untouched), bytes(disk))
        # The durable recovery copy is the pair that was installed, image bytes only.
        self.assertEqual(self.backups(), ["boot-image-gzip.bin", "package-gzip.bin"])
        self.assertEqual((self.state / "boot-image-gzip.bin").read_bytes(), derived["gzip_boot"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
