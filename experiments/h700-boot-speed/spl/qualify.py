#!/usr/bin/env python3
"""Check named host qualification reports without changing their artifacts.

The seal binds a checked snapshot of file hashes; hardware controls remain a
separate BROM-runner gate. verification.json is only a reset-control alias.
"""
import argparse
import hashlib
import json
import re
import struct
from pathlib import Path

CHECKER_PATH = Path(__file__).resolve()
CHECKER_SHA256 = hashlib.sha256(CHECKER_PATH.read_bytes()).hexdigest()
MISSING = object()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def seal(report):
    report.pop("seal_sha256", None)
    report["seal_sha256"] = hashlib.sha256(canonical(report)).hexdigest()
    return report


def field(value, name):
    for part in name.split("."):
        if not isinstance(value, dict) or part not in value:
            return MISSING
        value = value[part]
    return value


class Inputs:
    def __init__(self):
        self.hashes, self.errors = {}, []

    def error(self, label, name, expected, actual=MISSING):
        rendered = "<missing>" if actual is MISSING else json.dumps(actual, sort_keys=True)
        if len(rendered) > 300:
            rendered = rendered[:297] + "..."
        self.errors.append(f"{label}: {name}: expected {expected}; got {rendered}")

    def equal(self, label, data, name, expected):
        actual = field(data, name)
        if type(actual) is not type(expected) or actual != expected:
            self.error(label, name, json.dumps(expected), actual)

    def array(self, label, data, name):
        value = field(data, name)
        if not isinstance(value, list):
            self.error(label, name, "an array", value)
            return []
        return value

    def read(self, directory, name, group):
        label = f"{group}/{name}"
        try:
            data = (directory / name).read_bytes()
        except OSError as error:
            self.errors.append(f"{label}: cannot read: {error.strerror}")
            return None
        self.hashes[label] = {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}
        return data

    def report(self, directory, name, group="spl"):
        data = self.read(directory, name, group)
        if data is None:
            return None
        label = f"{group}/{name}"
        try:
            result = json.loads(data)
        except (ValueError, UnicodeError) as error:
            self.errors.append(f"{label}: invalid JSON: {error}")
            return None
        if not isinstance(result, dict):
            self.error(label, "document", "a JSON object", result)
            return None
        return result

    def binary(self, directory, name, manifest, digest_key, group="spl"):
        data = self.read(directory, name, group)
        if data is not None:
            self.equal(f"{group}/manifest.json", manifest, digest_key, hashlib.sha256(data).hexdigest())
        return data


def control(q, directory, name, digest, level, primary):
    data = q.report(directory, name)
    if data is None:
        return
    label = f"spl/{name}"
    for key, expected in {"spl_sha256": digest, "entry_el": level, "restore_primary": primary,
                          "sram_before_dram": True, "training_modeled": False, "reset": True,
                          "el3_registers_modeled": level == 3, "monitor_handoff": False,
                          "stage": 2, "errors": []}.items():
        q.equal(label, data, key, expected)
    writes = q.array(label, data, "writes")
    expected = [16 if primary else 0x56200, 65536]
    if expected not in writes:
        q.error(label, "writes", f"stock restore extent {expected}", writes)
    if not primary and [16, 65536] in writes:
        q.error(label, "writes", "no primary restore in scratch control", writes)


