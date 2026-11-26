#!/usr/bin/env python3
"""Build a purpose-bound, nondeployable outer protocol control with an old inner."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]


def build(inner, stock, native_manifest_path, output, purpose):
    if native_manifest_path.is_dir():
        native_manifest_path /= "manifest.json"
    assert purpose.strip() and not output.exists()
    spec = importlib.util.spec_from_file_location("capsule_control_builder", HERE / "build.py")
    capsule = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(capsule)
    native = json.loads(native_manifest_path.read_text())
    pins = {str((HERE / name).relative_to(ROOT)): capsule.sha((HERE / name).read_bytes())
            for name in (*capsule.SOURCES, Path(__file__).name)}
    pins["src/native-attempt.h"] = capsule.sha((ROOT / "src/native-attempt.h").read_bytes())
    derivation = {"version": 1, "purpose": purpose, "diagnostic": True,
                  "base_native_manifest_sha256": capsule.sha(native_manifest_path.read_bytes()),
                  "base_native_generation": native["generation"],
                  "inner_sha256": capsule.sha((inner / "spl.bin").read_bytes()),
                  "inner_manifest_sha256": capsule.sha((inner / "manifest.json").read_bytes()),
                  "stock_sha256": capsule.sha(stock.read_bytes()), "source_pins": pins,
                  "protocol": "outer full commit; old inner pre-own-arm witness; no Linux/native adoption"}
    generation = hashlib.sha256(json.dumps(derivation, sort_keys=True).encode()).hexdigest()
    assert capsule.fnv(bytes.fromhex(generation))
    native["diagnostic_derivation"] = derivation
    native["generation"] = generation
    native["durable_install_eligible"] = False
    derived = output.parent / (output.name + "-diagnostic-generation.json")
    assert not derived.exists()
    derived.parent.mkdir(parents=True, exist_ok=True)
    derived.write_text(json.dumps(native, indent=2) + "\n")
    return capsule.build(inner, stock, derived, output, diagnostic=True, purpose=purpose)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inner", type=Path)
    parser.add_argument("stock", type=Path)
    parser.add_argument("native_manifest", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--purpose", required=True)
    args = parser.parse_args()
    build(args.inner.resolve(), args.stock.resolve(), args.native_manifest.resolve(), args.output.resolve(), args.purpose)
