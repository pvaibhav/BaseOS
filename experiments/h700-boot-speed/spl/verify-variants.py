#!/usr/bin/env python3
"""Build and execute the SRAM selector against every prepared model's parameters."""
import argparse
import contextlib
import importlib.util
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def verify(archive, output):
    if output.exists():
        raise SystemExit("choose a fresh output directory")
    output.mkdir(parents=True)
    builder = module("variant_builder", HERE / "build-probe.py")
    selector = module("variant_selector", HERE / "verify-dram-select.py")
    protocol = module("variant_protocol", HERE / "verify-probe.py")
    contract = module("variant_contract", HERE.parent / "variant-survey/compatibility.py")
    summary = {"scope": "Build and instruction-level tests; DRAM training and geometry are modeled.", "targets": {}}
    for target in contract.TARGETS:
        prefix = (ROOT / "work" / target / "boot-prefix.img").read_bytes()
        header = output / (target + "-boot0.bin")
        header.write_bytes(prefix[8192:8192 + 65536])
        directory = output / target
        with (output / (target + ".log")).open("w") as log, contextlib.redirect_stdout(log):
            builder.build(archive, directory, header, autodetect=True)
            selector.verify(directory)
            protocol.verify(directory)
        manifest = json.loads((directory / "manifest.json").read_text())
        selection = json.loads((directory / "selection-verification.json").read_text())
        summary["targets"][target] = {"probe_sha256": manifest["probe_sha256"],
            "probe_bytes": manifest["probe_bytes"], "candidate_types": manifest["candidate_types"],
            "retry_cases": len(selection["retry_cases"]), "timing_cases": len(selection["timing_cases"]),
            "protocol_cases": 4}
        print(target, "passed", manifest["candidate_types"], flush=True)
        (output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    verify(args.archive, args.output)
