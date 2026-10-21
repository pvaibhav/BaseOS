#!/usr/bin/env python3
"""Run inside Alpine: the real BusyBox baseos-diag on fixture devices.

Each case builds a test root shaped like a running device: release and native
manifests, a small card (vendor boot0 or the capsule at sector 16, the vendor
copy at sector 512, the U-Boot package, partition 4 with the 1.4.0 image and
the fast loader's files), the loader's device tree records under /chosen, a
register dump standing in for baseos-regs, the install step's files on /data,
and the card's baseos-boot.log with earlier boots in it. Decoded values are
checked against values computed here.

With BASEOS_DIAG_SAMPLES=DIR set, the logs of the sample cases are copied there.
"""
import hashlib
import os
from pathlib import Path
import random
import signal
import shutil
import struct
import subprocess
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "overlay/usr/sbin/baseos-diag"

CAPSULE_SECTOR, CAPSULE_SIZE, SECONDARY_SECTOR = 16, 131072, 512
BOOT0_SIZE = 65536
TOC_SECTOR, TOC_SIZE = 700, 6656
DISK_SIZE = TOC_SECTOR * 512 + TOC_SIZE + 4096
PARTITION_SIZE = 1 << 20
FIRMWARE_OFFSET, FIRMWARE_SIZE = 512 * 1024, 10240
IMAGE_OFFSET, IMAGE_SIZE = 768 * 1024, 20480
STOCK_IMAGE_SIZE = 18432
GENERATION = bytes(range(1, 33))
STAMP = 0x5F0A6C39
TARGET = "rg40xxh"
HEADER = "=== BaseOS boot 2026-10-21 09:00:00, BaseOS 1.5.2 for rg40xxh, boot {} ==="
# The DRAM words 0x38..0xc4 of a user's RG40XX H trained boot0 (LPDDR4, 1 GiB).
TRAINED = [0x2a0, 8, 0x08080808, 0x0e0e0e0e, 0xe0e, 0x7887bbbb, 0x30fa, 0x04000000, 0, 0x34, 0x1b, 0x33, 3,
           0, 0, 4, 0x72, 0, 9, 0, 0, 0x24, 0, 0, 1, 0, 0x40808080, 0x402f6633, 0x1c20211c, 0x05050506,
           0x02008c60, 0, 0, 0x01020008, 0xffff, 0x01020108]


def sha(data):
    return hashlib.sha256(data).hexdigest()


def fnv(data):
    value = 2166136261
    for byte in data:
        value = (value ^ byte) * 16777619 & 0xFFFFFFFF
    return value


COOKIE = f"{fnv(GENERATION):08x}"


def checksum(blob):
    total = STAMP
    for at in range(0, len(blob), 4):
        if at != 12:
            total += int.from_bytes(blob[at:at + 4], "little")
    return total & 0xFFFFFFFF


def display_record(panel=1, shown=36_000_000, generation=GENERATION, damage=False):
    record = bytearray(80)
    struct.pack_into("<IIIIIIII", record, 0, 0x31505344, 1, 80, 0, panel, 6000, 0x1234, 0x5678)
    record[32:64] = generation
    struct.pack_into("<Q", record, 64, shown)
    hashed = bytes(b if not 12 <= i < 16 else 0 for i, b in enumerate(record))
    struct.pack_into("<I", record, 12, fnv(hashed) ^ (1 if damage else 0))
    return bytes(record)


REGS = '#!/bin/sh\ncat "$BASEOS_DIAG_ROOT/regs"\n'
HEALTH = r'''#!/bin/sh
[ "$1" = --check ] || exit 2
echo 'native-health: absent, malformed or mismatched generation' >&2
exit 3
'''
INSTALL_LOG = """0+1 records in
0+1 records out
131072 bytes (128.0KB) copied, 0.007601 seconds, 16.4MB/s
native: found primary stock, package earlier, firmware other, image other, pair 00000000 00000000 idle, battery 34%, charger no
native-health: stock latch held
native: stock latch held (COOKIE 42535031)
native: writing firmware
0+1 records in
0+1 records out
287744 bytes (281.0KB) copied, 0.030291 seconds, 9.1MB/s
native: writing kernel image
native: writing U-Boot package
native: writing capsule body
native: writing capsule header
native-health: stock latch released
native: stock latch cleared; the native loader starts at the next boot
native: installed
""".replace("COOKIE", COOKIE)


