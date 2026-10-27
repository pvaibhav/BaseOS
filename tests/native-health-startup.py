"""Execute rcS with the real health CLI and modeled hardware in a disposable chroot."""
import argparse
import ast
import struct
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Reuse the existing disposable BusyBox chroot, without running its test suite.
policy = ROOT / "tests/poweroff-policy.py"
tree = ast.parse(policy.read_text())
definitions = [node for node in tree.body
               if isinstance(node, (ast.Import, ast.ImportFrom, ast.Assign, ast.ClassDef))]
namespace = {"__file__": str(policy)}
exec(compile(ast.Module(body=definitions, type_ignores=[]), str(policy), "exec"), namespace)
Boot = namespace["Boot"]
GENERATION = bytes.fromhex("33dc8a21142807a21e6f9dc5ae861b37a9f55337705c0ae86495eb3f9c0d8513")
DISPLAY = "sys/firmware/devicetree/base/chosen/baseos,boot-display"


def display_record(panel, generation=GENERATION, blank=False):
    """The loader's 80-byte display record, as the A32 loader publishes it."""
    record = bytearray(struct.pack("<8I", 0x31505344, 1, 80, 0, panel,
                                   0 if blank else 921654, 0 if blank else 0x12345678, 0x9abcdef0))
    record += generation + struct.pack("<Q2I", 36312000, 0, 0)
    value = 2166136261
    for byte in record:
        value = (value ^ byte) * 16777619 & 0xffffffff
    struct.pack_into("<I", record, 12, value)
    return bytes(record)


def boot_fixture(args, *, native=True, **env):
    boot = Boot(**env)
    boot.write("etc/init.d/rcS", args.rcs.read_bytes(), executable=True)
    boot.write("test/native-health-model", args.helper.read_bytes(), executable=True)
    boot.stub("usr/sbin/native-health", 'exec /test/native-health-model "$@"')
    boot.stub("usr/sbin/baseos-update", '''
if [ "$1" = "${UPDATE_REBOOT_AT:-never}" ]; then
    printf '%s\\n' "$1" > /run/reboot-requested
fi
''')
    boot.stub("usr/sbin/baseos-charger", '''
while [ ! -e /run/release-charger ]; do /test/busybox sleep 0.01; done
''')
    if native:
        boot.write("sys/firmware/devicetree/base/chosen/baseos,native-boot", GENERATION)
        boot.write("etc/baseos/native-generation", GENERATION)
        boot.write(DISPLAY, display_record(0))
        boot.write("proc/cmdline", boot.read("proc/cmdline") + " lcd_type=old\n")
    return boot