def selection(q, directory, manifest, digest):
    name = "selection-verification.json"
    data = q.report(directory, name)
    if data is None:
        return
    label = f"spl/{name}"
    q.equal(label, data, "spl_sha256", digest)
    q.equal(label, data, "native_aarch64", True)
    types = manifest.get("candidate_types", [])
    if not isinstance(types, list) or not types or any(type(kind) is not int or kind not in (3, 7, 8) for kind in types):
        q.error("spl/manifest.json", "candidate_types", "a nonempty array of supported types 3/7/8", types)
        return
    retries = q.array(label, data, "retry_cases")
    for kind in sorted(set(types) | {0}):
        for timeout in (False, True):
            matches = [case for case in retries if isinstance(case, dict) and
                       type(case.get("working_type")) is int and case["working_type"] == kind and
                       case.get("timeout_exit") is timeout]
            purpose = f"retry_cases[type={kind},timeout={timeout}]"
            if len(matches) != 1:
                q.error(label, purpose, "one result", len(matches))
                continue
            q.equal(f"{label} {purpose}", matches[0], "outcome", "selected" if kind else "failed-all")
            q.equal(f"{label} {purpose}", matches[0], "attempts", types.index(kind) + 1 if kind else len(types))
    timings = q.array(label, data, "timing_cases")
    actual = [(case.get("type"), case.get("clock_mhz")) for case in timings if isinstance(case, dict)]
    expected = [(kind, clock) for kind in (3, 7, 8) for clock in (672, 792)]
    if (len(actual) != len(timings) or any(type(kind) is not int or type(clock) is not int for kind, clock in actual)
            or sorted(actual) != expected):
        q.error(label, "timing_cases", f"exactly {expected}", actual)


def handoff(q, directory, digest, native):
    name = "verification-el3-handoff-restore.json"
    data = q.report(directory, name)
    if data is None:
        return
    expected = {"spl_sha256": digest, "entry_el": 3, "restore_primary": True,
                "el3_registers_modeled": True, "sram_before_dram": True,
                "monitor_handoff": True, "stage": 34, "errors": []}
    if native:
        expected.update({"cache_visibility.complete_set_way_cleans": 1,
                         "cache_visibility.dirty_ranges_after_cleanup": 0})
    for key, value in expected.items():
        q.equal(f"spl/{name}", data, key, value)
    return data


def abi2_pmic(q, directory, data, spl):
    if data is None:
        return
    label = "spl/verification-el3-handoff-restore.json"
    for key, expected in {"chip_address": 0x34, "register_mask": 0xff,
                          "address_bytes": 1, "data_bytes": 1,
                          "buffer_from_argument": 1, "modeled_status": 0}.items():
        q.equal(label, data, f"pmic_read_boundary.{key}", expected)
    q.equal(label, data, "pmic_bootreason_register_reads", [0x20, 0xf0])
    arguments = q.array(label, data, "pmic_bootreason_argument_words")
    if (len(arguments) != 2 or any(type(value) is not int or not 0 <= value <= 0xffffffff for value in arguments)
            or [value & 0xff for value in arguments] != [0x20, 0xf0]):
        q.error(label, "pmic_bootreason_argument_words", "two uint32 arguments whose low bytes are 0x20, 0xf0", arguments)
    raw_symbols = q.read(directory, "symbols.txt", "spl")
    if raw_symbols is None or spl is None:
        return
    try:
        symbols = {name: int(address, 16) for address, name in re.findall(
            r"^([0-9a-f]+) [a-zA-Z] (\w+)$", raw_symbols.decode(), re.M)}
    except UnicodeError:
        q.error("spl/symbols.txt", "encoding", "UTF-8 text")
        return
    start, target = symbols.get("pmic_bus_read"), symbols.get("i2c_read")
    if (start is None or target is None or start % 4 or target % 4 or
            not 0x20000 <= start <= 0x20000 + len(spl) - 24 or
            not 0x20000 <= target < 0x20000 + len(spl)):
        q.error("spl/symbols.txt", "pmic_bus_read/i2c_read", "aligned functions inside this SPL", symbols.get("pmic_bus_read"))
        return
    body = spl[start - 0x20000:start - 0x20000 + 24]
    q.equal(label, data, "pmic_read_boundary.function", hex(start))
    q.equal(label, data, "pmic_read_boundary.instruction_bytes_sha256", hashlib.sha256(body).hexdigest())
    expected_words = (0xaa0103e3, 0x52800024, 0x12001c01, 0x2a0403e2, 0x52800680,
                      0x14000000 | (((target - (start + 20)) >> 2) & 0x03ffffff))
    if struct.unpack("<6I", body) != expected_words:
        q.error(label, "pmic_read_boundary.actual_instructions",
                "buffer x1 to x3, byte register mask, chip0x34, one address/data byte, tailcall i2c_read", body.hex())
    q.equal(label, data, "pmic_read_boundary.instructions", [["mov", "x3, x1"], ["mov", "w4, #1"],
        ["and", "w1, w0, #0xff"], ["mov", "w2, w4"], ["mov", "w0, #0x34"], ["b", f"#{target:#x}"]])


