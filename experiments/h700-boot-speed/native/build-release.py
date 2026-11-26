#!/usr/bin/env python3
"""Build one model's native boot files for a BaseOS release, with no device access.

Everything comes from the repository and the model's prepared inputs
(work/<target>/boot-prefix.img and boot-gzip/ from tools/kernel_gzip.py):
the capsule embeds the untouched stock boot0 from the prepared prefix, so the
result belongs to the model, not to one card. DRAM and panel are still chosen
on every boot by the SPL and loader.

Usage: build-release.py TARGET OUT
OUT/release/ holds what goes on the card: capsule.bin (sectors 16..271),
firmware.bin (p4 + 40 MiB), image.bin (p4 + 48 MiB), recovery-toc.bin (sector
32800), generation.bin (raw 32 bytes) and manifest (shell assignments).
Needs clang, the rustup ld.lld, capstone and the running baseos-h700-mainline
container for the SPL.
"""
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
SPL = HERE.parent / "spl"
CONTAINER = "baseos-h700-mainline"
ARCHIVE = ROOT / "work/h700-loader/mainline/u-boot-v2026.10.tar.gz"
CAPSULE_SECTOR, TOC_SECTOR, TOC_BYTES = 16, 32800, 1310720
FIRMWARE_OFFSET, IMAGE_OFFSET, PARTITION_END = 40 << 20, 48 << 20, 64 << 20
# Recovery TOCs that the per-card 1.5.0 installs left on the two lab devices.
# They boot the same 1.4.0 gzip image at p4+0; the 1.5.1 TOC replaces them.
PREVIOUS_TOCS = {
    "rg34xxsp": ["2bab1b40b7c8cbfb359c060c98ca84055886207979693ca8badf3d9236e452c1"],
    "rg40xxv": ["9daec3869e132106ede269335dbbe605ceefdba5704b5d29171cf58e265fe4f2"],
}


def sha(blob):
    return hashlib.sha256(blob).hexdigest()


def fnv(blob):
    value = 2166136261
    for byte in blob:
        value = (value ^ byte) * 16777619 & 0xffffffff
    return value


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


def inventory(target, out):
    fleet = module("release_fleet", HERE / "verify-fleet.py")
    contract = json.loads((HERE.parent / "evidence/compatibility.json").read_text())["targets"][target]
    provenance, data = fleet.prepared_inputs(target, contract)
    out.mkdir(parents=True)
    for name, blob in data.items():
        (out / name).write_bytes(blob)
    (out / "inventory.json").write_text(json.dumps({"target": target, "kind": "prepared-source", **provenance}, indent=2) + "\n")
    return out


def spl(target, inv, native, parent):
    parent.mkdir()
    os.link(ARCHIVE, parent / ARCHIVE.name)
    builder = module("release_spl", SPL / "build-early-mmc.py")
    builder.PARENT = parent.resolve()
    output = parent / "spl"
    remote = "/tmp/baseos-release-spl-" + target
    original = subprocess.run
    state = {"copied": False}

    def run(command, **kwargs):
        if isinstance(command, list) and command[:3] == ["docker", "exec", CONTAINER]:
            if not state["copied"]:
                original(["docker", "exec", CONTAINER, "rm", "-rf", remote], check=True)
                original(["docker", "cp", str(output), CONTAINER + ":" + remote], check=True)
                state["copied"] = True
            command = [arg.replace("/work/" + output.name, remote) if isinstance(arg, str) else arg for arg in command]
            result = original(command, **kwargs)
            if "/bin/sh" in command:
                original(["docker", "cp", CONTAINER + ":" + remote + "/.", str(output)], check=True)
            return result
        return original(command, **kwargs)

    builder.subprocess.run = run
    package = ROOT / "work" / target / "boot-gzip/boot-package.bin"
    builder.main(output, inv / "boot0.bin", True, package, False, native, raw_capsule=True)
    original(["docker", "exec", CONTAINER, "rm", "-rf", remote], check=True)
    return output


def main():
    if len(sys.argv) not in (3, 4) or (len(sys.argv) == 4 and sys.argv[3] != "--package-only"):
        raise SystemExit(__doc__)
    target, out = sys.argv[1], Path(sys.argv[2]).resolve()
    if len(sys.argv) == 3:
        assert not out.exists(), "use a fresh output directory"
        out.mkdir(parents=True)
        inv = inventory(target, out / "inventory")
        native = out / "native"
        module("release_native", HERE / "build.py").build(target, inv, native, raw_capsule=True)
        inner = spl(target, inv, native, out / "mainline")
        module("release_capsule", HERE / "capsule/build.py").build(inner, inv / "boot0.bin", native, out / "capsule")
    package(target, out)