class Diagnostics(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "root"
        self.rng = random.Random(20261021)
        self.build()

    # ------------------------------------------------------------ fixture

    def build(self):
        root = self.root
        for name in ("bin", "dev", "etc/baseos", "run", "tmp", "data", "mnt/sdcard", "usr/sbin",
                     "proc/sys/kernel/random", "usr/share/baseos/native", "usr/share/baseos/boot-gzip",
                     "sys/firmware/devicetree/base/chosen", "sys/firmware/devicetree/base/dram",
                     "sys/firmware/devicetree/base/memory@40000000",
                     "sys/class/power_supply/axp2202-battery", "sys/class/power_supply/axp2202-usb",
                     "sys/class/graphics/fb0"):
            (root / name).mkdir(parents=True, exist_ok=True)
        rng = self.rng
        (root / "etc/baseos-release").write_text(
            f'BASEOS_VERSION=1.5.2\nBASEOS_BUILD=v1.5.1-9-gtest\nBASEOS_TARGET={TARGET}\nBASEOS_MODEL="Anbernic RG40XX H"\n')
        self.boot0_body = rng.randbytes(BOOT0_SIZE - 512)
        factory = list(TRAINED)
        factory[1], factory[6], factory[7], factory[28], factory[29] = 7, 0x30eb, 0x04001000, 0, 0
        self.stock = self.make_boot0(rng.randbytes(56) + struct.pack("<36I", *factory) + rng.randbytes(512 - 56 - 144))
        self.trained = self.make_boot0(rng.randbytes(56) + struct.pack("<36I", *TRAINED) + rng.randbytes(512 - 56 - 144))
        self.capsule = self.make_capsule(self.stock)
        self.firmware, self.image = rng.randbytes(FIRMWARE_SIZE), rng.randbytes(IMAGE_SIZE)
        self.toc, self.package = rng.randbytes(TOC_SIZE), rng.randbytes(TOC_SIZE)
        self.stock_image = rng.randbytes(STOCK_IMAGE_SIZE)
        native = dict(
            FORMAT=1, TARGET=TARGET, GENERATION=GENERATION.hex(), COOKIE=COOKIE,
            CAPSULE_SECTOR=CAPSULE_SECTOR, CAPSULE_SIZE=CAPSULE_SIZE, CAPSULE_SHA256=sha(self.capsule),
            SECONDARY_SECTOR=SECONDARY_SECTOR, STOCK_SHA256=sha(self.stock),
            FIRMWARE_OFFSET=FIRMWARE_OFFSET, FIRMWARE_SIZE=FIRMWARE_SIZE, FIRMWARE_SHA256=sha(self.firmware),
            IMAGE_OFFSET=IMAGE_OFFSET, IMAGE_SIZE=IMAGE_SIZE, IMAGE_SHA256=sha(self.image),
            TOC_SECTOR=TOC_SECTOR, TOC_SIZE=TOC_SIZE, TOC_SHA256=sha(self.toc), PREVIOUS_TOC_SHA256='""')
        (root / "usr/share/baseos/native/manifest").write_text("".join(f"NATIVE_{k}={v}\n" for k, v in native.items()))
        (root / "etc/baseos/native-generation").write_bytes(GENERATION)
        boot = dict(TARGET=TARGET, PACKAGE_OFFSET=TOC_SECTOR * 512, PACKAGE_SIZE=TOC_SIZE,
                    BOOT_START=4096, BOOT_SECTORS=PARTITION_SIZE // 512, BOOT_IMAGE_SIZE=STOCK_IMAGE_SIZE,
                    PACKAGE_SHA256=sha(self.package), BOOT_IMAGE_SHA256=sha(self.stock_image),
                    BOOT0_BODY_SHA256=sha(self.boot0_body))
        (root / "usr/share/baseos/boot-gzip/manifest").write_text("".join(f"BOOT_{k}={v}\n" for k, v in boot.items()))
        dt = root / "sys/firmware/devicetree/base"
        for name, value in (("type", 8), ("clk", 672), ("mr0", 0x30fa), ("mr1", 0x04000000), ("odt_en", 0x0e0e0e0e),
                            ("para1", 0xe0e), ("para2", 0x7887bbbb)):
            (dt / f"dram/dram_{name}").write_bytes(struct.pack(">I", value))
        (dt / "memory@40000000/reg").write_bytes(struct.pack(">QQ", 0x40000000, 1 << 30))
        (root / "proc/cmdline").write_text(
            "console=ttyS0 root=/dev/mmcblk0p5 snum=5c001040f4438852493 wifi_mac=aa:bb:cc:dd:ee:ff "
            "androidboot.serialno=5c001040f4438852493 bootreason=unknow lcd_type=boe\n")
        (root / "proc/uptime").write_text("1.52 3.90\n")
        (root / "proc/meminfo").write_text("MemTotal:        1000000 kB\n")
        (root / "proc/sys/kernel/random/boot_id").write_text("70a21e9c-36bf-461c-84ba-35da198dd2d7\n")
        battery = root / "sys/class/power_supply/axp2202-battery"
        for name, value in (("status", "Discharging"), ("capacity", "62"), ("online", "0")):
            (battery / name).write_text(value + "\n")
        (root / "sys/class/power_supply/axp2202-usb/online").write_text("0\n")
        (root / "sys/class/graphics/fb0/virtual_size").write_text("640,960\n")
        (root / "sys/class/graphics/fb0/bits_per_pixel").write_text("32\n")
        self.tool("usr/sbin/baseos-regs", REGS)
        self.tool("usr/sbin/native-health", HEALTH)
        self.tool("bin/mountpoint", '#!/bin/sh\n[ ! -e "$BASEOS_DIAG_ROOT/no-card" ]\n')
        self.tool("bin/dmesg", '#!/bin/sh\ncat "$BASEOS_DIAG_ROOT/dmesg"\n')
        (root / "dmesg").write_text(
            "[    0.000000] Kernel command line: root=/dev/mmcblk0p5 snum=5c001040f4438852493 disp_reserve=1 "
            "androidboot.serialno=5c001040f4438852493 wifi_mac=aa:bb:cc:dd:ee:ff\n"
            "[    0.000000] Memory: 1003284K/1048576K available\n"
            "[    0.500000] disp: serial=5c001040f4438852493 panel probe failed\n"
            "[    1.200000] sunxi-mmc sdc0: sdc set ios:clk 50000000Hz bm PIO pm UP vdd 21 width 4 timing SD-HS(SDR25)\n"
            "[    1.300000] axp2202-battery: probe ok\n"
            "[    1.400000] wlan0: address 12:34:56:78:9a:bc registered\n"
            "[    1.500000] unrelated line\n")
        self.set_regs()
        self.flash()
        self.env = dict(os.environ, BASEOS_DIAG_TESTING="1", BASEOS_DIAG_ROOT=str(root), BASEOS_DIAG_DELAY="0",
                        PATH=f"{root}/bin:/usr/sbin:/usr/bin:/sbin:/bin")
        self.log = root / "mnt/sdcard/baseos-boot.log"

    def tool(self, name, body):
        path = self.root / name
        path.write_text(body)
        path.chmod(0o755)

    def make_boot0(self, header):
        blob = bytearray(header + self.boot0_body)
        blob[4:12] = b"eGON.BT0"
        struct.pack_into("<I", blob, 16, BOOT0_SIZE)
        struct.pack_into("<I", blob, 12, checksum(blob))
        return bytes(blob)

    def make_capsule(self, stock):
        blob = bytearray(self.rng.randbytes(CAPSULE_SIZE))
        blob[4:12] = b"eGON.BT0"
        struct.pack_into("<I", blob, 16, CAPSULE_SIZE)
        blob[0x1000:0x11000] = stock
        struct.pack_into("<I", blob, 12, checksum(blob))
        return bytes(blob)

    def flash(self, primary="fast", toc="fast", native_files=True, secondary=None):
        disk = bytearray(self.rng.randbytes(DISK_SIZE))
        at = CAPSULE_SECTOR * 512
        if primary == "stock":
            disk[at:at + BOOT0_SIZE] = self.trained
        elif primary == "fast":
            disk[at:at + CAPSULE_SIZE] = self.capsule
        disk[SECONDARY_SECTOR * 512:SECONDARY_SECTOR * 512 + BOOT0_SIZE] = secondary or self.stock
        disk[TOC_SECTOR * 512:TOC_SECTOR * 512 + TOC_SIZE] = self.toc if toc == "fast" else self.package
        (self.root / "dev/mmcblk0").write_bytes(bytes(disk))
        boot = bytearray(self.rng.randbytes(PARTITION_SIZE))
        boot[:STOCK_IMAGE_SIZE] = self.stock_image
        if native_files:
            boot[FIRMWARE_OFFSET:FIRMWARE_OFFSET + FIRMWARE_SIZE] = self.firmware
            boot[IMAGE_OFFSET:IMAGE_OFFSET + IMAGE_SIZE] = self.image
        (self.root / "dev/mmcblk0p4").write_bytes(bytes(boot))

    def set_regs(self, gp14="00000000", gp15="00000000", retention="00000000", unavailable=False):
        values = {f"rtc_gp{i}": "00000000" for i in range(14)}
        values.update(rtc_gp14=gp14, rtc_gp15=gp15, rtc_1f8=retention, rtc_1fc="00000000", wdt_ctrl="00000000",
                      wdt_cfg="00000001", wdt_mode="00000000", sid0="00005c00", pe_cfg0="77777777",
                      pe_dat="00000040", pe_pull0="00000000", pi_cfg1="77777777", pi_dat="00000000",
                      de_top0="00000001", de_top4="00000001", de_top8="00000001", de_top100="00000001",
                      de_top108="01df027f", tcon_line="00000000")
        (self.root / "regs").write_text("".join(
            f"{name} 0x00000000 {'unavailable' if unavailable else '0x' + value}\n" for name, value in values.items()))

    def latched(self):
        self.set_regs(gp14=COOKIE, gp15="42535031")

    def install_state(self, done=None, log=INSTALL_LOG, attempts=1, latch=False):
        state = self.root / "data/boot-native-v1"
        state.mkdir(parents=True, exist_ok=True)
        (state / "install.log").write_text(log)
        if done is not None:
            (state / f"done-{GENERATION.hex()}").write_text(done + "\n")
        if attempts is not None:
            (state / f"attempts-{GENERATION.hex()}").write_text(f"{attempts}\n")
        if latch:
            (state / "latch").write_text(GENERATION.hex() + "\n")
        (state / "primary-before.bin").write_bytes(self.trained + bytes(CAPSULE_SIZE - BOOT0_SIZE))
        pair = self.root / "data/boot-pair-v2"
        pair.mkdir(parents=True, exist_ok=True)
        (pair / "complete").write_text("native boot files present\n")

    def native_boot(self, display=None, timing=True):
        chosen = self.root / "sys/firmware/devicetree/base/chosen"
        (chosen / "baseos,native-boot").write_bytes(GENERATION)
        if display is not None:
            (chosen / "baseos,boot-display").write_bytes(display)
        if timing:
            ticks = [2_400_000, 4_800_000, 12_000_000, 14_400_000, 24_000_000, 26_400_000, 28_800_000]
            (chosen / "baseos,native-timing").write_bytes(struct.pack(">7Q", *ticks))

    def result(self, text):
        (self.root / "run/baseos-native-result").write_text(text + "\n")

    def boot_log(self, *earlier, current=("1.36 " + HEADER.format("70a21e9c"),
                                          "1.41 Startup: 4.45 s before Linux, 1.41 s Linux to menu, 5.86 s total (MinUI)")):
        lines = list(earlier) + list(current)
        self.log.write_text("".join(line + "\n" for line in lines))

    def earlier_boot(self, boot_id, loader, header=None, extra=()):
        return [f"1.35 {header or HEADER.format(boot_id)}", *extra,
                "1.40 Startup: 1.46 s before Linux, 1.40 s Linux to menu, 2.86 s total (MinUI)", f"1.80 Loader: {loader}"]

    def run_diag(self, *args):
        result = subprocess.run(["/bin/busybox", "sh", str(SCRIPT), *args], env=self.env,
                                capture_output=True, text=True, timeout=120)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result.stdout, result.stderr

    def loader(self):
        out, err = self.run_diag("--loader")
        self.assertEqual(err, "")
        return out.strip()

    def boot(self):
        self.run_diag("--boot")
        return self.log.read_text()

    def this_boot(self):
        text = self.log.read_text()
        return text[text.rindex("=== BaseOS boot"):]

    def sample(self, name):
        target = os.environ.get("BASEOS_DIAG_SAMPLES")
        if target:
            shutil.copy(self.log, Path(target) / f"{name}.log")

    def assert_private(self, text):
        for secret in ("5c001040f4438852493", "aa:bb:cc:dd:ee:ff", "12:34:56:78:9a:bc", "snum", "serialno"):
            self.assertNotIn(secret, text)

    # ----------------------------------------------------- power diagnostics

    def test_power_report_does_not_poll_peripheral_batteries(self):
        supplies = self.root / "sys/class/power_supply"
        # A foreign sysfs read can synchronously request a report from a HID
        # device. FIFOs with no writer prove these attributes are never opened.
        for name in ("hid-controller-battery", "external-battery", "usb-controller", "ac-controller"):
            supply = supplies / name
            supply.mkdir()
            for field in ("type", "capacity", "status", "online"):
                os.mkfifo(supply / field)
        ac = supplies / "axp2202-ac"
        ac.mkdir()
        (ac / "online").write_text("1\n")
        process = subprocess.Popen(["/bin/busybox", "sh", str(SCRIPT)], env=self.env,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   text=True, start_new_session=True)
        try:
            out, err = process.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            # Terminate the shell and its blocked reader, not unrelated tests.
            os.killpg(process.pid, signal.SIGKILL)
            process.communicate()
            self.fail("Diagnostics blocked while reading a peripheral power supply")
        self.assertEqual(process.returncode, 0, out + err)
        self.assertIn("battery 62% Discharging", out)
        self.assertIn("axp2202-usb not connected", out)
        self.assertIn("axp2202-ac connected", out)
        for name in ("hid-controller-battery", "external-battery", "usb-controller", "ac-controller"):
            self.assertNotIn(name, out)

    # --------------------------------------------------------- loader line

    def test_fast_boot(self):
        self.native_boot()
        self.assertEqual(self.loader(), "Loader: fast")

    def test_fast_boot_of_another_generation(self):
        self.native_boot()
        (self.root / "sys/firmware/devicetree/base/chosen/baseos,native-boot").write_bytes(bytes(32))
        self.assertEqual(self.loader(), "Loader: fast, from another BaseOS build (generation 00000000)")

    def test_build_without_fast_loader(self):
        shutil.rmtree(self.root / "usr/share/baseos/native")
        self.assertEqual(self.loader(), f"Loader: stock (this BaseOS has no fast loader for {TARGET})")

    def test_not_installed_yet(self):
        self.flash(primary="stock", toc="package", native_files=False)
        self.assertEqual(self.loader(), "Loader: stock (fast loader not installed yet)")

    def test_waiting_for_usb_storage(self):
        self.result("deferred a computer owns USB storage")
        self.assertEqual(self.loader(), "Loader: stock (fast loader not installed yet: a computer was using USB storage)")

    def test_waiting_for_the_pair_step(self):
        self.result("deferred the boot pair step has not finished")
        pair = self.root / "data/boot-pair-v2"
        pair.mkdir(parents=True)
        (pair / "migration.log").write_text("boot-pair: deferred; waiting for external power or at least 50% battery\n")
        self.assertEqual(self.loader(), "Loader: stock (fast loader not installed yet: the earlier boot file update "
                                        "has not finished (deferred; waiting for external power or at least 50% battery))")

    def test_installed_this_boot(self):
        self.install_state(done="installed")
        self.result("installed installed and checked; it is used from the next start")
        self.assertEqual(self.loader(), "Loader: stock (fast loader not installed yet; it was installed and checked "
                                        "during this start and is used from the next one)")

    def test_install_failed_this_boot(self):
        self.result("failed failed at firmware (try 2 of 3; the stock loader stays in use and the next start tries again)")
        self.assertEqual(self.loader(), "Loader: stock (fast loader install failed at firmware (try 2 of 3; the stock "
                                        "loader stays in use and the next start tries again))")

    def test_latch_hold_failed_this_boot(self):
        self.result("failed could not hold the stock latch; nothing was written (try 1 of 3); the stock loader stays "
                    "in use and the next start tries again")
        self.assertIn("Loader: stock (fast loader install failed: could not hold the stock latch", self.loader())

    def test_skipped(self):
        self.install_state(done="skipped: sector 16 holds neither stock boot0 nor a BaseOS capsule")
        self.assertEqual(self.loader(), "Loader: stock (fast loader install not possible on this card: sector 16 holds "
                                        "neither stock boot0 nor a BaseOS capsule)")

    def test_gave_up_names_the_first_error(self):
        log = ("native: found primary stock, package earlier, firmware other, image other, pair idle, battery 80%, charger no\n"
               "native: writing firmware\nnative: firmware did not read back; writing it once more\n"
               "native: FAILED at firmware; keeping the stock latch, so the device keeps starting stock\n")
        self.install_state(done="gave up after 3 failed attempts; the device stays on stock startup", log=log, attempts=3)
        self.assertEqual(self.loader(), "Loader: stock (fast loader install gave up after 3 failed tries; first error: "
                                        "firmware did not read back; writing it once more)")

    def test_fast_start_failed_before_linux(self):
        self.install_state(done="installed")
        self.latched()
        self.assertEqual(self.loader(), "Loader: stock (the fast loader's start failed: it is installed, but its last "
                                        "start did not reach Linux, so the chip's latch now selects the stock loader)")

    def test_fast_start_failed_its_health_check(self):
        self.install_state(done="installed", log=INSTALL_LOG + (
            "native: a fast start reached Linux but failed its health check (7.81 s): native-health: acceptance refused "
            "(6): still waiting for the GPU (/dev/mali0) after 6 s; watchdog not reloaded \n"))
        self.latched()
        self.assertIn("its last start reached Linux and failed its health check (acceptance refused (6): "
                      "still waiting for the GPU (/dev/mali0) after 6 s; watchdog not reloaded)", self.loader())

    def test_interrupted_install(self):
        self.install_state(done="installed", latch=True)
        self.latched()
        self.assertEqual(self.loader(), "Loader: stock (a fast loader install was interrupted; it continues at the next start)")

    def test_latch_of_another_build(self):
        self.install_state(done="installed")
        self.set_regs(gp14="0823f549", gp15="42535031")
        self.assertIn("the chip's latch holds a value this build does not know (0823f549 42535031)", self.loader())

    def test_half_cleared_latch(self):
        self.install_state(done="installed")
        self.set_regs(gp14="00000000", gp15="42535031")
        self.assertIn("latch was being cleared when the device restarted", self.loader())

    def test_vendor_resume_flag(self):
        self.install_state(done="installed")
        self.set_regs(retention="00010000")
        self.assertIn("the chip asked for the vendor's resume path (RTC 0x070001f8 = 00010000)", self.loader())

    def test_unknown_reason(self):
        self.install_state(done="installed")
        self.assertIn("unknown: the fast loader is installed and the chip's latch is clear", self.loader())

    def test_unreadable_registers(self):
        self.install_state(done="installed")
        self.set_regs(unavailable=True)
        self.assertIn("the chip's latch could not be read", self.loader())

    def test_progress_trace(self):
        # The lab trace of a hang in the memory size check (v152-lp4 E6).
        self.install_state(done="installed")
        self.set_regs(gp14="b0002000", gp15="54054b45")
        self.assertIn("left a progress trace: memory setting 0, 5 setup run(s), size check, 11 columns, 17 rows, "
                      "1 rank(s), full width; last: probing size 8192 MiB", self.loader())

    # ------------------------------------------------ boot: line and block

    def test_normal_fast_boot_adds_only_its_loader_line(self):
        self.install_state(done="installed")
        self.native_boot(display=display_record())
        self.boot_log(*self.earlier_boot("5f06fbfa", "fast"), current=(
            "1.33 " + HEADER.format("70a21e9c"),
            "1.40 Startup: 1.45 s before Linux, 1.40 s Linux to menu, 2.85 s total (MinUI)"))
        before = self.log.read_text()
        after = self.boot()
        self.assertEqual(after, before + "1.52 Loader: fast\n")
        self.sample("fast-boot")

    def test_stock_fallback_after_install_gets_a_block(self):
        self.install_state(done="installed")
        self.latched()
        self.boot_log(*self.earlier_boot("5f06fbfa", "stock (fast loader not installed yet; it was installed and checked "
                                         "during this start and is used from the next one)",
                                         extra=["1.39 Fast loader install: installed and checked; it is used from the next start"]))
        text = self.boot()
        block = self.this_boot()
        self.assertIn("1.52 Loader: stock (the fast loader's start failed", block)
        self.assertIn("--- Diagnostics for boot 70a21e9c, ", block)
        self.assertIn("(the loader line changed) ---\n", block)
        lines = block[block.index("--- Diagnostics"):].splitlines()
        self.assertEqual(lines[1], "Variant: rg40xxh, LPDDR4 1024 MiB at 672 MHz, layout 0x30fa/0x4000000, "
                                   "panel boe, PMIC AXP2202")
        self.assertEqual(lines[2], "BaseOS: 1.5.2 (build v1.5.1-9-gtest) for rg40xxh (Anbernic RG40XX H)")
        self.assertTrue(lines[3].startswith("Loader: stock (the fast loader's start failed"))
        self.assertIn(f"Fast loader: generation 01020304, cookie {COOKIE}; installed generation 01020304", block)
        self.assertIn("A fast start reached Linux: no start since the install has", block)
        self.assertIn(f"Latch (RTC 0x07000138/0x0700013c): {COOKIE} 42535031, set by the fast loader itself", block)
        self.assertIn("  none (the stock loader leaves none)", block)
        self.assertIn("  sector 16: fast loader of this BaseOS (eGON header, 131072 bytes, checksum ok)", block)
        self.assertIn("  sector 512: vendor loader, the fallback copy (ok)", block)
        self.assertIn("  verdict: matches the BaseOS 1.5.2 fast loader for rg40xxh", block)
        self.assertIn("This unit's own trained memory settings (primary-before.bin): LPDDR4 at 672 MHz, layout "
                      "0x30fa/0x4000000, tpr6 0x40808080, tpr10 0x402f6633, tpr11 0x1c20211c, tpr12 0x05050506", block)
        self.assertIn("Chip: id low half 00005c00", block)
        self.assertIn("Power now: battery 62% Discharging, axp2202-usb not connected; at the install: battery 34%, charger no",
                      block)
        self.assertIn("Health check now (read only): exit 3, absent, malformed or mismatched generation", block)
        self.assertNotIn("records in", block)
        self.assertIn("  stock latch cleared; the native loader starts at the next boot", block)
        self.assertIn("  [    0.000000] Memory: 1003284K/1048576K available", block)
        self.assertNotIn("unrelated line", block)
        self.assertIn("  [    0.500000] disp: serial=(hidden) panel probe failed", block)
        self.assertTrue(text.endswith("--- end of diagnostics ---\n"))
        self.assert_private(text)
        self.sample("stock-fallback")

    def test_same_stock_fallback_again_stays_quiet_then_repeats(self):
        self.install_state(done="installed")
        self.latched()
        line = self.loader().removeprefix("Loader: ")
        earlier = self.earlier_boot("00000001", line) + ["--- Diagnostics for boot 00000001 (x) ---", "--- end of diagnostics ---"]
        for n in range(2, 20):
            earlier += self.earlier_boot(f"{n:08x}", line)
        self.boot_log(*earlier)
        self.assertNotIn("--- Diagnostics for boot 70a21e9c", self.boot())
        (self.root / "run/baseos-diag-boot").unlink()
        self.boot_log(*earlier, *self.earlier_boot("00000020", line))
        self.assertIn("(the fast loader is installed but the stock loader started) ---", self.boot())

    def test_first_boot_of_a_new_build_gets_a_block(self):
        self.native_boot()
        self.install_state(done="installed")
        old = "=== BaseOS boot 2026-10-17 21:15:00 (70a21e9c-36bf-461c-84ba-35da198dd2d7) ==="
        self.boot_log(*self.earlier_boot("1", "fast", header=old))
        self.assertIn("(first boot of BaseOS 1.5.2) ---", self.boot())

    def test_install_this_boot_gets_a_block(self):
        self.flash(primary="stock", toc="package", native_files=False)
        self.boot_log(*self.earlier_boot("5f06fbfa", "stock (fast loader not installed yet)"))
        self.result("failed failed at firmware (try 1 of 3; the stock loader stays in use and the next start tries again)")
        block = self.boot()
        self.assertIn("(the fast loader install ran) ---", block)
        self.assertIn("  verdict: stock vendor loader\n", block)
        self.assertIn("  sector 16: vendor loader (eGON header, 65536 bytes, checksum ok)", block)
        self.assertIn("  U-Boot package (sector 700): the 1.4.0 package", block)

    def test_health_failure_from_the_last_start_gets_a_block(self):
        self.install_state(done="installed")
        self.latched()
        line = self.loader().removeprefix("Loader: ")
        self.boot_log(*self.earlier_boot("5f06fbfa", line), current=(
            "1.36 " + HEADER.format("70a21e9c"),
            "7.81 An earlier start with the fast loader reached Linux but failed its health check: x"))
        self.assertIn("(a fast start failed its health check) ---", self.boot())

    def test_mismatched_boot_areas(self):
        damaged = bytearray(self.stock)
        damaged[600] ^= 1
        self.flash(secondary=bytes(damaged))
        self.install_state(done="installed")
        out, _ = self.run_diag()
        self.assertIn("  sector 512: NOT a usable vendor loader (checksum does not add up)", out)
        self.assertIn("  verdict: mismatch (sector 16 fast, sector 512 bad, package fast, firmware ok, kernel ok, "
                      "stock kernel ok)", out)

    def test_loader_records_are_decoded(self):
        self.native_boot(display=display_record(panel=1, shown=36_000_000))
        out, _ = self.run_diag()
        self.assertIn("  baseos,native-boot: generation 01020304\n", out)
        self.assertIn("  baseos,boot-display: panel boe, logo lit 1.500 s after power-on, record valid", out)
        self.assertIn("  baseos,native-timing: first stage 0.100, card read 0.200, files loaded 0.500, second stage "
                      "0.600, kernel unpacked 1.000, device tree ready 1.100, kernel started 1.200 s", out)
        self.assertIn("A fast start reached Linux: yes, this start", out)
        self.assertIn("Latch (RTC 0x07000138/0x0700013c): 00000000 00000000, clear", out)

    def test_damaged_display_record(self):
        self.native_boot(display=display_record(damage=True))
        out, _ = self.run_diag()
        self.assertIn("record INVALID", out)

    def test_asked_for_by_hand(self):
        self.boot_log()
        out, err = self.run_diag()
        self.assertTrue(out.startswith("--- Diagnostics for boot 70a21e9c, "), out)
        self.assertIn("(asked for by hand) ---", out)
        self.assertIn("Added to", err)
        self.assertTrue(self.log.read_text().endswith(out))
        self.assert_private(out)

    def test_by_hand_without_a_card_prints_only(self):
        (self.root / "no-card").touch()
        out, err = self.run_diag()
        self.assertIn("--- end of diagnostics ---", out)
        self.assertIn("Not added", err)
        self.assertFalse(self.log.exists())

    def test_usb_storage_leaves_the_card_alone(self):
        self.boot_log()
        before = self.log.read_text()
        (self.root / "run/usb-storage-device").write_text("/dev/mmcblk0p7\n")
        self.assertEqual(self.run_diag("--boot")[0], "")
        self.assertEqual(self.run_diag()[0], "")
        self.assertEqual(self.log.read_text(), before)

    def test_once_per_boot(self):
        self.native_boot()
        self.boot_log(*self.earlier_boot("1", "fast"))
        self.boot()
        self.boot()
        self.assertEqual(self.log.read_text().count("Loader: fast"), 2)

    def test_log_is_trimmed_by_whole_boots_in_place(self):
        self.native_boot()
        earlier = []
        for n in range(4000):
            earlier += self.earlier_boot(f"{n:08x}", "fast")
        self.boot_log(*earlier)
        self.assertGreater(self.log.stat().st_size, 262144)
        text = self.boot()
        self.assertLess(len(text), 262144)
        self.assertGreater(len(text), 150000)
        lines = text.splitlines()
        self.assertEqual(lines[0], "[older boots removed to keep this log small]")
        self.assertIn("=== BaseOS boot", lines[1])
        self.assertTrue(text.endswith(HEADER.format("70a21e9c") + "\n1.41 Startup: 4.45 s before Linux, 1.41 s Linux "
                                      "to menu, 5.86 s total (MinUI)\n1.52 Loader: fast\n"))
        self.assertEqual(sorted(os.listdir(self.root / "mnt/sdcard")), ["baseos-boot.log"])

    def test_nothing_new_on_the_card_or_data(self):
        self.install_state(done="installed")
        self.latched()
        self.boot_log()
        data_before = sorted(str(p) for p in (self.root / "data").rglob("*"))
        self.run_diag("--boot")
        self.run_diag()
        self.assertEqual(sorted(os.listdir(self.root / "mnt/sdcard")), ["baseos-boot.log"])
        self.assertEqual(sorted(str(p) for p in (self.root / "data").rglob("*")), data_before)
        self.assertEqual(os.listdir(self.root / "tmp"), [])

    def test_update_sample(self):
        """The two boots after an update from 1.5.1, as the samples show them."""
        self.install_state(done="installed", log=INSTALL_LOG + INSTALL_LOG)
        old = HEADER.replace("1.5.2", "1.5.1")
        self.boot_log(*self.earlier_boot("5f06fbfa", "fast", header=old.format("5f06fbfa")),
                      "1.34 " + old.format("ba5357d2"),
                      "1.41 Startup: 1.46 s before Linux, 1.41 s Linux to menu, 2.87 s total (MinUI)",
                      "1.80 Loader: fast",
                      "61.02 update: installing 1.5.2 (v1.5.1-9-gtest) from baseos-rg40xxh-1.5.2.bosupd",
                      "130.11 update: 1.5.2 written and checked; restarting into it",
                      current=("1.36 " + HEADER.format("70a21e9c"),
                               "1.28 update: trying 1.5.2, start 1 of 3",
                               "7.62 An earlier start with the fast loader reached Linux but failed its health check: "
                               "native-health: absent, malformed or mismatched generation (expected on the first start "
                               "after an update: the fast loader on the card is the earlier version's; the stock start "
                               "that follows installs the new one)",
                               "1.95 Fast loader install: installed and checked; it is used from the next start",
                               "2.01 update to 1.5.2 confirmed",
                               "2.02 Startup: 4.45 s before Linux, 2.02 s Linux to menu, 6.47 s total (MinUI)"))
        self.result("installed installed and checked; it is used from the next start")
        self.assertIn("(first boot of BaseOS 1.5.2) ---", self.boot())
        # The next start uses the new fast loader.
        (self.root / "run/baseos-native-result").unlink()
        (self.root / "run/baseos-diag-boot").unlink()
        self.native_boot(display=display_record())
        (self.root / "proc/sys/kernel/random/boot_id").write_text("0c1b3154-0000-4000-8000-000000000000\n")
        with self.log.open("a") as log:
            log.write("1.33 " + HEADER.format("0c1b3154") + "\n"
                      "1.38 Startup: 1.46 s before Linux, 1.38 s Linux to menu, 2.84 s total (MinUI)\n")
        self.boot()
        self.assertIn("(the loader line changed) ---", self.this_boot())
        self.sample("update")

if __name__ == "__main__":
    unittest.main(verbosity=1)