def assert_blocked(boot, before_charger=False):
    boot.await_call("bin/sleep 60")
    assert boot.proc.poll() is None
    calls = boot.calls()
    assert "baseos-update" not in calls, calls
    assert " -t vfat " not in calls and " -t exfat " not in calls, calls
    assert "hwclock" not in calls and "rfkill" not in calls and "bin/ip " not in calls, calls
    if before_charger:
        assert "baseos-charger" not in calls and "insmod" not in calls, calls


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rcs", type=Path, required=True)
    parser.add_argument("--helper", type=Path, required=True)
    args = parser.parse_args()
    source = args.rcs.read_text()
    assert source.index("mali_kbase.ko") < source.index("native-health --accept 2>&1")
    assert source.index("/dev/mmcblk0p6 /data") < source.index("native-health --accept 2>&1")
    assert source.index("native-health --accept 2>&1") < source.index("baseos-update boot-check")
    assert source.index("native-health --accept 2>&1") < source.index("baseos-update apply")
    assert source.index("native-health --accept-charger") < source.index("while ! /usr/sbin/baseos-charger")

    for reboot_at in ("boot-check", "apply"):
        boot = boot_fixture(args, mode="0", UPDATE_REBOOT_AT=reboot_at)
        try:
            boot.start()
            assert boot.proc.wait(timeout=4) == 0, boot.calls()
            calls = boot.calls()
            assert calls.count("usr/sbin/native-health --accept\n") == 1, calls
            assert "baseos-charger" not in calls, calls
            assert calls.index("usr/sbin/native-health --accept\n") < calls.index("baseos-update " + reboot_at), calls
            assert boot.read("run/reboot-requested") == reboot_at
        finally:
            boot.close()
        print("PASS: native acceptance precedes updater", reboot_at)

    boot = boot_fixture(args, mode="0", UPDATE_REBOOT_AT="boot-check")
    try:
        boot.write(DISPLAY, display_record(0, blank=True))
        boot.start()
        assert boot.proc.wait(timeout=4) == 0, boot.calls()
        assert "usr/sbin/native-health --accept\n" in boot.calls()
        assert boot.read("run/reboot-requested") == "boot-check"
    finally:
        boot.close()
    print("PASS: a lit blank framebuffer passes startup acceptance")

    for fault in ("wrong-panel", "foreign-record", "duplicate-token", "missing-token"):
        boot = boot_fixture(args, mode="0")
        try:
            if fault == "foreign-record":
                boot.write(DISPLAY, display_record(0, bytes(32)))
            else:
                commands = {"wrong-panel": "lcd_type=boe\n",
                            "duplicate-token": "lcd_type=old lcd_type=old\n",
                            "missing-token": "quiet\n"}
                boot.write("proc/cmdline", commands[fault])
            boot.start()
            assert_blocked(boot)
        finally:
            boot.close()
        print("PASS: healthy GPU/DE with", fault, "keeps native startup blocked")

    # A refusal leaves its reason on /data for the next start, then waits for
    # the watchdog as before.
    boot = boot_fixture(args, mode="0", HEALTH_MODEL_GPU_MISSING="1")
    try:
        boot.write("data/boot-native-v1/install.log", b"native: installed\n")
        boot.start()
        assert_blocked(boot)
        spool = boot.read("data/baseos-boot.log")
        assert "An earlier start with the fast loader reached Linux but failed its health check: " in spool, spool
        assert "still waiting for the GPU (/dev/mali0)" in spool, spool
        record = boot.read("data/boot-native-v1/install.log")
        assert record.startswith("native: installed\nnative: a fast start reached Linux but failed its health check"), record
    finally:
        boot.close()
    print("PASS: a refused fast start records its reason on /data before waiting")

    boot = boot_fixture(args, mode="1")
    try:
        boot.write("proc/cmdline", "bootreason=charger lcd_type=boe\n")
        boot.start()
        assert_blocked(boot, before_charger=True)
    finally:
        boot.close()
    print("PASS: wrong panel blocks charger acceptance before hardware/startup")

    boot = boot_fixture(args, mode="0")
    try:
        boot.write(DISPLAY, display_record(1))
        boot.write("proc/cmdline", "quiet lcd_type=boe\n")
        boot.start()
        assert boot.proc.wait(timeout=4) == 0, boot.calls()
        assert "native-health --accept" in boot.calls() and "baseos-update boot-check" in boot.calls(), boot.calls()
    finally:
        boot.close()
    print("PASS: matched boe panel reaches normal accepted startup")

    for fault in ("HEALTH_MODEL_GPU_MISSING", "HEALTH_MODEL_BAD_DE"):
        boot = boot_fixture(args, mode="0", **{fault: "1"})
        try:
            boot.start()
            assert_blocked(boot)
        finally:
            boot.close()
        print("PASS: pending", fault, "blocks both updater commands")

    for bad_display in (False, True):
        env = {"HEALTH_MODEL_GPU_MISSING": "1"}
        if bad_display:
            env["HEALTH_MODEL_BAD_DE"] = "1"
        boot = boot_fixture(args, mode="1", **env)
        try:
            boot.start()
            if bad_display:
                assert_blocked(boot, before_charger=True)
            else:
                boot.await_call("usr/sbin/baseos-charger")
                calls = boot.calls()
                assert calls.index("native-health --accept-charger") < calls.index("baseos-charger"), calls
                assert "insmod" not in calls and "baseos-update" not in calls, calls
                assert boot.proc.poll() is None
        finally:
            boot.close()
        print("PASS:", "bad DE cannot enter charger" if bad_display else "charger accepted before GPU/poweroff path")

    boot = boot_fixture(args, native=False, mode="0")
    try:
        (boot.root / "usr/sbin/native-health").unlink()
        boot.start()
        assert boot.proc.wait(timeout=4) == 0, boot.calls()
        assert "native-health" not in boot.calls() and "baseos-update boot-check" in boot.calls(), boot.calls()
    finally:
        boot.close()
    print("PASS: legacy boot needs no helper")

    boot = boot_fixture(args, native=False, mode="1")
    try:
        (boot.root / "usr/sbin/native-health").unlink()
        boot.start()
        boot.await_call("usr/sbin/baseos-charger")
        calls = boot.calls()
        assert "native-health" not in calls and "insmod" not in calls and "baseos-update" not in calls, calls
        assert boot.proc.poll() is None
    finally:
        boot.close()
    print("PASS: legacy charger boot needs no helper")

    boot = boot_fixture(args, mode="0")
    try:
        (boot.root / "usr/sbin/native-health").unlink()
        boot.start()
        assert_blocked(boot)
    finally:
        boot.close()
    print("PASS: missing native helper blocks updater commands")

    boot = boot_fixture(args, mode="0")
    try:
        boot.write("etc/baseos/native-generation", bytes(32))
        boot.start()
        assert_blocked(boot)
    finally:
        boot.close()
    print("PASS: generation mismatch blocks updater commands")


if __name__ == "__main__":
    main()
