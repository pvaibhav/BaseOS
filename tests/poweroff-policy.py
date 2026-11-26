"""Run the installed shell scripts in a disposable BusyBox chroot.

All hardware, mount, kill and power commands are stubs inside that chroot.
The C tests separately exercise actual PMIC and input logic with fake devices.
"""
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
BUSYBOX = Path("/bin/busybox.static")


class Boot:
    def __init__(self, mode="1", cmdline="bootreason=charger", **env):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.env = dict(os.environ, **env)
        self.proc = None
        self.write("test/busybox", BUSYBOX.read_bytes(), executable=True)
        for command in ("sh", "cat", "cut", "cp", "dd", "grep", "ln", "mkdir",
                        "mv", "tr", "rm", "sleep", "date", "mountpoint"):
            path = self.root / "bin" / command
            path.parent.mkdir(exist_ok=True)
            path.symlink_to("/test/busybox")
        for directory in ("data", "run", "tmp", "var", "mnt", "root",
                          "sys/class/net/wlan0"):
            (self.root / directory).mkdir(parents=True, exist_ok=True)
        self.write("proc/mounts", "/dev/mmcblk0p5 / ext4 rw 0 0\ndev /dev devtmpfs rw 0 0\n")
        self.write("proc/cmdline", cmdline + "\n")
        self.write("proc/uptime", "1.23 0\n")
        self.write("proc/sys/kernel/random/uuid", "test-id\n")
        self.write("proc/sys/kernel/random/boot_id", "test-boot-id\n")
        if mode is not None:
            self.write("sys/class/power_supply/axp2202-battery/boot_mode", mode + "\n")
        self.write("sys/class/rtc/rtc0/since_epoch", "1000\n")
        self.write("sys/class/backlight/backlight/brightness", "180\n")
        self.write("sys/class/power_supply/axp2202-battery/work_led", "0\n")
        self.write("sys/devices/system/cpu/cpufreq/policy0/scaling_governor", "performance\n")
        self.write("dev/null", "")
        self.write("dev/console", "")
        self.write("dev/urandom", "seed")
        self.write("etc/hostname", "baseos\n")
        self.write("data/update/state", "trial\n")
        for script in ("etc/init.d/rcS", "etc/init.d/rcK", "usr/sbin/baseos-charger",
                       "usr/bin/baseos-splash", "usr/share/baseos/boot-log.sh"):
            self.write(script, (ROOT / "overlay" / script).read_bytes(), executable=True)
        self.stub("bin/mount", '''
case "$*" in
  *'remount,rw /data'*) exit "${REMOUNT_FAIL:-0}" ;;
  *'/dev/mmcblk0p6 /data'*) exit "${DATA_FAIL:-0}" ;;
esac
''')
        self.stub("bin/umount", 'exit "${UMOUNT_FAIL:-0}"')
        self.stub("usr/sbin/boot-menu-held", '''
[ -e /run/menu-checked ] && exit 1
: > /run/menu-checked
exit "${MENU_STATUS:-1}"
''')
        self.stub("usr/sbin/axp-off", '''
[ "$#" -gt 0 ] && exit 1
exit "${PROBE_STATUS:-0}"
''')
        self.stub("usr/sbin/charger-wait", '''
if [ "${WAIT_FAIL_FIRST:-0}" = 1 ] && [ ! -e /run/failed-once ]; then
  : > /run/failed-once
  exit 1
fi
while [ ! -e /run/allow-boot ]; do /test/busybox sleep 0.01; done
''')
        self.stub("bin/sleep", '''
if [ "$1" = 60 ]; then
  while [ ! -e /run/allow-retry ]; do /test/busybox sleep 0.01; done
fi
''')
        self.stub("usr/bin/fbsplash", '''
[ "$(cat /sys/devices/system/cpu/cpufreq/policy0/scaling_governor)" = performance ] || exit 2
[ "$(cat /sys/class/backlight/backlight/brightness)" = 180 ] || exit 2
[ "$(cat /sys/class/power_supply/axp2202-battery/work_led)" = 1 ] || exit 2
: > /run/boot-feedback
exit "${SPLASH_STATUS:-0}"
''')
        for command in ("usr/bin/busybox", "bin/sync", "sbin/swapoff", "sbin/insmod",
                        "sbin/hwclock", "bin/hostname", "bin/rfkill", "bin/ip",
                        "usr/sbin/baseos-update", "usr/sbin/expand-storage",
                        "usr/sbin/usb-storage-mode"):
            self.stub(command, "exit 0")

    def write(self, path, contents, executable=False):
        path = self.root / path
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.is_symlink():
            path.unlink()
        if isinstance(contents, bytes):
            path.write_bytes(contents)
        else:
            path.write_text(contents)
        if executable:
            path.chmod(0o755)

    def stub(self, path, body):
        self.write(path, f'#!/bin/sh\nprintf "%s %s\\n" "{path}" "$*" >> /run/calls\n{body}\n', True)

    def start(self, script="etc/init.d/rcS"):
        self.proc = subprocess.Popen(["chroot", str(self.root), "/bin/sh", "/" + script],
                                     env=self.env, stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL, start_new_session=True)

    def calls(self):
        path = self.root / "run/calls"
        return path.read_text() if path.exists() else ""

    def await_call(self, text):
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            if text in self.calls():
                return
            if self.proc.poll() is not None:
                break
            time.sleep(0.01)
        raise AssertionError(f"missing {text}: {self.calls()}")

    def blocked(self):
        self.await_call("usr/sbin/charger-wait")
        assert self.proc.poll() is None
        calls = self.calls()
        assert "insmod" not in calls and "baseos-update" not in calls, calls
        assert "fbsplash" not in calls, calls
        assert self.read("sys/class/power_supply/axp2202-battery/work_led") == "0"
        assert self.read("sys/class/backlight/backlight/brightness") == "0"
        assert self.read("sys/devices/system/cpu/cpufreq/policy0/scaling_governor") == "powersave"

    def read(self, path):
        return (self.root / path).read_text().strip()

    def finish(self):
        self.write("run/allow-boot", "")
        self.write("run/allow-retry", "")
        assert self.proc.wait(timeout=4) == 0
        assert "baseos-update boot-check" in self.calls()
        assert self.read("sys/class/backlight/backlight/brightness") == "180"
        assert self.read("sys/devices/system/cpu/cpufreq/policy0/scaling_governor") == "performance"
        if "usr/sbin/charger-wait" in self.calls():
            assert (self.root / "run/boot-feedback").exists()
            assert self.calls().count("usr/bin/fbsplash 100\n") == 1
            assert self.calls().index("usr/bin/fbsplash") < self.calls().index("sbin/insmod")
        else:
            assert "fbsplash" not in self.calls()

    def close(self):
        if self.proc:
            try:
                os.killpg(self.proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            self.proc.wait()
        self.tmp.cleanup()


def check(name, test):
    boot = Boot(**test.pop("env", {}))
    try:
        for path, value in test.pop("files", {}).items():
            boot.write(path, value)
        boot.start()
        if test.pop("wait", True):
            boot.blocked()
            assert ("usr/sbin/axp-off --now" in boot.calls()) == test.pop("cut", False), boot.calls()
        else:
            boot.proc.wait(timeout=4)
            assert "axp-off" not in boot.calls() and "charger-wait" not in boot.calls()
        boot.finish()
        if test.pop("menu", False):
            assert "usb-storage-mode prepare" in boot.calls()
        assert not test, test
        print("PASS:", name)
    finally:
        boot.close()


check("normal boot skips all charger work", {"env": {"mode": "0"}, "wait": False})
boot_no_trial = Boot(mode="0")
try:
    (boot_no_trial.root / "data/update/state").unlink()
    boot_no_trial.start()
    assert boot_no_trial.proc.wait(timeout=4) == 0
    assert "baseos-update boot-check" not in boot_no_trial.calls()
    print("PASS: normal boot without update trial skips baseos-update boot-check")
finally:
    boot_no_trial.close()
check("exact cmdline token", {"env": {"mode": None, "cmdline": "bootreason=charger-extra"}, "wait": False})
check("normal latched mode wins over cmdline", {"env": {"mode": "0"}, "wait": False})
check("older kernel charger token", {"env": {"mode": None}, "cut": True})
check("failed PMIC cut waits for POWER", {"cut": True})
check("30-second replug stays charging", {"files": {"data/charger-off-stamp": "970\n"}})
check("future RTC stamp stays charging", {"files": {"data/charger-off-stamp": "1100\n"}})
check("expired stamp permits one attempt", {"files": {"data/charger-off-stamp": "800\n"}, "cut": True})
check("missing RTC stays charging", {"files": {"sys/class/rtc/rtc0/since_epoch": "bad\n"}})
check("missing data stays charging", {"env": {"DATA_FAIL": "1"}})
check("read-only data stays charging", {"env": {"REMOUNT_FAIL": "1"}})
check("PMIC probe failure stays charging", {"env": {"PROBE_STATUS": "1"}})
check("input helper failure cannot start frontend", {"env": {"WAIT_FAIL_FIRST": "1"}, "cut": True})
check("failed boot-logo draw does not prevent startup", {"env": {"SPLASH_STATUS": "1"}, "cut": True})
check("opt-out boots normally", {"files": {"data/no-charger-off": ""}, "wait": False})
check("MENU intent survives early release", {"env": {"MENU_STATUS": "0"}, "wait": False, "menu": True})

for poweroff in (False, True):
    boot = Boot()
    try:
        if poweroff:
            boot.write("run/poweroff-requested", "")
        boot.start("etc/init.d/rcK")
        assert boot.proc.wait(timeout=4) == 0
        calls = boot.calls()
        assert calls.index("killall5 -TERM") < calls.index("killall5 -KILL") < calls.index("swapoff -a")
        assert ("axp-off --now" in calls) == poweroff
        if poweroff:
            assert calls.index("killall5 -KILL") < calls.index("axp-off --now") < calls.index("umount -a -r")
        print("PASS:", "poweroff quiesces writers" if poweroff else "reboot never cuts PMIC")
    finally:
        boot.close()
