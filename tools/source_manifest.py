#!/usr/bin/env python3
"""Query and verify StockMod preparation metadata and composed images."""

from __future__ import annotations

import argparse
import json
import shlex
import struct
from pathlib import Path

from prepare_stock import BASEOS_NAMES, EXPECTED_NAMES, parse_gpt, sha256_file, sha256_range
from kernel_gzip import BOOT0_OFFSET, derive as derive_gzip, sha as blob_sha


def load(path: Path, expected_target: str) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema") != 1 or data.get("provenance") != "stockmod-image":
        raise ValueError(f"unsupported source manifest: {path}")
    if data.get("target") != expected_target:
        raise ValueError(
            f"source target is {data.get('target')!r}, expected {expected_target!r}"
        )
    return data


def partition(layout: dict, number: int) -> dict:
    for item in layout["partitions"]:
        if item["number"] == number:
            return item
    raise ValueError(f"source manifest has no partition {number}")


def verify_prepared(data: dict, directory: Path) -> None:
    # The manifest is the trust anchor for artifacts restored from the prepared
    # cache rather than rebuilt from firmware, so re-assert the layout contract
    # it records. prepare-stock.sh enforced this against the real GPT; checking
    # it again here catches a manifest that has since been edited or corrupted.
    names = [item["name"] for item in data["layout"]["partitions"]]
    if names != EXPECTED_NAMES[: len(names)]:
        raise ValueError(f"manifest records an unexpected partition order: {names}")

    checks = (
        (directory / "boot-prefix.img", data["boot_prefix"]),
        (directory / "stock-harvest.tar", data["harvest"]),
    )
    for path, record in checks:
        if not path.is_file():
            raise ValueError(f"missing prepared artifact: {path}")
        if path.stat().st_size != record["size"]:
            raise ValueError(f"prepared artifact size changed: {path}")
        if sha256_file(path) != record["sha256"]:
            raise ValueError(f"prepared artifact hash changed: {path}")


def native_files(directory: Path, package_offset: int) -> dict:
    """The model's native boot files and where they go; checked against their manifest."""
    fields = dict(line.split("=", 1) for line in (directory / "manifest").read_text().splitlines() if line)
    files = {name: (directory / f"{name}.bin").read_bytes()
             for name in ("capsule", "firmware", "image", "recovery-toc")}
    for name, key in (("capsule", "CAPSULE"), ("firmware", "FIRMWARE"), ("image", "IMAGE"), ("recovery-toc", "TOC")):
        if len(files[name]) != int(fields[f"NATIVE_{key}_SIZE"]) or blob_sha(files[name]) != fields[f"NATIVE_{key}_SHA256"]:
            raise ValueError(f"native {name} differs from its manifest")
    if int(fields["NATIVE_CAPSULE_SECTOR"]) * 512 != BOOT0_OFFSET or int(fields["NATIVE_TOC_SECTOR"]) * 512 != package_offset:
        raise ValueError("native capsule or package sector differs from the boot layout")
    if (directory / "generation.bin").read_bytes().hex() != fields["NATIVE_GENERATION"]:
        raise ValueError("native generation differs from its manifest")
    return {"raw": [(BOOT0_OFFSET, files["capsule"]), (package_offset, files["recovery-toc"])],
            "boot": [(int(fields["NATIVE_FIRMWARE_OFFSET"]), files["firmware"]),
                     (int(fields["NATIVE_IMAGE_OFFSET"]), files["image"])]}


def write_native(data: dict, prefix: Path, image: Path, directory: Path) -> None:
    """Write the capsule, recovery package, firmware and kernel image into a composed card image."""
    boot = partition(data["layout"], 4)
    native = native_files(directory, derive_gzip(data["target"], prefix)["profile"]["package_offset"])
    with image.open("r+b") as handle:
        for offset, blob in native["raw"]:
            handle.seek(offset)
            handle.write(blob)
        for offset, blob in native["boot"]:
            if offset + len(blob) > boot["sector_count"] * 512:
                raise ValueError("native payload exceeds the boot partition")
            handle.seek(boot["start_sector"] * 512 + offset)
            handle.write(blob)


