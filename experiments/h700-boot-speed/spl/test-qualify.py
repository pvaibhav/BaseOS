#!/usr/bin/env python3
"""Mutate disposable copies of real qualification inputs and run the CLI.

Usage: python test-qualify.py SPL_DIR --native-loader NATIVE_DIR [-v]
No verifier, toolchain, device or firmware writer is invoked.
"""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest

CHECKER = Path(__file__).with_name("qualify.py")
SOURCE_SPL = SOURCE_NATIVE = None


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class QualificationBehavior(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.spl, self.native = self.root / "spl", self.root / "native"
        for source, target in ((SOURCE_SPL, self.spl), (SOURCE_NATIVE, self.native)):
            target.mkdir()
            for path in source.iterdir():
                if not path.is_file():
                    continue
                if path.suffix == ".json":
                    shutil.copyfile(path, target / path.name)
                elif path.name in ("spl.bin", "restore-source.bin", "handoff-package.bin",
                                   "native-record.bin", "symbols.txt",
                                   "loader.bin", "image.bin", "firmware.bin", "kernel.bin", "monitor.bin"):
                    (target / path.name).symlink_to(path.resolve())

    def run_checker(self, expected=0, *, bundle=True, output=None):
        args = [sys.executable, str(CHECKER), str(self.spl)]
        if bundle:
            args += ["--native-loader", str(self.native)]
        if output is not None:
            args += ["--output", str(output)]
        process = subprocess.run(args, capture_output=True, text=True)
        self.assertEqual(process.returncode, expected, process.stderr + process.stdout)
        self.assertEqual(process.stderr, "")
        report = json.loads(process.stdout)
        digest = report.pop("seal_sha256")
        encoded = json.dumps(report, sort_keys=True, separators=(",", ":")).encode()
        self.assertEqual(digest, hashlib.sha256(encoded).hexdigest())
        report["seal_sha256"] = digest
        self.assertEqual(report["passed"], expected == 0)
        return report

    def change(self, directory, name, **values):
        path = directory / name
        document = json.loads(path.read_text())
        document.update(values)
        path.write_text(json.dumps(document))

    def rejected(self, name, field):
        report = self.run_checker(1)
        self.assertTrue(any(name in error and field in error for error in report["errors"]), report["errors"])

    def test_real_inputs_are_read_only_and_sealed(self):
        before = {str(path): sha(path) for directory in (self.spl, self.native) for path in directory.iterdir()}
        report = self.run_checker()
        self.assertEqual(report["errors"], [])
        self.assertEqual(report["checker_sha256"], sha(CHECKER))
        self.assertEqual(report, self.run_checker())
        for label, pin in report["inputs"].items():
            directory, name = label.split("/", 1)
            path = (self.spl if directory == "spl" else self.native) / name
            self.assertEqual(pin, {"sha256": sha(path), "bytes": path.stat().st_size})
        self.assertEqual(before, {str(path): sha(path) for directory in (self.spl, self.native) for path in directory.iterdir()})

    def test_imported_checker_cannot_claim_changed_source(self):
        source = self.root / "copied-checker.py"
        original = CHECKER.read_bytes()
        source.write_bytes(original)
        spec = importlib.util.spec_from_file_location("copied_checker", source)
        checker = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(checker)
        self.assertTrue(checker.qualify(self.spl, self.native)["passed"])
        source.write_bytes(original + b"\n# changed after import\n")
        report = checker.qualify(self.spl, self.native)
        self.assertFalse(report["passed"])
        self.assertEqual(report["checker_sha256"], hashlib.sha256(original).hexdigest())
        self.assertNotEqual(report["checker_sha256"], sha(source))
        self.assertTrue(any("checker: source_sha256" in error and "restart" in error
                            for error in report["errors"]), report["errors"])

    def test_control_alias_is_not_a_handoff_report(self):
        alias = json.loads((self.spl / "verification.json").read_text())
        self.assertTrue(alias["reset"])
        self.assertEqual(alias["stage"], 2)
        self.run_checker()
        shutil.copyfile(self.spl / "verification-el3-control-restore.json",
                        self.spl / "verification-el3-handoff-restore.json")
        report = self.run_checker(1)
        for field in ("monitor_handoff", "stage", "cache_visibility.complete_set_way_cleans"):
            self.assertTrue(any("handoff-restore.json" in error and field in error for error in report["errors"]), report["errors"])

    def test_missing_named_reports_are_not_replaced_by_alias(self):
        required = [(self.spl, name) for name in (
            "verification-el1-control.json", "verification-el1-control-restore.json",
            "verification-el3-control-restore.json", "verification-el3-handoff-restore.json",
            "brom-entry-verification.json", "selection-verification.json", "vendor-pll-verification.json",
            "cache-verification.json")]
        required += [(self.native, name) for name in (
            "verification.json", "verification-from-spl.json", "monitor-verification.json")]
        native = json.loads((self.native / "manifest.json").read_text())
        for flag, name in (("kernel_exception_witness", "kernel-idmap-verification.json"),
                           ("kernel_initcall_probe", "initcall-verification.json")):
            if native.get(flag):
                required.append((self.native, name))
        for directory, name in required:
            with self.subTest(name=name):
                path = directory / name
                original = path.read_bytes()
                path.unlink()
                try:
                    self.rejected(name, "cannot read")
                finally:
                    path.write_bytes(original)

    def test_report_identity_mismatches(self):
        cases = [(self.spl, name, "spl_sha256") for name in (
            "verification-el1-control.json", "verification-el1-control-restore.json",
            "verification-el3-control-restore.json", "verification-el3-handoff-restore.json",
            "brom-entry-verification.json", "selection-verification.json", "vendor-pll-verification.json",
            "cache-verification.json")]
        cases += [(self.native, "verification.json", "loader_sha256"),
                  (self.native, "verification-from-spl.json", "spl_sha256"),
                  (self.native, "monitor-verification.json", "native_loader_sha256")]
        native = json.loads((self.native / "manifest.json").read_text())
        for flag, name in (("kernel_exception_witness", "kernel-idmap-verification.json"),
                           ("kernel_initcall_probe", "initcall-verification.json")):
            if native.get(flag):
                cases.append((self.native, name, "kernel_sha256"))
        for directory, name, key in cases:
            with self.subTest(name=name):
                path = directory / name
                original = path.read_bytes()
                try:
                    self.change(directory, name, **{key: "0" * 64})
                    self.rejected(name, key)
                finally:
                    path.write_bytes(original)

    def test_primary_control_cannot_be_el1_or_training(self):
        name = "verification-el3-control-restore.json"
        self.change(self.spl, name, entry_el=1, training_modeled=True)
        self.rejected(name, "entry_el")
        self.rejected(name, "training_modeled")

    def test_scratch_cannot_restore_primary(self):
        self.change(self.spl, "verification-el1-control.json", restore_primary=True, writes=[[16, 65536]])
        self.rejected("verification-el1-control.json", "writes")

    def test_handoff_needs_cache_publication(self):
        self.change(self.spl, "verification-el3-handoff-restore.json",
                    cache_visibility={"complete_set_way_cleans": 0, "dirty_ranges_after_cleanup": 1})
        self.rejected("handoff-restore.json", "complete_set_way_cleans")
        self.rejected("handoff-restore.json", "dirty_ranges_after_cleanup")

    def require_abi2(self):
        if json.loads((self.native / "manifest.json").read_text()).get("native_record_version") != 2:
            self.skipTest("ABI2 contract; actual ABI1 positives retain their original requirements")

    def test_abi2_sram_contract_is_required_in_both_loader_reports(self):
        self.require_abi2()
        wrong = {"sram_release_writes": [], "sram_release_after_kernel_preparation": False,
                 "display_register_accesses": 6, "sram_release_unrelated_bits_preserved": False}
        for name in ("verification.json", "verification-from-spl.json"):
            path = self.native / name
            original = path.read_bytes()
            for key, value in wrong.items():
                for missing in (False, True):
                    with self.subTest(name=name, key=key, missing=missing):
                        data = json.loads(original)
                        if missing: data.pop(key)
                        else: data[key] = value
                        path.write_text(json.dumps(data))
                        self.rejected(name, key)
            path.write_bytes(original)

    def test_abi2_sram_writes_must_preserve_values_order_and_extent(self):
        self.require_abi2()
        path = self.native / "verification.json"
        original = path.read_bytes()
        mutations = [lambda rows: rows.reverse(),
                     lambda rows: rows[0].update(value="0x0"),
                     lambda rows: rows[1].update(address="0x1008000"),
                     lambda rows: rows.append({"address": "0x3001600", "value": "0x1"})]
        for mutate in mutations:
            data = json.loads(original)
            mutate(data["sram_release_writes"])
            path.write_text(json.dumps(data))
            self.rejected("verification.json", "sram_release_writes")

    def test_abi2_pmic_contract_fields_cannot_be_missing_or_wrong(self):
        self.require_abi2()
        name = "verification-el3-handoff-restore.json"
        path = self.spl / name
        original = path.read_bytes()
        wrong = {"function": "0x0", "instruction_bytes_sha256": "0" * 64, "instructions": [],
                 "chip_address": 0x35, "register_mask": 0x7f, "address_bytes": 2,
                 "data_bytes": 2, "buffer_from_argument": 0, "modeled_status": -1}
        for key, value in wrong.items():
            for missing in (False, True):
                with self.subTest(key=key, missing=missing):
                    data = json.loads(original)
                    if missing: data["pmic_read_boundary"].pop(key)
                    else: data["pmic_read_boundary"][key] = value
                    path.write_text(json.dumps(data))
                    self.rejected(name, "pmic_read_boundary." + key)
        for key in ("pmic_read_boundary", "pmic_bootreason_register_reads", "pmic_bootreason_argument_words"):
            data = json.loads(original)
            data.pop(key)
            path.write_text(json.dumps(data))
            self.rejected(name, key)
        for key in ("pmic_bootreason_register_reads", "pmic_bootreason_argument_words"):
            data = json.loads(original)
            data[key] = [0x20, 0xf1]
            path.write_text(json.dumps(data))
            self.rejected(name, key)

    def test_abi2_pmic_claims_bind_actual_mask_instruction(self):
        self.require_abi2()
        symbols = {name: int(address, 16) for address, name in re.findall(
            r"^([0-9a-f]+) [a-zA-Z] (\w+)$", (self.spl / "symbols.txt").read_text(), re.M)}
        offset = symbols["pmic_bus_read"] - 0x20000
        path = self.spl / "spl.bin"
        blob = bytearray(path.read_bytes())
        struct.pack_into("<I", blob, offset + 8, 0x12001801)  # AND register with0x7f.
        path.unlink()
        path.write_bytes(blob)
        name = "verification-el3-handoff-restore.json"
        report = json.loads((self.spl / name).read_text())
        report["pmic_read_boundary"]["instruction_bytes_sha256"] = hashlib.sha256(blob[offset:offset + 24]).hexdigest()
        (self.spl / name).write_text(json.dumps(report))
        self.rejected(name, "pmic_read_boundary.actual_instructions")

    def test_abi2_metadata_types_cannot_skip_contract_checks(self):
        self.require_abi2()
        native_path, spl_path = self.native / "manifest.json", self.spl / "manifest.json"
        native_original, spl_original = native_path.read_bytes(), spl_path.read_bytes()
        for value in ("2", 2.0, True):
            data, manifest = json.loads(native_original), json.loads(spl_original)
            data["native_record_version"] = value
            manifest["native_loader"] = data
            native_path.write_text(json.dumps(data))
            spl_path.write_text(json.dumps(manifest))
            self.rejected("native/manifest.json", "native_record_version")
        data, manifest = json.loads(native_original), json.loads(spl_original)
        data["native_record_bytes"] = 232
        manifest["native_loader"] = data
        native_path.write_text(json.dumps(data))
        spl_path.write_text(json.dumps(manifest))
        self.rejected("native/manifest.json", "native_record_bytes")
        for missing in (False, True):
            data, manifest = json.loads(native_original), json.loads(spl_original)
            if missing: data.pop("native_record_version")
            else: data["native_record_version"] = 1
            manifest["native_loader"] = data
            native_path.write_text(json.dumps(data))
            spl_path.write_text(json.dumps(manifest))
            self.rejected("native/manifest.json", "native_record_bytes")

    def test_cache_mutation_needs_both_control_and_failure(self):
        name = "cache-verification.json"
        original = (self.spl / name).read_bytes()
        for remaining in (0, 1):
            with self.subTest(remaining=remaining):
                data = json.loads(original)
                data["cases"] = [data["cases"][remaining]]
                (self.spl / name).write_text(json.dumps(data))
                self.rejected(name, "cases")

    def test_selection_needs_timeout_failure_and_all_timings(self):
        name = "selection-verification.json"
        data = json.loads((self.spl / name).read_text())
        data["retry_cases"] = [case for case in data["retry_cases"] if case["working_type"]]
        data["timing_cases"].pop()
        (self.spl / name).write_text(json.dumps(data))
        self.rejected(name, "retry_cases")
        self.rejected(name, "timing_cases")

    def test_brom_needs_two_distinct_revisions(self):
        name = "brom-entry-verification.json"
        data = json.loads((self.spl / name).read_text())
        data["cases"] = [data["cases"][0]] * 2
        (self.spl / name).write_text(json.dumps(data))
        self.rejected(name, "cases.revision")

    def test_brom_stimulus_labels_do_not_replace_actual_branch_outputs(self):
        name = "brom-entry-verification.json"
        data = json.loads((self.spl / name).read_text())
        data["cases"][0]["revision"], data["cases"][1]["revision"] = 0, 2
        (self.spl / name).write_text(json.dumps(data))
        self.run_checker()
        data["cases"][1]["rvbar_writes"] = data["cases"][0]["rvbar_writes"]
        (self.spl / name).write_text(json.dumps(data))
        self.rejected(name, "cases.rvbar_writes")

    def test_monitor_must_reach_exact_kernel_abi(self):
        name = "monitor-verification.json"
        data = json.loads((self.native / name).read_text())
        data["eret"][-1]["x0"] = "0x0"
        (self.native / name).write_text(json.dumps(data))
        self.rejected(name, "eret[-1]")

    def test_actual_payload_identity_is_checked(self):
        path = self.native / "loader.bin"
        data = bytearray(path.read_bytes())
        data[-1] ^= 1
        path.unlink()  # Never write through a fixture symlink into an artifact.
        path.write_bytes(data)
        self.rejected("native/manifest.json", "loader_sha256")

    def test_malformed_reports_emit_precise_sealed_errors(self):
        name = "selection-verification.json"
        (self.spl / name).write_text("{")
        self.rejected(name, "invalid JSON")
        shutil.copyfile(SOURCE_SPL / name, self.spl / name)
        self.change(self.spl, name, timing_cases=[{"type": "bad", "clock_mhz": None}])
        self.rejected(name, "timing_cases")

    def test_missing_native_argument_is_an_error(self):
        report = self.run_checker(1, bundle=False)
        self.assertTrue(any("--native-loader" in error for error in report["errors"]))

    def test_restore_control_does_not_claim_unused_handoff_metadata(self):
        path = self.spl / "manifest.json"
        manifest = json.loads(path.read_text())
        manifest.pop("native_loader")
        path.write_text(json.dumps(manifest))
        (self.spl / "verification-el3-handoff-restore.json").unlink()
        (self.spl / "handoff-package.bin").unlink()
        report = self.run_checker(bundle=False)
        self.assertEqual(report["purpose"], "restore-control")
        self.assertNotIn("spl/verification-el3-handoff-restore.json", report["inputs"])

    def test_output_cannot_overwrite_named_report(self):
        target = self.spl / "verification-el3-handoff-restore.json"
        before = target.read_bytes()
        report = self.run_checker(1, output=target)
        self.assertTrue(any("output: cannot create" in error for error in report["errors"]))
        self.assertEqual(target.read_bytes(), before)

    def test_new_output_matches_stdout(self):
        target = self.root / "qualification.json"
        report = self.run_checker(output=target)
        self.assertEqual(json.loads(target.read_text()), report)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--native-loader", required=True, type=Path)
    args, remaining = parser.parse_known_args()
    SOURCE_SPL, SOURCE_NATIVE = args.directory.resolve(), args.native_loader.resolve()
    baseline = subprocess.run([sys.executable, str(CHECKER), str(SOURCE_SPL), "--native-loader", str(SOURCE_NATIVE)],
                              capture_output=True, text=True)
    if baseline.returncode:
        raise SystemExit("positive artifacts are not qualified:\n" + baseline.stdout + baseline.stderr)
    unittest.main(argv=[sys.argv[0], *remaining])
