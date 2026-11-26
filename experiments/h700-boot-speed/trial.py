#!/usr/bin/env python3
"""Pinned RG34XX SP boot-package trials using the existing checked lab path.

Source work/boot-speed/work/lab/rg34sp.env first. This does not use the lab's
older 1.3.0 backup as the baseline: trials start from the exact 1.4.0 package.
"""
from __future__ import annotations

import argparse
import json
import struct
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "tools"))
import lab
import kernel_gzip as kg
from lab_devices import PROFILES

PROFILE_TARGET = lab.TARGET if lab.TARGET in PROFILES else "rg34xxsp"
PROFILE = PROFILES[PROFILE_TARGET]
WORK = ROOT / PROFILE["work"]
BASE = ROOT / "work" / PROFILE_TARGET / "boot-gzip/boot-package.bin"
BASE_SHA = PROFILE["package_sha256"]
BASE_IMAGE = BASE.parent / "boot.img"


def image_backup() -> bytes:
    path = WORK / "baseline-boot-partition.bin"
    metadata = WORK / "baseline-boot-partition.json"
    if not path.exists():
        expected = BASE_IMAGE.read_bytes()
        lab.require(lab.device_sha("boot", len(expected)) == lab.sha(expected), "boot image is not the baseline")
        blob = lab.read_region("boot")
        lab.require(blob[:len(expected)] == expected, "boot backup differs from baseline")
        path.write_bytes(blob)
        metadata.write_text(json.dumps({"bytes": len(blob), "sha256": lab.sha(blob)}, indent=2) + "\n")
    blob = path.read_bytes()
    info = json.loads(metadata.read_text())
    lab.require(len(blob) == info["bytes"] and lab.sha(blob) == info["sha256"], "boot backup changed")
    return blob


def write_image(image: Path | None, proof: dict | None = None) -> None:
    baseline = image_backup()
    record = WORK / "installed-images.json"
    known = json.loads(record.read_text()) if record.exists() else {}
    blobs = []
    for digest, filename in known.items():
        blob = Path(filename).read_bytes()
        lab.require(lab.sha(blob) == digest, "a previously installed image changed")
        blobs.append(blob)
    if image:
        blob = image.read_bytes()
        lab.require(proof["boot_image_sha256"] == lab.sha(blob)
                    and proof["original_boot_image_sha256"] == lab.sha(BASE_IMAGE.read_bytes())
                    and proof["verification"].get("gzip_fallback_exact"), "image proof/fallback differs")
        kg.split(blob)
        span = max([len(BASE_IMAGE.read_bytes()), len(blob)] + [len(old) for old in blobs])
        desired = blob + baseline[len(blob):span]
    else:
        span = max([len(BASE_IMAGE.read_bytes())] + [len(old) for old in blobs])
        desired = baseline[:span]
    expected = {lab.sha(baseline[:span])}
    expected.update(lab.sha(old + baseline[len(old):span]) for old in blobs)
    current = lab.device_sha("boot", span)
    lab.require(current in expected or current == lab.sha(desired), "unknown on-card boot image; refusing write")
    if image:
        known[lab.sha(blob)] = str(image.resolve())
        record.write_text(json.dumps(known, indent=2) + "\n")
    if current != lab.sha(desired):
        lab.write_region("boot", desired)


def checked_base() -> bytes:
    blob = BASE.read_bytes()
    lab.require(lab.TARGET == PROFILE_TARGET and lab.SERIAL == PROFILE["serial"],
                "this trial is pinned to its explicit lab identity")
    lab.require(lab.sha(blob) == BASE_SHA, "baseline package differs")
    return blob