def trace_reports(q, directory, digest):
    for name, pin, expected in (
            ("bl33-trace-verification.json", "spl_sha256", {"r0_r3_preserved": True, "monitor_repeat_safe": True}),
            ("monitor-trace-verification.json", "traced_spl_sha256", {"rtc_trace": [0x4a000000], "errors": []})):
        data = q.report(directory, name)
        if data is None:
            continue
        label = f"spl/{name}"
        q.equal(label, data, pin, digest)
        for key, value in expected.items():
            q.equal(label, data, key, value)
        if name == "monitor-trace-verification.json":
            for address in ("0x480000c0", "0x480000dc"):
                key = f"el3_translation_descriptors.{address}"
                descriptors = q.array(label, data, key)
                try:
                    executable = bool(descriptors) and not int(descriptors[-1], 16) & (1 << 54)
                except (ValueError, TypeError):
                    executable = False
                if not executable:
                    q.error(label, key, "a final executable trace mapping", descriptors)


def native_reports(q, directory, bundle, manifest, native, digest):
    if bundle is None:
        q.error("native", "directory", "--native-loader matching spl/manifest.json")
        return
    declared = q.report(bundle, "manifest.json", "native")
    if declared is None:
        return
    if declared != native:
        q.error("native/manifest.json", "document", "exact embedded native_loader manifest", "different manifest")
    for name in ("loader", "image", "firmware", "kernel", "monitor"):
        q.binary(bundle, f"{name}.bin", native, f"{name}_sha256", "native")
    loader = q.report(bundle, "verification.json", "native")
    cross = q.report(bundle, "verification-from-spl.json", "native")
    for name, data in (("verification.json", loader), ("verification-from-spl.json", cross)):
        if data is None:
            continue
        label = f"native/{name}"
        q.equal(label, data, "passed", True)
        if native.get("native_record_version") == 2:
            for key, expected in {"sram_release_writes": [{"address": "0x3000000", "value": "0x80000000"},
                                                           {"address": "0x3000004", "value": "0xa4ffffff"}],
                                  "sram_release_after_kernel_preparation": True,
                                  "display_register_accesses": 0,
                                  "sram_release_unrelated_bits_preserved": True}.items():
                q.equal(label, data, key, expected)
        for key in ("loader_sha256", "image_sha256", "kernel_sha256"):
            q.equal(label, data, key, native.get(key))
        if name == "verification.json":
            q.equal(label, data, "live_fdt_static_properties_exact", True)
        else:
            q.equal(label, data, "spl_sha256", digest)
            if "record_sha256" in data:
                record = q.read(directory, "native-record.bin", "spl")
                if record is not None:
                    q.equal(label, data, "record_sha256", hashlib.sha256(record).hexdigest())
        for flag in ("entry_checksum_witness", "kernel_exception_witness"):
            if native.get(flag):
                q.equal(label, data, flag + "_verified", True)
    monitor = q.report(bundle, "monitor-verification.json", "native")
    if monitor is not None:
        label = "native/monitor-verification.json"
        for key, expected in {"native_loader_sha256": native.get("loader_sha256"), "errors": [],
                              "package_sha256": manifest.get("handoff_package_sha256"),
                              "boot0_sha256": manifest.get("restore_source_sha256")}.items():
            q.equal(label, monitor, key, expected)
        eret = q.array(label, monitor, "eret")
        entry = native.get("kernel_smc_entry", 0x40080000)
        if type(entry) is not int:
            q.error("native/manifest.json", "kernel_smc_entry", "an integer", entry)
            entry = 0x40080000
        expected = {"entry": hex(entry), "spsr": "0x3c5",
                    "x0": "0x44000000", "x1": "0x0", "x2": "0x0", "x3": "0x0"}
        if not eret or eret[-1] != expected:
            q.error(label, "eret[-1]", json.dumps(expected), eret[-1] if eret else MISSING)
        if "kernel_smc_entry" in native:
            q.equal(label, monitor, "native_kernel_entry_r0_r3", ["0x44000000", "0x0", "0x0", "0x0"])
        if native.get("entry_checksum_witness"):
            trace = q.array(label, monitor, "rtc_trace")
            if not trace or trace[-1] != 0x42530033:
                q.error(label, "rtc_trace[-1]", "kernel/FDT witness 0x42530033", trace[-1] if trace else MISSING)
        if native.get("monitor_psci_probe"):
            q.equal(label, monitor, "psci_query_probe.passed", True)
    for flag, name, pins in (
            ("kernel_exception_witness", "kernel-idmap-verification.json", ("kernel_sha256",)),
            ("kernel_initcall_probe", "initcall-verification.json", ("kernel_sha256", "monitor_sha256"))):
        if native.get(flag):
            data = q.report(bundle, name, "native")
            if data is not None:
                q.equal(f"native/{name}", data, "passed", True)
                for key in pins:
                    q.equal(f"native/{name}", data, key, native.get(key))


