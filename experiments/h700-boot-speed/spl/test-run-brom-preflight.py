#!/usr/bin/env python3
"""Exercise the runner's qualification boundary with real artifacts, no device.

Usage: python test-run-brom-preflight.py SPL_DIR --native-loader NATIVE_DIR [-v]
"""
import argparse
import importlib.util
import json
from pathlib import Path
import shutil
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import qualify

SOURCE_SPL = SOURCE_NATIVE = None


class DeviceBoundary(Exception):
    pass


class LabBoundary:
    def __init__(self):
        self.accesses = 0

    @staticmethod
    def require(condition, message):
        if not condition:
            raise RuntimeError(message)

    def identity(self):
        self.accesses += 1
        raise DeviceBoundary()


class RunnerPreflight(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.output = self.root / "run"
        self.spl = self.root / "spl"
        self.spl.mkdir()
        for path in SOURCE_SPL.iterdir():
            if path.is_file():
                if path.suffix == ".json":
                    shutil.copyfile(path, self.spl / path.name)
                else:
                    (self.spl / path.name).symlink_to(path.resolve())
        self.lab = LabBoundary()
        stubs = {
            "oneshot": types.SimpleNamespace(lab=self.lab),
            "rtc_trace": types.ModuleType("rtc_trace"),
            "boot_observer": types.ModuleType("boot_observer"),
            "lab_devices": types.SimpleNamespace(PROFILES={}),
        }
        with patch.dict(sys.modules, stubs):
            spec = importlib.util.spec_from_file_location("brom_preflight_test", HERE / "run-brom-control.py")
            self.runner = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(self.runner)

    def invoke(self, native=True):
        self.runner.run(self.spl, self.root / "unused-warm", self.output,
                        handoff=native, native_loader=SOURCE_NATIVE if native else None)

    def saved(self):
        return json.loads((self.output / "spl-qualification.json").read_text())

    def test_valid_report_is_saved_before_device_boundary(self):
        with self.assertRaises(DeviceBoundary):
            self.invoke()
        self.assertEqual(self.lab.accesses, 1)
        self.assertEqual(self.saved(), qualify.qualify(self.spl, SOURCE_NATIVE))
        self.assertTrue(self.saved()["passed"])

    def test_stage2_substitution_stops_before_device_access(self):
        shutil.copyfile(self.spl / "verification-el3-control-restore.json",
                        self.spl / "verification-el3-handoff-restore.json")
        with self.assertRaisesRegex(RuntimeError, "verification-el3-handoff-restore.json: stage"):
            self.invoke()
        self.assertEqual(self.lab.accesses, 0)
        self.assertFalse(self.saved()["passed"])
        self.assertEqual(list(self.output.iterdir()), [self.output / "spl-qualification.json"])

    def test_missing_report_stops_before_device_access(self):
        (self.spl / "verification-el1-control.json").unlink()
        with self.assertRaisesRegex(RuntimeError, "verification-el1-control.json: cannot read"):
            self.invoke()
        self.assertEqual(self.lab.accesses, 0)
        self.assertFalse(self.saved()["passed"])

    def test_existing_output_is_never_overwritten(self):
        self.output.mkdir()
        sentinel = self.output / "spl-qualification.json"
        sentinel.write_text("retain existing evidence")
        with self.assertRaisesRegex(RuntimeError, "fresh evidence"):
            self.invoke()
        self.assertEqual(sentinel.read_text(), "retain existing evidence")
        self.assertEqual(self.lab.accesses, 0)

    def test_non_native_control_skips_qualification(self):
        with patch.object(qualify, "qualify", side_effect=AssertionError("unexpected native gate")):
            with self.assertRaises(DeviceBoundary):
                self.invoke(native=False)
        self.assertEqual(self.lab.accesses, 1)
        self.assertFalse(self.output.exists())


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("spl", type=Path)
    parser.add_argument("--native-loader", type=Path, required=True)
    args, remaining = parser.parse_known_args()
    SOURCE_SPL, SOURCE_NATIVE = args.spl.resolve(), args.native_loader.resolve()
    unittest.main(argv=[sys.argv[0], *remaining])
