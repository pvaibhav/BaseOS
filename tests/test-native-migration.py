#!/usr/bin/env python3
"""Run inside Alpine: the native step of the actual BusyBox migrator on fake cards.

The card is two files (whole disk and partition 4) under a test root, laid out
like a real card at small scale: boot0 at sector 16, its stock copy at sector
512, the U-Boot package after it, the 1.4.0 image at the start of partition 4
and the native firmware and kernel image further in. The RTC pair GP14/GP15 is
a file driven by a model of `native-health --stock-latch/--hold-stock/
--release-stock` (the C implementation has its own tests). Every dd and every
latch change goes into one log, so the order of writes against the latch is
asserted, not assumed.
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

BOOT0_OFFSET, BOOT0_SIZE, DEBUG_OFFSET, BODY_OFFSET = 8192, 65536, 52, 512
CAPSULE_SIZE, SECONDARY_SECTOR = 131072, 512
TOC_SECTOR, PACKAGE_SIZE = 700, 6656
PACKAGE_OFFSET = TOC_SECTOR * 512
DISK_SIZE = PACKAGE_OFFSET + PACKAGE_SIZE + 4096
PARTITION_SIZE = 1 << 20
FIRMWARE_OFFSET, FIRMWARE_SIZE = 512 * 1024, 10240
IMAGE_OFFSET, IMAGE_SIZE = 768 * 1024, 20480
IMAGES = dict(original=43008, gzip=26624, fast=18432)
STAMP = 0x5F0A6C39
PENDING = "42535031"
IDLE = "00000000 00000000"
GENERATION = bytes(range(1, 33))
OLD_GENERATION = bytes(range(101, 133))


def sha(data):
    return hashlib.sha256(data).hexdigest()


def fnv(data):
    value = 2166136261
    for byte in data:
        value = (value ^ byte) * 16777619 & 0xFFFFFFFF
    return f"{value:08x}"


def checksum(blob, field=12):
    total = STAMP
    for at in range(0, len(blob), 4):
        if at != field:
            total += int.from_bytes(blob[at:at + 4], "little")
    return total & 0xFFFFFFFF


HEALTH = r'''#!/bin/sh
# Model of the native-health stock latch on a file-backed RTC pair.
R=$BASEOS_BOOT_TEST_ROOT
read -r c m < "$R/rtc"
read -r wdt < "$R/wdt"
read -r C < "$R/cookie"
note() { printf 'latch %s\n' "$*" >> "$R/dd.log"; }
put() { printf '%s %s\n' "$1" "$2" > "$R/rtc"; printf '%s %s\n' "$1" "$2" >> "$R/pair.log"; c=$1 m=$2; }
case "$1" in
--stock-latch)
	state=other
	[ "$c $m" != "$C 42535031" ] || state=held
	[ "$c $m" != "00000000 00000000" ] || state=idle
	echo "$c $m $state" ;;
--hold-stock)
	[ "$wdt" = 0 ] || exit 4
	[ ! -e "$R/hold-fails" ] || exit 5
	note hold
	[ "$c $m" != "$C 42535031" ] || exit 0
	if [ "$c $m" = "00000000 00000000" ]; then put "$C" 00000000; put "$C" 42535031; exit 0; fi
	[ "$m" = 42535031 ] || put "$c" 42535031
	[ "$c" = "$C" ] || put "$C" 42535031 ;;
--release-stock)
	[ "$wdt" = 0 ] || exit 4
	[ "$c $m" != "00000000 00000000" ] || exit 0
	[ "$c $m" = "$C 42535031" ] || exit 5
	note release
	put 00000000 42535031; put 00000000 00000000 ;;
--sync-wake-dram)
	printf '%s\n' "${2:-none}" > "$R/wake-sync.log" ;;
*) exit 2 ;;
esac
'''


class NativeMigration(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.build()

    # ------------------------------------------------------------ the card

    def build(self):
        self.root = Path(self.temp.name) / "root"
        if self.root.exists():
            shutil.rmtree(self.root)
        self.assets = self.root / "usr/share/baseos/boot-gzip"
        self.native = self.root / "usr/share/baseos/native"
        self.pair_state = self.root / "data/boot-pair-v2"
        self.state = self.root / "data/boot-native-v1"
        for name in ("dev", "etc/baseos", "run", "tmp", "bin", "data", "usr/bin", "usr/sbin",
                     "sys/class/block/mmcblk0p4", "sys/class/power_supply/battery",
                     "usr/share/baseos/boot-gzip", "usr/share/baseos/native"):
            (self.root / name).mkdir(parents=True, exist_ok=True)
        (self.root / ".boot-migration-test").touch()
        (self.root / "etc/baseos-release").write_text("BASEOS_TARGET=rgsp\n")
        rng = random.Random(20261015)
        self.rng = rng
        self.packages = {name: rng.randbytes(PACKAGE_SIZE) for name in IMAGES}
        self.images = {name: rng.randbytes(size) for name, size in IMAGES.items()}
        self.partition_base = rng.randbytes(PARTITION_SIZE)
        self.boot0_header = rng.randbytes(BODY_OFFSET)
        self.boot0_body = rng.randbytes(BOOT0_SIZE - BODY_OFFSET)
        self.disk_base = rng.randbytes(DISK_SIZE)
        self.capsule = self.make_capsule(self.make_boot0(debug=3))
        self.old_capsule = self.make_capsule(self.make_boot0(debug=0, header=rng.randbytes(BODY_OFFSET)))
        self.firmware = rng.randbytes(FIRMWARE_SIZE)
        self.image = rng.randbytes(IMAGE_SIZE)
        self.toc = rng.randbytes(PACKAGE_SIZE)
        self.lab_toc = rng.randbytes(PACKAGE_SIZE)
        self.boot = self.root / "dev/mmcblk0p4"
        self.disk = self.root / "dev/mmcblk0"
        (self.assets / "boot.img").write_bytes(self.images["fast"])
        (self.assets / "boot-package.bin").write_bytes(self.packages["fast"])
        fast = self.images["fast"]
        original = self.images["original"] + self.partition_base[IMAGES["original"]:]
        fields = dict(
            TARGET="rgsp", PACKAGE_OFFSET=PACKAGE_OFFSET, PACKAGE_SIZE=PACKAGE_SIZE,
            BOOT_START=4096, BOOT_SECTORS=PARTITION_SIZE // 512, BOOT_IMAGE_SIZE=len(fast),
            PACKAGE_SHA256=sha(self.packages["fast"]), BOOT_IMAGE_SHA256=sha(fast),
            ORIGINAL_PACKAGE_SHA256=sha(self.packages["original"]), ORIGINAL_BOOT_SHA256=sha(original),
            BOOT_PARTITION_SHA256=sha(fast + original[len(fast):]), KERNEL_SHA256=sha(b"k"), KERNEL_SIZE=1,
            ORIGINAL_BOOT_IMAGE_SIZE=IMAGES["original"], ORIGINAL_BOOT_IMAGE_SHA256=sha(self.images["original"]),
            GZIP_PACKAGE_SHA256=sha(self.packages["gzip"]), GZIP_BOOT_IMAGE_SIZE=IMAGES["gzip"],
            GZIP_BOOT_IMAGE_SHA256=sha(self.images["gzip"]),
            BOOT0_OFFSET=BOOT0_OFFSET, BOOT0_SIZE=BOOT0_SIZE, BOOT0_DEBUG_OFFSET=DEBUG_OFFSET,
            BOOT0_BODY_OFFSET=BODY_OFFSET, BOOT0_BODY_SHA256=sha(self.boot0_body),
            BOOT0_ORIGINAL_SHA256=sha(self.make_boot0()))
        (self.assets / "manifest").write_text("".join(f"BOOT_{k}={v}\n" for k, v in fields.items()))
        for name, blob in (("capsule.bin", self.capsule), ("firmware.bin", self.firmware),
                           ("image.bin", self.image), ("recovery-toc.bin", self.toc)):
            (self.native / name).write_bytes(blob)
        self.write_native_manifest()
        (self.native / "generation").write_text(GENERATION.hex() + "\n")
        (self.root / "etc/baseos/native-generation").write_bytes(GENERATION)
        (self.root / "sys/class/block/mmcblk0p4/start").write_text("4096\n")
        (self.root / "sys/class/block/mmcblk0p4/size").write_text(f"{PARTITION_SIZE // 512}\n")
        (self.root / "sys/class/power_supply/battery/capacity").write_text("100\n")
        health = self.root / "usr/sbin/native-health"
        health.write_text(HEALTH)
        health.chmod(0o755)
        (self.root / "cookie").write_text(fnv(GENERATION) + "\n")
        self.set_pair(IDLE)
        (self.root / "wdt").write_text("0\n")
        splash = self.root / "usr/bin/baseos-splash"
        splash.write_text('#!/bin/sh\nprintf \'%s\\n\' "$*" >> "$BASEOS_BOOT_TEST_ROOT/splash.log"\n')
        splash.chmod(0o755)
        self.inject("")
        self.boot_log = self.root / "card-boot.log"
        self.env = dict(os.environ, BASEOS_BOOT_TESTING="1", BASEOS_BOOT_TEST_ROOT=str(self.root),
                        BASEOS_BOOT_LOG=str(self.boot_log),
                        PATH=f"{self.root}/bin:/usr/sbin:/usr/bin:/sbin:/bin")

    def write_native_manifest(self, **override):
        fields = dict(
            FORMAT=1, TARGET="rgsp", GENERATION=GENERATION.hex(), COOKIE=fnv(GENERATION),
            CAPSULE_SECTOR=BOOT0_OFFSET // 512, CAPSULE_SIZE=CAPSULE_SIZE, CAPSULE_SHA256=sha(self.capsule),
            SECONDARY_SECTOR=SECONDARY_SECTOR, STOCK_SHA256=sha(self.make_boot0()),
            FIRMWARE_OFFSET=FIRMWARE_OFFSET, FIRMWARE_SIZE=FIRMWARE_SIZE, FIRMWARE_SHA256=sha(self.firmware),
            IMAGE_OFFSET=IMAGE_OFFSET, IMAGE_SIZE=IMAGE_SIZE, IMAGE_SHA256=sha(self.image),
            TOC_SECTOR=TOC_SECTOR, TOC_SIZE=PACKAGE_SIZE, TOC_SHA256=sha(self.toc),
            PREVIOUS_TOC_SHA256=f'"{sha(self.lab_toc)}"')
        fields.update(override)
        (self.native / "manifest").write_text("".join(f"NATIVE_{k}={v}\n" for k, v in fields.items()))

    def make_boot0(self, debug=3, header=None):
        blob = bytearray((header or self.boot0_header) + self.boot0_body)
        blob[4:12] = b"eGON.BT0"
        struct.pack_into("<I", blob, 16, BOOT0_SIZE)
        blob[DEBUG_OFFSET] = debug
        struct.pack_into("<I", blob, 12, checksum(blob))
        return bytes(blob)

    def make_capsule(self, stock):
        blob = bytearray(self.rng.randbytes(CAPSULE_SIZE))
        blob[4:12] = b"eGON.BT0"
        struct.pack_into("<I", blob, 16, CAPSULE_SIZE)
        blob[0x1000:0x11000] = stock
        struct.pack_into("<I", blob, 12, checksum(blob))
        return bytes(blob)

    def flash(self, primary="stock", package="fast", image="fast", pair=IDLE, pair_done=True):
        """A card as some release left it. primary: stock | old | this."""
        disk = bytearray(self.disk_base)
        if primary == "stock":
            disk[BOOT0_OFFSET:BOOT0_OFFSET + BOOT0_SIZE] = self.make_boot0()
        else:
            blob = {"old": self.old_capsule, "this": self.capsule}[primary]
            disk[BOOT0_OFFSET:BOOT0_OFFSET + CAPSULE_SIZE] = blob
        disk[SECONDARY_SECTOR * 512:SECONDARY_SECTOR * 512 + BOOT0_SIZE] = self.make_boot0()
        blob = {"this": self.toc, "lab": self.lab_toc}.get(package) or self.packages[package]
        disk[PACKAGE_OFFSET:PACKAGE_OFFSET + PACKAGE_SIZE] = blob
        self.disk.write_bytes(bytes(disk))
        boot = bytearray(self.partition_base)
        boot[:IMAGES[image]] = self.images[image]
        self.boot.write_bytes(bytes(boot))
        self.set_pair(pair)
        if pair_done:
            self.pair_state.mkdir(parents=True, exist_ok=True)
            (self.pair_state / "complete").write_text("installed\n")
        self.before_disk, self.before_boot = self.disk.read_bytes(), self.boot.read_bytes()

    def set_pair(self, pair):
        (self.root / "rtc").write_text(pair + "\n")

    def pair(self):
        return (self.root / "rtc").read_text().strip()

    def inject(self, body):
        wrapper = self.root / "bin/dd"
        wrapper.write_text('#!/bin/sh\nprintf \'dd %s\\n\' "$*" >> "$BASEOS_BOOT_TEST_ROOT/dd.log"\n'
                           + body + '\nexec /bin/busybox dd "$@"\n')
        wrapper.chmod(0o755)

    def fault(self, source, mode, device, offset):
        """Make the write that copies SOURCE refuse, tear (damage + fail) or silently corrupt."""
        if mode == "refuse":
            return f'case "$*" in *"/{source} of="*) exit 1 ;; esac\n'
        return (f'case "$*" in *"/{source} of="*)\n /bin/busybox dd "$@" || exit 1\n'
                f" printf '\\245\\245\\245\\245' | /bin/busybox dd of={device} bs=1 seek={offset}"
                ' conv=notrunc 2>/dev/null\n'
                f' exit {1 if mode == "torn" else 0} ;;\nesac\n')

    def run_migration(self, expected=0):
        result = subprocess.run(["/bin/busybox", "sh", str(SCRIPT)], env=self.env,
                                capture_output=True, text=True, timeout=120)
        self.assertEqual(result.returncode, expected, result.stdout + result.stderr + self.log())
        self.assertEqual(result.stdout + result.stderr, "", self.log())

    def log(self):
        path = self.state / "install.log"
        return path.read_text() if path.exists() else ""

    def result(self):
        path = self.root / "run/baseos-native-result"
        return path.read_text().strip() if path.exists() else None

    def boot_log_lines(self):
        text = self.boot_log.read_text() if self.boot_log.exists() else ""
        return [line.split(" ", 1)[1] for line in text.splitlines()]

    def done(self):
        path = self.state / f"done-{GENERATION.hex()}"
        return path.read_text().strip() if path.exists() else None

    AREAS = {("disk", BOOT0_OFFSET): "capsule", ("disk", BOOT0_OFFSET + 512): "capsule body",
             ("disk", SECONDARY_SECTOR * 512): "secondary", ("disk", PACKAGE_OFFSET): "package",
             ("boot", 0): "stock image", ("boot", FIRMWARE_OFFSET): "firmware", ("boot", IMAGE_OFFSET): "image"}

    def ops(self, reads=False):
        """Native-step writes (and optionally reads) and latch changes, in order."""
        found = []
        path = self.root / "dd.log"
        for line in (path.read_text().splitlines() if path.exists() else []):
            words = line.split()
            if words[0] == "latch":
                found.append(line)
                continue
            args = dict(word.split("=", 1) for word in words[1:] if "=" in word)
            device = {str(self.disk): "disk", str(self.boot): "boot"}
            if args.get("of") in device and "seek_bytes" in args.get("oflag", ""):
                self.assertEqual((args["bs"], args["conv"], args["oflag"]), ("1M", "notrunc,fsync", "direct,seek_bytes"))
                name = self.AREAS[(device[args["of"]], int(args["seek"]))]
                if name == "capsule":
                    name = "capsule header"
                found.append("write " + name)
            elif reads and args.get("if") in device and "skip_bytes" in args.get("iflag", ""):
                found.append("read " + self.AREAS[(device[args["if"]], int(args["skip"]))])
        return found

    def writes(self):
        return [op for op in self.ops() if op.startswith("write")]

    # ------------------------------------------------------- assertions

    def assert_native(self):
        disk, boot = self.disk.read_bytes(), self.boot.read_bytes()
        self.assertEqual(disk[BOOT0_OFFSET:BOOT0_OFFSET + CAPSULE_SIZE], self.capsule)
        self.assertEqual(disk[PACKAGE_OFFSET:PACKAGE_OFFSET + PACKAGE_SIZE], self.toc)
        self.assertEqual(boot[FIRMWARE_OFFSET:FIRMWARE_OFFSET + FIRMWARE_SIZE], self.firmware)
        self.assertEqual(boot[IMAGE_OFFSET:IMAGE_OFFSET + IMAGE_SIZE], self.image)
        self.assertEqual(boot[:IMAGES["fast"]], self.images["fast"])
        # Nothing else on the card changes, the stock copy at sector 512 included.
        expected = bytearray(self.before_disk)
        expected[BOOT0_OFFSET:BOOT0_OFFSET + CAPSULE_SIZE] = self.capsule
        expected[PACKAGE_OFFSET:PACKAGE_OFFSET + PACKAGE_SIZE] = self.toc
        self.assertEqual(disk, bytes(expected))
        expected = bytearray(self.before_boot)
        expected[:IMAGES["fast"]] = self.images["fast"]
        expected[FIRMWARE_OFFSET:FIRMWARE_OFFSET + FIRMWARE_SIZE] = self.firmware
        expected[IMAGE_OFFSET:IMAGE_OFFSET + IMAGE_SIZE] = self.image
        self.assertEqual(boot, bytes(expected))

    def assert_installed_in_order(self, extra_before=()):
        ops = self.ops()
        latch = [op for op in ops if op.startswith("latch")]
        self.assertEqual(latch, ["latch hold", "latch release"], ops)
        writes = self.writes()
        expected = ["write firmware", "write image", "write package", "write capsule body", "write capsule header"]
        self.assertEqual(writes, list(extra_before) + expected)
        # Every boot-area write happens inside the held latch, header last.
        self.assertEqual(ops, list(extra_before) + ["latch hold"] + expected + ["latch release"])
        self.assertEqual(self.pair(), IDLE)
        # Between hold and release the pair was never zero.
        history = (self.root / "pair.log").read_text().split("\n")[:-1]
        self.assertEqual(history[-1], IDLE)
        self.assertNotIn(IDLE, history[:-1])
        self.assertEqual(self.done(), "installed")
        self.assertFalse((self.state / "latch").exists())
        self.assertEqual(os.listdir(self.root / "tmp"), [])

    # ------------------------------------------------- starting points

    def test_from_140_fast_pair(self):
        self.flash()
        self.run_migration()
        self.assert_native()
        self.assert_installed_in_order()
        # First copies of every replaced area stay on userdata.
        self.assertEqual((self.state / "primary-before.bin").read_bytes()[:BOOT0_SIZE], self.make_boot0())
        self.assertEqual((self.state / "toc-before.bin").read_bytes(), self.packages["fast"])
        self.assertEqual((self.state / "firmware-before.bin").read_bytes(),
                         self.before_boot[FIRMWARE_OFFSET:FIRMWARE_OFFSET + FIRMWARE_SIZE])
        self.assertEqual((self.state / "secondary-before.bin").read_bytes(), self.make_boot0())
        self.assertIn("SPEEDING UP BOOT", (self.root / "splash.log").read_text())
        # One plain line in the boot log, the same outcome for this boot's loader
        # line, and no copy counters in the install log.
        self.assertEqual(self.boot_log_lines(),
                         ["Fast loader install: installed and checked; it is used from the next start"])
        self.assertTrue(self.result().startswith("installed "), self.result())
        for noise in ("records in", "records out", " copied, "):
            self.assertNotIn(noise, self.log())

    def test_from_13x_original_files(self):
        """The pair step installs the 1.4.0 files and keeps the originals, then the native step runs."""
        self.flash(package="original", image="original", pair_done=False)
        self.run_migration()
        self.assertEqual((self.pair_state / "complete").read_text().strip(), "installed")
        self.assertEqual((self.pair_state / "package-original.bin").read_bytes(), self.packages["original"])
        self.assertEqual((self.pair_state / "boot-image-original.bin").read_bytes(), self.images["original"])
        disk, boot = self.disk.read_bytes(), self.boot.read_bytes()
        self.assertEqual(disk[BOOT0_OFFSET:BOOT0_OFFSET + CAPSULE_SIZE], self.capsule)
        self.assertEqual(disk[PACKAGE_OFFSET:PACKAGE_OFFSET + PACKAGE_SIZE], self.toc)
        self.assertEqual(boot[:IMAGES["fast"]], self.images["fast"])
        self.assertEqual(boot[FIRMWARE_OFFSET:FIRMWARE_OFFSET + FIRMWARE_SIZE], self.firmware)
        self.assertEqual(boot[IMAGE_OFFSET:IMAGE_OFFSET + IMAGE_SIZE], self.image)
        self.assertEqual(self.pair(), IDLE)
        self.assertEqual(self.done(), "installed")
        # The native step's own first copy is the 1.4.0 package the pair step wrote.
        self.assertEqual((self.state / "toc-before.bin").read_bytes(), self.packages["fast"])

    def test_from_140_gzip_pair_without_pair_marker(self):
        self.flash(package="gzip", image="gzip", pair_done=False)
        self.run_migration()
        self.assertEqual(self.done(), "installed")
        self.assertEqual(self.pair(), IDLE)
        self.assertEqual(self.disk.read_bytes()[PACKAGE_OFFSET:PACKAGE_OFFSET + PACKAGE_SIZE], self.toc)

    def test_from_150_per_card_native_after_watchdog_fallback(self):
        """1.5.0 card: its own capsule and lab package, pair latched by its failed native boot."""
        for package in ("lab", "this"):
            with self.subTest(package=package):
                self.build()
                self.flash(primary="old", package=package, pair=f"{fnv(OLD_GENERATION)} {PENDING}")
                (self.root / "pair.log").unlink(missing_ok=True)
                self.run_migration()
                self.assert_native()
                self.assertEqual(self.pair(), IDLE)
                self.assertEqual(self.done(), "installed")
                history = (self.root / "pair.log").read_text().split("\n")[:-1]
                self.assertNotIn(IDLE, history[:-1])
                self.assertEqual((self.state / "primary-before.bin").read_bytes(), self.old_capsule)
                expected = ["write firmware", "write image"] + (["write package"] if package == "lab" else []) \
                    + ["write capsule body", "write capsule header"]
                self.assertEqual(self.writes(), expected)

    def test_fresh_151_image_native_boot_is_a_no_op(self):
        self.flash(primary="this", package="this", pair_done=False)
        boot = bytearray(self.boot.read_bytes())
        boot[FIRMWARE_OFFSET:FIRMWARE_OFFSET + FIRMWARE_SIZE] = self.firmware
        boot[IMAGE_OFFSET:IMAGE_OFFSET + IMAGE_SIZE] = self.image
        self.boot.write_bytes(bytes(boot))
        self.before_boot = bytes(boot)
        running = self.root / "sys/firmware/devicetree/base/chosen/baseos,native-boot"
        running.parent.mkdir(parents=True)
        running.write_bytes(GENERATION)
        self.run_migration()
        self.assertEqual(self.writes(), [])
        self.assertEqual((self.pair_state / "complete").read_text().strip(), "native boot files present")
        self.assertEqual(self.done(), "installed; running")
        self.assertEqual(self.disk.read_bytes(), self.before_disk)
        self.assertEqual(self.boot.read_bytes(), self.before_boot)
        # Second boot: the marker alone ends it, with no reads at all.
        (self.root / "dd.log").unlink()
        self.run_migration()
        self.assertFalse((self.root / "dd.log").exists())

    def test_idempotent(self):
        self.flash()
        self.run_migration()
        (self.root / "dd.log").unlink()
        self.run_migration()
        self.assertFalse((self.root / "dd.log").exists())
        # Even with the marker gone, an installed card needs no write.
        (self.state / f"done-{GENERATION.hex()}").unlink()
        self.run_migration()
        self.assertEqual(self.writes(), [])
        self.assertEqual(self.done(), "installed")

    # ------------------------------------------------- failures

    def test_interrupted_before_header_keeps_latch_then_finishes(self):
        self.flash()
        self.inject(self.fault("capsule-header.bin", "refuse", None, 0))
        self.run_migration()
        self.assertEqual(self.pair(), f"{fnv(GENERATION)} {PENDING}")
        self.assertIsNone(self.done())
        self.assertTrue((self.state / "latch").exists())
        disk = self.disk.read_bytes()
        # New body behind the old stock header: its checksum no longer covers
        # the sectors, so BootROM uses the stock copy; and the pair says stock.
        self.assertEqual(disk[BOOT0_OFFSET:BOOT0_OFFSET + 512], self.make_boot0()[:512])
        self.assertEqual(disk[BOOT0_OFFSET + 512:BOOT0_OFFSET + CAPSULE_SIZE], self.capsule[512:])
        self.assertIn("FAILED at capsule header; keeping the stock latch", self.log())
        # Next boot (stock, latch held by us): finish and clear exactly our pair.
        self.inject("")
        (self.root / "dd.log").unlink()
        self.run_migration()
        self.assert_native()
        self.assertEqual(self.pair(), IDLE)
        self.assertEqual(self.done(), "installed")
        self.assertEqual(self.writes(), ["write capsule body", "write capsule header"])

    def test_torn_body_is_rewritten_on_the_next_boot(self):
        self.flash()
        self.inject(self.fault("capsule-body.bin", "torn", self.disk, BOOT0_OFFSET + 70000))
        self.run_migration()
        self.assertIn("FAILED at capsule body", self.log())
        self.assertEqual(self.pair(), f"{fnv(GENERATION)} {PENDING}")
        self.assertEqual(self.disk.read_bytes()[BOOT0_OFFSET:BOOT0_OFFSET + 512], self.make_boot0()[:512])
        self.inject("")
        self.run_migration()
        self.assert_native()
        self.assertEqual(self.pair(), IDLE)
        self.assertEqual(self.done(), "installed")

    def test_readback_mismatch_keeps_latch_and_gives_up_after_three(self):
        self.flash()
        self.inject(self.fault("firmware.bin", "silent", self.boot, FIRMWARE_OFFSET + 100))
        for attempt in (1, 2, 3):
            self.run_migration()
            self.assertEqual(self.pair(), f"{fnv(GENERATION)} {PENDING}")
            self.assertIsNone(self.done())
            self.assertIn("FAILED at firmware", self.log())
            self.assertEqual(self.boot_log_lines()[-1],
                             f"Fast loader install: failed at firmware (try {attempt} of 3; the stock loader"
                             " stays in use and the next start tries again)")
            # Nothing after the failed area was written.
            self.assertEqual(self.disk.read_bytes(), self.before_disk)
        self.run_migration()
        self.assertEqual(self.done(), "gave up after 3 failed attempts; the device stays on stock startup")
        self.assertEqual(self.result().split()[0], "gave-up")
        self.assertEqual(len(self.boot_log_lines()), 4)
        self.assertEqual(self.pair(), f"{fnv(GENERATION)} {PENDING}")

    def test_unwritable_package_is_restored(self):
        self.flash()
        self.inject(self.fault("recovery-toc.bin", "torn", self.disk, PACKAGE_OFFSET + 10))
        self.run_migration()
        self.assertEqual(self.disk.read_bytes()[PACKAGE_OFFSET:PACKAGE_OFFSET + PACKAGE_SIZE], self.packages["fast"])
        self.assertEqual(self.disk.read_bytes()[BOOT0_OFFSET:BOOT0_OFFSET + BOOT0_SIZE], self.make_boot0())
        self.assertEqual(self.pair(), f"{fnv(GENERATION)} {PENDING}")
        self.assertIn("FAILED at U-Boot package", self.log())

    def test_package_that_cannot_be_restored_stops_startup(self):
        self.flash()
        self.inject(self.fault("recovery-toc.bin", "torn", self.disk, PACKAGE_OFFSET + 10)
                    + self.fault("toc.cur", "torn", self.disk, PACKAGE_OFFSET + 10))
        self.run_migration(expected=1)
        self.assertIn("BOOT UPDATE FAILED: KEEP POWER ON", (self.root / "splash.log").read_text())
        self.assertEqual(self.pair(), f"{fnv(GENERATION)} {PENDING}")
        self.assertTrue(list((self.root / "tmp").iterdir()))

    def test_usb_storage_blocks_everything(self):
        self.flash()
        (self.root / "run/usb-storage-device").write_text("/dev/mmcblk0p7\n")
        self.run_migration()
        self.assertEqual(self.ops(reads=True), [])
        self.assertEqual(self.pair(), IDLE)
        self.assertIsNone(self.done())

    def test_usb_storage_starting_mid_install_keeps_latch(self):
        self.flash()
        self.inject('case "$*" in *"/recovery-toc.bin of="*) echo /dev/mmcblk0p7 > '
                    '"$BASEOS_BOOT_TEST_ROOT/run/usb-storage-device" ;; esac\n')
        self.run_migration()
        self.assertIn("FAILED at USB storage started", self.log())
        self.assertEqual(self.disk.read_bytes()[BOOT0_OFFSET:BOOT0_OFFSET + BOOT0_SIZE], self.make_boot0())
        self.assertEqual(self.pair(), f"{fnv(GENERATION)} {PENDING}")

    def test_armed_watchdog_writes_nothing(self):
        self.flash()
        (self.root / "wdt").write_text("1\n")
        self.run_migration()
        self.assertEqual(self.writes(), [])
        self.assertIn("could not hold the stock latch; nothing was written", self.log())
        self.assertEqual(self.pair(), IDLE)

    def test_failed_native_boot_is_not_retried(self):
        self.flash(primary="this", package="this", pair=f"{fnv(GENERATION)} {PENDING}")
        boot = bytearray(self.boot.read_bytes())
        boot[FIRMWARE_OFFSET:FIRMWARE_OFFSET + FIRMWARE_SIZE] = self.firmware
        boot[IMAGE_OFFSET:IMAGE_OFFSET + IMAGE_SIZE] = self.image
        self.boot.write_bytes(bytes(boot))
        self.run_migration()
        self.assertEqual(self.ops(), [])
        self.assertEqual(self.pair(), f"{fnv(GENERATION)} {PENDING}")
        self.assertEqual(self.done(), "native boot failed health; the device stays on stock startup")
        self.assertEqual(self.result().split()[0], "start-failed")
        self.assertIn("fast loader's start failed", self.boot_log_lines()[0])

    def test_card_moved_into_a_unit_with_a_foreign_latch(self):
        self.flash(primary="this", package="this", pair=f"11111111 {PENDING}")
        boot = bytearray(self.boot.read_bytes())
        boot[FIRMWARE_OFFSET:FIRMWARE_OFFSET + FIRMWARE_SIZE] = self.firmware
        boot[IMAGE_OFFSET:IMAGE_OFFSET + IMAGE_SIZE] = self.image
        self.boot.write_bytes(bytes(boot))
        self.run_migration()
        self.assertEqual(self.writes(), [])
        self.assertEqual(self.pair(), IDLE)
        self.assertEqual(self.done(), "installed")

    def test_unknown_firmware_is_left_alone(self):
        cases = {"package": dict(package="original"), "image": dict(image="gzip")}
        for name, flash in cases.items():
            with self.subTest(name=name):
                self.build()
                self.flash(**flash)
                self.run_migration()
                self.assertEqual(self.writes(), [])
                self.assertTrue(self.done().startswith("skipped: "))
                self.assertEqual(self.disk.read_bytes(), self.before_disk)
        self.build()
        self.flash()
        disk = bytearray(self.disk.read_bytes())
        disk[SECONDARY_SECTOR * 512 + 600] ^= 1
        self.disk.write_bytes(bytes(disk))
        self.run_migration()
        self.assertEqual(self.done(), "skipped: the secondary sector holds no stock boot0 to fall back on")

    def test_unfinished_pair_step_defers(self):
        # The boot pair step (cards from before 1.4.0) still waits for power;
        # the fast loader install then waits for it.
        self.flash()
        (self.pair_state / "complete").unlink()
        (self.root / "sys/class/power_supply/battery/capacity").write_text("20\n")
        self.run_migration()
        self.assertIsNone(self.done())
        self.assertEqual(self.writes(), [])
        self.assertIn("the boot pair step has not finished", self.log())
        # A wait is not an attempt: it is named only in this boot's loader line.
        self.assertEqual(self.result(), "deferred the boot pair step has not finished")
        self.assertEqual(self.boot_log_lines(), [])

    def test_low_battery_does_not_hold_the_install_back(self):
        self.flash()
        battery = self.root / "sys/class/power_supply/battery"
        (battery / "capacity").write_text("3\n")
        (battery / "online").write_text("0\n")
        self.run_migration()
        self.assert_native()
        self.assert_installed_in_order()

    def test_damaged_native_files_are_never_written(self):
        self.flash()
        (self.native / "image.bin").write_bytes(self.image[:-1] + b"\0")
        self.run_migration()
        self.assertEqual(self.done(), "skipped: native files failed verification")
        self.assertEqual(self.boot_log_lines(),
                         ["Fast loader install: not possible on this card: native files failed verification"])
        self.assertEqual(self.writes(), [])

    def test_stamped_wake_capsule_recognised_as_this_generation(self):
        self.flash(primary="this", package="this")
        disk = bytearray(self.disk.read_bytes())
        cap = bytearray(self.capsule)
        cap[56:184] = bytes(range(128))
        struct.pack_into("<I", cap, 12, checksum(cap))
        disk[BOOT0_OFFSET:BOOT0_OFFSET + CAPSULE_SIZE] = cap
        self.disk.write_bytes(bytes(disk))
        boot = bytearray(self.boot.read_bytes())
        boot[FIRMWARE_OFFSET:FIRMWARE_OFFSET + FIRMWARE_SIZE] = self.firmware
        boot[IMAGE_OFFSET:IMAGE_OFFSET + IMAGE_SIZE] = self.image
        self.boot.write_bytes(bytes(boot))
        chosen = self.root / "sys/firmware/devicetree/base/chosen"
        chosen.mkdir(parents=True, exist_ok=True)
        (chosen / "baseos,native-boot").write_bytes(GENERATION)
        self.run_migration()
        self.assertEqual(self.writes(), [])
        self.assertEqual(self.done(), "installed; running")

    def test_corrupted_stamped_capsule_not_recognised_as_this(self):
        self.flash(primary="this", package="this")
        disk = bytearray(self.disk.read_bytes())
        cap = bytearray(self.capsule)
        cap[56:184] = bytes(range(128))
        cap[200] ^= 1
        struct.pack_into("<I", cap, 12, checksum(cap))
        disk[BOOT0_OFFSET:BOOT0_OFFSET + CAPSULE_SIZE] = cap
        self.disk.write_bytes(bytes(disk))
        boot = bytearray(self.boot.read_bytes())
        boot[FIRMWARE_OFFSET:FIRMWARE_OFFSET + FIRMWARE_SIZE] = self.firmware
        boot[IMAGE_OFFSET:IMAGE_OFFSET + IMAGE_SIZE] = self.image
        self.boot.write_bytes(bytes(boot))
        self.run_migration()
        self.assertIn("write capsule header", self.writes())
        self.assertEqual(self.done(), "installed")

    def test_install_runs_wake_dram_sync_after_release(self):
        self.flash(primary="stock")
        self.run_migration()
        self.assert_installed_in_order()
        self.assertTrue((self.root / "wake-sync.log").read_text().strip().endswith("/primary-stock.cur"))
        self.build()
        self.flash(primary="old")
        self.run_migration()
        self.assert_installed_in_order()
        self.assertTrue((self.root / "wake-sync.log").read_text().strip().endswith("/secondary.cur"))


if __name__ == "__main__":
    unittest.main(verbosity=1)