def package(target, out):
    """Collect the card files and their manifest from a finished build."""
    inv, native = out / "inventory", out / "native"
    n = json.loads((native / "manifest.json").read_text())
    c = json.loads((out / "capsule/manifest.json").read_text())
    capsule = (out / "capsule/capsule.egon.bin").read_bytes()
    firmware, image = (native / "firmware.bin").read_bytes(), (native / "image.bin").read_bytes()
    stock = (inv / "boot0.bin").read_bytes()
    generation = bytes.fromhex(n["generation"])
    assert n["target"] == c["target"] == target and c["generation"] == n["generation"]
    assert not n["native_diagnostic"] and not c.get("diagnostic") and c["inner_adoption_implemented"]
    assert len(capsule) == 131072 and capsule[4:12] == b"eGON.BT0" and capsule[0x1000:0x11000] == stock
    assert sha(capsule) == c["raw_image_sha256"]
    assert sha(firmware) == n["firmware_sha256"] and sha(image) == n["image_sha256"]
    assert FIRMWARE_OFFSET + len(firmware) <= IMAGE_OFFSET and IMAGE_OFFSET + len(image) <= PARTITION_END
    assert CAPSULE_SECTOR + len(capsule) // 512 <= 512 and len(capsule) % 512 == 0
    cookie = fnv(generation)
    assert cookie, "zero cookie cannot be latched"

    gzip_package = (ROOT / "work" / target / "boot-gzip/boot-package.bin").read_bytes()
    recovery = module("release_recovery", HERE / "prepare-recovery-toc.py")
    toc, toc_report = recovery.prepare(gzip_package, sha(gzip_package))
    assert len(toc) == TOC_BYTES

    release = out / "release"
    if release.exists():
        for stale in release.iterdir():
            stale.unlink()
    release.mkdir(exist_ok=True)
    files = {"capsule.bin": capsule, "firmware.bin": firmware, "image.bin": image,
             "recovery-toc.bin": toc, "generation.bin": generation}
    for name, blob in files.items():
        (release / name).write_bytes(blob)
    (out / "recovery-toc-report.json").write_text(json.dumps(toc_report, indent=2) + "\n")
    values = {
        "NATIVE_FORMAT": "1",
        "NATIVE_TARGET": target,
        "NATIVE_GENERATION": n["generation"],
        "NATIVE_COOKIE": f"{cookie:08x}",
        "NATIVE_CAPSULE_SECTOR": CAPSULE_SECTOR,
        "NATIVE_CAPSULE_SIZE": len(capsule),
        "NATIVE_CAPSULE_SHA256": sha(capsule),
        "NATIVE_CAPSULE_BODY_SHA256": sha(capsule[512:]),
        "NATIVE_SECONDARY_SECTOR": 512,
        "NATIVE_STOCK_SHA256": sha(stock),
        "NATIVE_FIRMWARE_OFFSET": FIRMWARE_OFFSET,
        "NATIVE_FIRMWARE_SIZE": len(firmware),
        "NATIVE_FIRMWARE_SHA256": sha(firmware),
        "NATIVE_IMAGE_OFFSET": IMAGE_OFFSET,
        "NATIVE_IMAGE_SIZE": len(image),
        "NATIVE_IMAGE_SHA256": sha(image),
        "NATIVE_TOC_SECTOR": TOC_SECTOR,
        "NATIVE_TOC_SIZE": len(toc),
        "NATIVE_TOC_SHA256": sha(toc),
        "NATIVE_PREVIOUS_TOC_SHA256": " ".join(PREVIOUS_TOCS.get(target, [])),
    }
    (release / "generation").write_text(n["generation"] + "\n")
    (release / "manifest").write_text("".join(f"{key}={json.dumps(str(value)) if ' ' in str(value) or not str(value) else value}\n"
                                              for key, value in values.items()))
    summary = {"target": target, "generation": n["generation"], "cookie": f"{cookie:08x}",
               "files": {name: {"bytes": len(blob), "sha256": sha(blob)} for name, blob in files.items()},
               "spl_sha256": c["native_sha256"], "loader_sha256": n["loader_sha256"],
               "source_boot0_sha256": sha(stock), "device_access": False, "hardware_qualified": False}
    (out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps({k: summary[k] for k in ("target", "generation", "cookie")}))


if __name__ == "__main__":
    main()