def install(package: Path | None, proof: Path | None, image: Path | None = None) -> None:
    base = checked_base()
    blob = package.read_bytes() if package else base
    lab.identity()
    lab.require(len(blob) == len(base), "package length differs")
    lab.require(kg.checksum(blob, 20) == kg.word(blob, 20), "invalid TOC1 checksum")
    lab.require(kg.checksum(blob[0x800:0x100800], 12) == kg.word(blob, 0x80C), "invalid U-Boot checksum")
    expected_tail = base[0x100800:]
    if package:
        lab.require(proof is not None, "an emulation proof is required")
        report = json.loads(proof.read_text())
        lab.require(report["original_package_sha256"] == BASE_SHA
                    and report["patched_package_sha256"] == lab.sha(blob)
                    and bool(report["verification"]), "proof does not match the package")
        lab.require("boot_image_sha256" not in report or image is not None, "this package requires its matched --image")
        edits = report.get("fdt_edits", [])
        lab.require(edits in ([], [{"node": "/soc@03000000/hdmi@06000000", "property": "status",
                                   "before": "okay", "after": "late"}]), "unapproved device-tree change")
        if edits:
            tree = bytearray(base[0x11B800:0x13EA00])
            kg.fdt_edit.replace(tree, edits[0]["node"].lstrip("/"), "status", b"okay\0", b"late\0")
            tail = bytearray(expected_tail)
            tail[0x11B800 - 0x100800:0x13EA00 - 0x100800] = tree
            expected_tail = bytes(tail)
    lab.require(blob[:20] == base[:20] and blob[24:0x800] == base[24:0x800]
                and blob[0x100800:] == expected_tail, "change outside U-Boot and declared HDMI status")
    WORK.mkdir(parents=True, exist_ok=True)
    record = WORK / "installed-packages.json"
    known = json.loads(record.read_text()) if record.exists() else {BASE_SHA: str(BASE)}
    current = lab.device_sha("toc1", len(base))
    lab.require(current in known, f"unknown on-card package {current}; refusing write")
    if current == BASE_SHA:
        backup = WORK / "baseline-package.bin"
        if not backup.exists():
            actual = lab.read_region("toc1")
            lab.require(actual == base, "baseline read differs")
            backup.write_bytes(actual)
    if image:
        # This adapter is proven to boot the current gzip image. Installing it
        # first leaves a bootable intermediate state if the image write stops.
        lab.require(report["verification"].get("gzip_fallback_exact"), "package lacks proven gzip fallback")
        image_backup()
    elif (WORK / "baseline-boot-partition.bin").exists():
        # Restore gzip before removing the adapter, including the exact tail
        # overwritten by the larger experimental Android image.
        write_image(None)
    if current != lab.sha(blob):
        lab.write_region("toc1", blob)
    known[lab.sha(blob)] = str(package or BASE)
    record.write_text(json.dumps(known, indent=2) + "\n")
    if image:
        write_image(image, report)
    print(f"installed {lab.sha(blob)}; not restarted", flush=True)


def read_stamps() -> dict:
    raw = lab.sh("od -An -tx1 -v /sys/firmware/fdt", 60)
    tree = bytes.fromhex(raw)
    total = struct.unpack_from(">I", tree, 4)[0]
    lab.require(len(tree) == total, "short live FDT")
    at = tree.rfind(b"MATS", max(0, total - 16384))
    if at < 0:
        return {"found": False}
    values = struct.unpack_from("<16Q", tree, at + 8)
    return {"found": True, "fdt_offset": at,
            "seconds": [value / 24_000_000 for value in values]}


def backup_env():
    checked_base()
    lab.identity()
    sys.path.insert(0, str(HERE / "fastboot"))
    from oneshot import parse_env
    WORK.mkdir(parents=True, exist_ok=True)
    path = WORK / "baseline-env.bin"
    lab.require(not path.exists(), "environment backup already exists")
    blob = lab.read_region("env", 0x20000)
    values = parse_env(blob)
    lab.require(values["boot_normal"].strip() == "sunxi_flash read 45000000 boot;bootm 45000000",
                "environment does not default to the normal boot")
    path.write_bytes(blob)
    path.with_suffix(".sha256").write_text(lab.sha(blob) + "\n")
    print("pinned environment", lab.sha(blob))


def restart(label: str) -> None:
    checked_base()
    lab.identity()
    lab.reboot()
    data = lab.collect(label)
    data["package_sha256"] = lab.device_sha("toc1", len(BASE.read_bytes()))
    data["stamps"] = read_stamps()
    data["load"] = lab.sh("uptime; cat /sys/devices/system/cpu/online")
    out = WORK / "runs"
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{label}-{data['boot_id']}.json").write_text(json.dumps(data, indent=2) + "\n")
    print(json.dumps(data["stamps"], indent=2), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    put = commands.add_parser("install")
    put.add_argument("package", type=Path)
    put.add_argument("--proof", type=Path, required=True)
    put.add_argument("--image", type=Path)
    commands.add_parser("restore")
    run = commands.add_parser("restart")
    run.add_argument("label")
    commands.add_parser("read")
    commands.add_parser("backup-env")
    args = parser.parse_args()
    if args.command == "install":
        install(args.package, args.proof, args.image)
    elif args.command == "restore":
        install(None, None)
    elif args.command == "restart":
        restart(args.label)
    elif args.command == "backup-env":
        backup_env()
    else:
        print(json.dumps(read_stamps(), indent=2))