def verify_composed(data: dict, prefix: Path, image: Path, logo: Path, native_dir: Path | None = None) -> None:
    verify_prepared(data, prefix.parent)
    source_layout = data["layout"]
    output_layout = parse_gpt(image, BASEOS_NAMES)
    gzip_boot = derive_gzip(data["target"], prefix)
    native = native_files(native_dir, gzip_boot["profile"]["package_offset"]) if native_dir else None

    # All four boot partitions retain their GPT identity and geometry. p2 has
    # the replacement bootlogo; p4 must match the exact gzip derivation.
    for number in range(1, 5):
        source = partition(source_layout, number)
        output = partition(output_layout, number)
        for key in ("name", "type_guid", "unique_guid", "start_sector", "end_sector"):
            if source[key] != output[key]:
                raise ValueError(f"partition {number} changed {key}")
        if number == 2:
            continue
        offset = source["start_sector"] * 512
        size = source["sector_count"] * 512
        expected = next(
            item["sha256"] for item in data["preserved_regions"] if item["partition"] == number
        )
        if number == 4:
            expected_boot = bytearray(gzip_boot["expected_boot"])
            for at, blob in native["boot"] if native else ():
                expected_boot[at:at + len(blob)] = blob
            if len(expected_boot) != size:
                raise ValueError("native payload exceeds the boot partition")
            expected = blob_sha(bytes(expected_boot))
        if sha256_range(image, offset, size) != expected:
            raise ValueError(f"partition {number} differs from its expected composed bytes")

    first_partition = partition(source_layout, 1)
    raw_offset = 4 * 512
    raw_size = (first_partition["start_sector"] - 4) * 512
    with prefix.open("rb") as handle:
        handle.seek(raw_offset)
        expected_raw = bytearray(handle.read(raw_size))
    for offset, blob in ((BOOT0_OFFSET, gzip_boot["boot0"]),
                         (gzip_boot["profile"]["package_offset"], gzip_boot["package"])):
        expected_raw[offset - raw_offset:offset - raw_offset + len(blob)] = blob
    for offset, blob in native["raw"] if native else ():
        expected_raw[offset - raw_offset:offset - raw_offset + len(blob)] = blob
    if sha256_range(image, raw_offset, raw_size) != blob_sha(expected_raw):
        raise ValueError("raw boot region differs from the exact gzip bootloader derivation"
                         + (" with the native boot files" if native else ""))

    source_root = partition(source_layout, 5)
    output_root = partition(output_layout, 5)
    if source_root["start_sector"] != output_root["start_sector"]:
        raise ValueError("root partition start changed")

    # A/B geometry: `rootfs` is slot A, an identically sized unallocated slot B
    # follows it, and /data starts exactly two slots in. gptslot derives the
    # same relationship at runtime and refuses anything else.
    output_data = partition(output_layout, 6)
    slot_sectors = output_root["sector_count"]
    if output_data["start_sector"] != output_root["start_sector"] + 2 * slot_sectors:
        raise ValueError("UDISK does not start exactly two rootfs slots in")

    # Only the user-visible FAT volume may claim a desktop drive letter.
    for item in output_layout["partitions"]:
        expected = 0 if item["name"] == "primary" else 0xC000000000000000
        if item["attributes"] != expected:
            raise ValueError(
                f"partition {item['number']} ({item['name']}) has attributes "
                f"{item['attributes']:#x}, expected {expected:#x}"
            )

    bmp = logo.read_bytes()[:26]
    if len(bmp) < 26 or bmp[:2] != b"BM":
        raise ValueError("generated boot logo is not a BMP")
    width, height = struct.unpack_from("<ii", bmp, 18)
    expected_logo = data["bootlogo"]
    if width != expected_logo["width"] or abs(height) != expected_logo["height"]:
        raise ValueError(
            f"boot logo is {width}x{abs(height)}, expected "
            f"{expected_logo['width']}x{expected_logo['height']}"
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    shell_parser = subparsers.add_parser("shell")
    shell_parser.add_argument("manifest", type=Path)
    shell_parser.add_argument("target")
    verify_parser = subparsers.add_parser("verify")
    verify_parser.add_argument("manifest", type=Path)
    verify_parser.add_argument("target")
    image_parser = subparsers.add_parser("verify-image")
    image_parser.add_argument("manifest", type=Path)
    image_parser.add_argument("target")
    image_parser.add_argument("prefix", type=Path)
    image_parser.add_argument("image", type=Path)
    image_parser.add_argument("logo", type=Path)
    image_parser.add_argument("--native", type=Path, help="also expect this native boot directory's files")
    native_parser = subparsers.add_parser("write-native")
    native_parser.add_argument("manifest", type=Path)
    native_parser.add_argument("target")
    native_parser.add_argument("prefix", type=Path)
    native_parser.add_argument("image", type=Path)
    native_parser.add_argument("native", type=Path)
    arguments = parser.parse_args()

    data = load(arguments.manifest, arguments.target)
    if arguments.command == "shell":
        mapping = {
            "SOURCE_TARGET": data["target"],
            "SOURCE_BOOT_PREFIX_SIZE": data["boot_prefix"]["size"],
        }
        # Every preserved partition's geometry, so gates can address p1/p3/p4
        # inside boot-prefix.img without parsing the manifest themselves.
        for number in range(1, 6):
            item = partition(data["layout"], number)
            mapping[f"SOURCE_P{number}_START"] = item["start_sector"]
            mapping[f"SOURCE_P{number}_SECTORS"] = item["sector_count"]
        for key, value in mapping.items():
            print(f"{key}={shlex.quote(str(value))}")
    elif arguments.command == "verify":
        verify_prepared(data, arguments.manifest.parent)
        print(f"prepared artifacts OK: {data['target']}")
    elif arguments.command == "write-native":
        write_native(data, arguments.prefix, arguments.image, arguments.native)
    else:
        verify_composed(data, arguments.prefix, arguments.image, arguments.logo, arguments.native)
        print(f"preserved firmware regions and boot logo OK: {data['target']}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as error:
        raise SystemExit(f"source-manifest: {error}")
