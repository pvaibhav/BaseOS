#!/usr/bin/env python3
"""Run the generated library shell with isolated files and fake mount/copy tools."""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
spec = importlib.util.spec_from_file_location("sequential_fixture", HERE / "build-fixture.py")
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)

FAKE_COMMAND = r'''#!PYTHON
import json, os, pathlib, sys
root = pathlib.Path(os.environ['FIXTURE_ROOT'])
command, args = pathlib.Path(sys.argv[0]).name, sys.argv[1:]
faults = os.environ.get('FIXTURE_FAULTS', '').split(',')
with (root / 'commands.jsonl').open('a') as log:
    log.write(json.dumps([command, *args]) + '\n')
marker = root / 'etc/baseos-lab-ram-libs'
ram = root / 'usr/share/baseos/lab-ram-libs'
if command == 'rm':
    path = pathlib.Path(args[-1])
    if path == marker and 'consume' in faults: sys.exit(1)
    try: path.unlink()
    except FileNotFoundError: pass
    except IsADirectoryError: sys.exit(1)
elif command == 'sync':
    if 'sync' in faults: sys.exit(1)
elif command == 'mkdir':
    if 'mkdir' in faults: sys.exit(1)
    pathlib.Path(args[-1]).mkdir(parents=True, exist_ok=True)
elif command == 'mount':
    if 'tmpfs' in args:
        assert not marker.exists(), 'RAM work began before marker consumption'
        if 'tmpfs' in faults: sys.exit(1)
    else:
        source = pathlib.Path(args[-2])
        if source.parent == ram and 'ram-mount' in faults: sys.exit(1)
        if source.parent != ram and 'disk-mount' in faults: sys.exit(1)
        assert source.read_bytes(), 'mount requested an empty image'
    with (root / 'mounts.jsonl').open('a') as log:
        log.write(json.dumps(args) + '\n')
elif command == 'cp':
    assert not marker.exists(), 'copy began before marker consumption'
    data = pathlib.Path(args[0]).read_bytes()
    pathlib.Path(args[1]).write_bytes(data[:7] if 'copy' in faults else data)
    (root / 'proc/uptime').write_text('2.73 0.00\n')
    (root / 'sys/block/mmcblk0/stat').write_text('2 0 53525 0 0 0 0 0 0 1397 0\n')
    if 'copy' in faults: sys.exit(1)
elif command == 'umount':
    if 'umount' in faults: sys.exit(1)
else:
    raise AssertionError(command)
'''.replace("#!PYTHON", "#!" + sys.executable)


class GeneratedShellTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = (ROOT / "overlay/etc/init.d/rcS").read_text()
        cls.sampler = builder.sampler_from_source((HERE.parent / "first-frame.py").read_text())
        cls.fixture = builder.build_fixture(cls.source, cls.sampler)
        start = cls.fixture.index("# LAB sequential library fixture begin")
        end = cls.fixture.index("# LAB sequential library fixture end")
        cls.shell = cls.fixture[start:end]

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="baseos-sequential-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for directory in ("bin", "etc", "usr/share/baseos", "usr/lib", "proc", "sys/block/mmcblk0"):
            (self.root / directory).mkdir(parents=True, exist_ok=True)
        self.marker = self.root / "etc/baseos-lab-ram-libs"
        self.image = self.root / "usr/share/baseos/usr-lib.sqfs"
        self.ram_dir = self.root / "usr/share/baseos/lab-ram-libs"
        self.image.write_bytes(b"hsqs\x00same exact library image\xff" * 100)
        (self.root / "proc/uptime").write_text("1.23 0.00\n")
        (self.root / "sys/block/mmcblk0/stat").write_text("1 0 100 0 0 0 0 0 0 7 0\n")
        fake = self.root / "bin/fake-command"
        fake.write_text(FAKE_COMMAND)
        fake.chmod(0o755)
        for command in ("mount", "umount", "mkdir", "cp", "rm", "sync"):
            (self.root / "bin" / command).symlink_to(fake)

    def commands(self):
        path = self.root / "commands.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def run_shell(self, mode=None, faults=()):
        if mode is not None:
            self.marker.write_text(mode + "\n")
        shell = self.shell
        # Redirect only the generated block's external boundaries. Shell branching,
        # marker handling, read/printf builtins and fallback ordering remain real.
        for path in (
            "/usr/share/baseos/usr-lib.sqfs", "/usr/share/baseos/lab-ram-libs",
            "/etc/baseos-lab-ram-libs", "/sys/block/mmcblk0/stat", "/proc/uptime", "/usr/lib",
        ):
            shell = shell.replace(path, shlex.quote(str(self.root / path.lstrip("/"))))
        for command in ("mount", "umount", "mkdir", "cp", "rm", "sync"):
            shell = shell.replace("/bin/" + command, shlex.quote(str(self.root / "bin" / command)))
        # Preserve each kmsg write in the captured pipe without opening a host device.
        shell = shell.replace("> /dev/kmsg", ">&1")
        result = subprocess.run(
            ["/bin/sh", "-c", shell], capture_output=True, text=True,
            env={**os.environ, "FIXTURE_ROOT": str(self.root), "FIXTURE_FAULTS": ",".join(faults)},
            timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        log_lines = [line for line in result.stdout.splitlines() if line.startswith("baseos-lab-ram-libs ")]
        self.assertEqual(len(log_lines), 2, repr(result.stdout))
        self.assertTrue(result.stdout.endswith("\n"), repr(result.stdout))
        self.assertNotIn("\\n", result.stdout)
        records = [dict(item.split("=", 1) for item in line.split()[1:])
                   for line in log_lines]
        self.assertEqual([row["phase"] for row in records], ["start", "end"], result.stdout)
        return records, result.stdout

    def squash_mounts(self):
        return [call for call in self.commands() if call[0] == "mount" and "squashfs" in call]

    def test_missing_marker_mounts_disk_without_ram_work(self):
        rows, _ = self.run_shell()
        self.assertEqual(rows[-1], dict(phase="end", mode="baseline", status="default", backing="disk",
                                      uptime="1.23", card_read_sectors="100", card_busy_ms="7"))
        self.assertEqual([call[0] for call in self.commands()], ["mount"])
        self.assertEqual(self.squash_mounts()[0][-2], str(self.image))

    def test_baseline_consumes_marker_once_then_defaults(self):
        rows, _ = self.run_shell("baseline")
        self.assertFalse(self.marker.exists())
        self.assertEqual(rows[-1]["status"], "selected")
        self.assertEqual([call[0] for call in self.commands()], ["rm", "sync", "mount"])
        before = len(self.commands())
        rows, _ = self.run_shell()
        self.assertEqual(rows[-1]["status"], "default")
        self.assertEqual([call[0] for call in self.commands()[before:]], ["mount"])

    def test_ram_success_copies_exact_image_after_durable_consumption(self):
        rows, _ = self.run_shell("ram")
        self.assertFalse(self.marker.exists())
        self.assertEqual((self.ram_dir / "usr-lib.sqfs").read_bytes(), self.image.read_bytes())
        self.assertEqual(rows[-1], dict(phase="end", mode="ram", status="ram-mounted", backing="ram",
                                      uptime="2.73", card_read_sectors="53525", card_busy_ms="1397"))
        calls = self.commands()
        self.assertEqual([call[0] for call in calls], ["rm", "sync", "mkdir", "mount", "cp", "mount"])
        self.assertIn("mode=700,size=64m,nodev,nosuid", calls[3])
        self.assertEqual(self.squash_mounts()[0][-2], str(self.ram_dir / "usr-lib.sqfs"))
        self.assertEqual(self.squash_mounts()[0][4], "loop,ro,nodev,nosuid")
        before = len(calls)
        rows, _ = self.run_shell()
        self.assertEqual(rows[-1]["mode"], "baseline")
        self.assertEqual([call[0] for call in self.commands()[before:]], ["mount"])

    def test_ram_failures_fall_back_to_original_disk_image(self):
        for fault, status in (("mkdir", "mkdir-failed"), ("tmpfs", "tmpfs-failed"),
                              ("copy", "copy-failed"), ("ram-mount", "ram-mount-failed")):
            with self.subTest(fault=fault):
                (self.root / "commands.jsonl").unlink(missing_ok=True)
                rows, _ = self.run_shell("ram", (fault,))
                self.assertFalse(self.marker.exists())
                self.assertEqual(rows[-1]["status"], "fallback-" + status)
                self.assertEqual(rows[-1]["backing"], "disk")
                self.assertEqual(self.squash_mounts()[-1][-2], str(self.image))
                self.assertFalse((self.ram_dir / "usr-lib.sqfs").exists())
                if fault in ("copy", "ram-mount"):
                    self.assertEqual([call[0] for call in self.commands()][-3:], ["rm", "umount", "mount"])

    def test_cannot_consume_or_sync_marker_never_attempts_ram(self):
        for fault, status in (("consume", "marker-remove-failed"), ("sync", "marker-sync-failed")):
            with self.subTest(fault=fault):
                (self.root / "commands.jsonl").unlink(missing_ok=True)
                rows, _ = self.run_shell("ram", (fault,))
                self.assertEqual(rows[-1]["mode"], "baseline")
                self.assertEqual(rows[-1]["status"], status)
                self.assertNotIn("cp", [call[0] for call in self.commands()])
                self.assertEqual(self.squash_mounts()[-1][-2], str(self.image))
                self.assertEqual(self.marker.exists(), fault == "consume")

    def test_unknown_marker_is_consumed_and_uses_disk(self):
        rows, _ = self.run_shell("unexpected")
        self.assertEqual(rows[-1]["status"], "invalid-marker")
        self.assertEqual(rows[-1]["mode"], "baseline")
        self.assertFalse(self.marker.exists())
        self.assertEqual([call[0] for call in self.commands()], ["rm", "sync", "mount"])

    def test_symlink_marker_is_unlinked_without_following_it(self):
        target = self.root / "target"
        target.write_text("ram\n")
        self.marker.symlink_to(target)
        rows, _ = self.run_shell()
        self.assertEqual(rows[-1]["status"], "invalid-marker")
        self.assertEqual(rows[-1]["mode"], "baseline")
        self.assertFalse(self.marker.is_symlink())
        self.assertEqual(target.read_text(), "ram\n")

    def test_directory_marker_defaults_without_ram_work(self):
        self.marker.mkdir()
        rows, _ = self.run_shell()
        self.assertEqual(rows[-1]["status"], "marker-remove-failed")
        self.assertEqual(rows[-1]["mode"], "baseline")
        self.assertEqual([call[0] for call in self.commands()], ["rm", "mount"])

    def test_missing_image_preserves_bare_tree_behavior(self):
        self.image.unlink()
        rows, _ = self.run_shell("ram")
        self.assertEqual(rows[-1]["status"], "missing-image")
        self.assertEqual(rows[-1]["backing"], "directory")
        self.assertEqual([call[0] for call in self.commands()], ["rm", "sync"])

    def test_disk_mount_failure_keeps_production_diagnostic(self):
        rows, output = self.run_shell("baseline", ("disk-mount",))
        self.assertEqual(rows[-1]["status"], "disk-mount-failed")
        self.assertEqual(rows[-1]["backing"], "none")
        self.assertIn("no dynamic program can start", output)

    def test_failed_ram_and_disk_mount_are_reported_without_false_success(self):
        rows, output = self.run_shell("ram", ("ram-mount", "disk-mount", "umount"))
        self.assertEqual(rows[-1]["status"], "disk-mount-failed")
        self.assertEqual(rows[-1]["backing"], "none")
        self.assertIn("no dynamic program can start", output)

    def test_full_fixture_has_original_sampler_and_other_production_bytes(self):
        recovered = self.fixture.replace(builder.FIXTURE_BLOCK, builder.LIBRARY_BLOCK, 1)
        recovered = recovered.replace(self.sampler + builder.FINAL_LINE, builder.FINAL_LINE, 1)
        self.assertEqual(recovered, self.source)
        self.assertEqual(self.fixture.count(self.sampler), 1)
        for hook in ("native-health --accept-charger", "native-health --accept",
                     "baseos-update boot-check", "baseos-update apply"):
            self.assertIn(hook, self.fixture)
        syntax = subprocess.run(["/bin/sh", "-n"], input=self.fixture, text=True, capture_output=True)
        self.assertEqual(syntax.returncode, 0, syntax.stderr)

    def test_changed_or_duplicated_library_block_is_refused(self):
        for source in (self.source.replace("loop,ro,nodev,nosuid", "loop,ro", 1),
                       self.source.replace(builder.LIBRARY_BLOCK, builder.LIBRARY_BLOCK * 2, 1)):
            with self.assertRaisesRegex(ValueError, "unknown /usr/lib mount block"):
                builder.build_fixture(source, self.sampler)

    def test_existing_instrumentation_and_unknown_tail_are_refused(self):
        for source in (self.fixture, self.source + "echo unexpected\n", self.source + "# LAB: other\n"):
            with self.assertRaises(ValueError):
                builder.build_fixture(source, self.sampler)

    def test_sampler_is_read_without_executing_its_module(self):
        self.assertEqual(builder.sampler_from_source("raise RuntimeError('do not import')\nSAMPLER = 'same'"), "same")
        with self.assertRaisesRegex(ValueError, "one literal SAMPLER"):
            builder.sampler_from_source("SAMPLER = make_sampler()")

    def test_cli_writes_executable_fixture_and_refuses_overwrite(self):
        out = self.root / "rcS.fixture"
        command = [sys.executable, str(HERE / "build-fixture.py"), str(ROOT / "overlay/etc/init.d/rcS"), str(out)]
        result = subprocess.run(command, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(out.read_text(), self.fixture)
        self.assertEqual(out.stat().st_mode & 0o777, 0o755)
        report = json.loads(result.stdout)
        self.assertFalse(report["device_access"])
        self.assertEqual(report["modes"], ["baseline", "ram"])
        result = subprocess.run(command, text=True, capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(out.read_text(), self.fixture)


if __name__ == "__main__":
    unittest.main(verbosity=2)