def qualify(directory, bundle=None):
    q = Inputs()
    try:
        current_checker = hashlib.sha256(CHECKER_PATH.read_bytes()).hexdigest()
        if current_checker != CHECKER_SHA256:
            q.error("checker", "source_sha256",
                    f"imported source {CHECKER_SHA256}; restart after source changes", current_checker)
    except OSError as error:
        q.errors.append(f"checker: cannot read imported source: {error.strerror}")
    purpose = "restore-control"
    manifest = q.report(directory, "manifest.json")
    if manifest is not None:
        spl = q.binary(directory, "spl.bin", manifest, "spl_sha256")
        source = q.binary(directory, "restore-source.bin", manifest, "restore_source_sha256")
        digest = hashlib.sha256(spl).hexdigest() if spl is not None else manifest.get("spl_sha256")
        if spl is not None:
            q.equal("spl/manifest.json", manifest, "spl_bytes", len(spl))
        if source is not None and len(source) != 65536:
            q.error("spl/restore-source.bin", "bytes", "65536", len(source))
        for name, level, primary in (("verification-el1-control.json", 1, False),
                                     ("verification-el1-control-restore.json", 1, True),
                                     ("verification-el3-control-restore.json", 3, True)):
            control(q, directory, name, digest, level, primary)
        # This alias serves operation2 builders. Its purpose is never handoff.
        alias = q.report(directory, "verification.json")
        if alias is not None:
            for key, expected in {"spl_sha256": digest, "reset": True, "stage": 2,
                                  "monitor_handoff": False, "errors": []}.items():
                q.equal("spl/verification.json", alias, key, expected)
        entry = q.report(directory, "brom-entry-verification.json")
        if entry is not None:
            label = "spl/brom-entry-verification.json"
            q.equal(label, entry, "spl_sha256", digest)
            q.equal(label, entry, "egon_checksum_valid", True)
            cases = q.array(label, entry, "cases")
            revisions = [case.get("revision") for case in cases if isinstance(case, dict)]
            if len(revisions) != 2 or any(type(value) is not int for value in revisions) or len(set(revisions)) != 2:
                q.error(label, "cases.revision", "two distinct numeric input cases", revisions)
            destinations = []
            for index, case in enumerate(cases):
                q.equal(f"{label} cases[{index}]", case, "rmr_reset_value", 3)
                writes = q.array(label, case, "rvbar_writes")
                if (len(writes) != 1 or not isinstance(writes[0], list) or len(writes[0]) != 2 or
                        any(type(value) is not int for value in writes[0]) or writes[0][1] != 0x20060):
                    q.error(label, f"cases[{index}].rvbar_writes", "one RVBAR register write of 0x20060", writes)
                else:
                    destinations.append(writes[0][0])
            if sorted(destinations) != [0x08100040, 0x09010040]:
                q.error(label, "cases.rvbar_writes", "both real RVBAR destinations 0x08100040 and 0x09010040", destinations)
        if manifest.get("native_dram"):
            selection(q, directory, manifest, digest)
        native = manifest.get("native_loader")
        if native or manifest.get("rtc_handoff_trace"):
            purpose = "native-handoff" if native else "traced-vendor-handoff"
            handoff_proof = handoff(q, directory, digest, bool(native))
            q.binary(directory, "handoff-package.bin", manifest, "handoff_package_sha256")
        if manifest.get("rtc_handoff_trace"):
            trace_reports(q, directory, digest)
        if native:
            if not isinstance(native, dict):
                q.error("spl/manifest.json", "native_loader", "an object", native)
            else:
                version = native.get("native_record_version", 1)
                if type(version) is not int or version not in (1, 2):
                    q.error("native/manifest.json", "native_record_version", "ABI1 or ABI2 integer", version)
                else:
                    size = native.get("native_record_bytes", 232)
                    expected_size = {1: 232, 2: 240}[version]
                    if type(size) is not int or size != expected_size:
                        q.error("native/manifest.json", "native_record_bytes", f"{expected_size} for ABI{version}", size)
                if version == 2:
                    abi2_pmic(q, directory, handoff_proof, spl)
                pll = q.report(directory, "vendor-pll-verification.json")
                if pll is not None:
                    for key, expected in {"passed": True, "spl_sha256": digest,
                                          "boot0_sha256": manifest.get("restore_source_sha256"),
                                          "other_bits_and_entire_ccu_page_preserved": True,
                                          "writes_per_case": 9}.items():
                        q.equal("spl/vendor-pll-verification.json", pll, key, expected)
                    if type(pll.get("cases")) is not int or pll["cases"] <= 0:
                        q.error("spl/vendor-pll-verification.json", "cases", "a positive case count", field(pll, "cases"))
                cache = q.report(directory, "cache-verification.json")
                if cache is not None:
                    label = "spl/cache-verification.json"
                    q.equal(label, cache, "passed", True)
                    q.equal(label, cache, "spl_sha256", digest)
                    cases = q.array(label, cache, "cases")
                    for mutated, rejected, cleans in ((False, False, 1), (True, True, 0)):
                        if not any(isinstance(case, dict) and case.get("cleanup_call_nopped") is mutated and
                                   case.get("poc_rejected") is rejected and
                                   type(case.get("complete_clean_sweeps")) is int and
                                   case.get("complete_clean_sweeps") == cleans for case in cases):
                            q.error(label, "cases", f"cleanup mutation={mutated}, rejection={rejected}, cleans={cleans}", cases)
                native_reports(q, directory, bundle, manifest, native, digest)
        elif bundle is not None:
            q.error("spl/manifest.json", "native_loader", "native identity when --native-loader is supplied")
    report = {"schema": "baseos-spl-qualification-v1", "passed": not q.errors,
              "purpose": purpose,
              "checker_sha256": CHECKER_SHA256,
              "inputs": q.hashes, "errors": q.errors,
              "scope": "Host artifact/report qualification; hardware controls remain separate."}
    return seal(report)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--native-loader", type=Path)
    parser.add_argument("--output", type=Path, help="create a new report; existing files are never overwritten")
    args = parser.parse_args()
    report = qualify(args.directory, args.native_loader)
    if args.output:
        try:
            with args.output.open("x") as stream:
                stream.write(json.dumps(report, indent=2) + "\n")
        except OSError as error:
            report["errors"].append(f"output: cannot create {args.output}: {error.strerror}")
            report["passed"] = False
            seal(report)
    print(json.dumps(report, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
