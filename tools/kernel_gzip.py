#!/usr/bin/env python3
"""Derive the fast boot set from pinned prepared firmware; never write a device.

The vendor Android handler forces raw Image. Change its compression selector,
preserving the Android layout and every other TOC1 component. Then drop what
startup only waits for: the initramfs (an empty archive), kernel serial output
and slow boot-time RCU grace periods (kernel arguments in the Android header),
U-Boot/boot0 serial output (`debug_mode` 3 -> 0 in the TOC1 device tree and
the boot0 header) and five USB host controllers (status "okay" -> "late" in
that device tree; a module starts them once init is running). On targets with
a `panel_timing` profile it also shortens the panel's front porches so it
refreshes at 60 Hz. The package and boot image must be installed together. The earlier gzip-only pair is still
derived so cards that carry it can be recognised. These are exact-firmware
patches, not signatures to apply speculatively to another U-Boot build.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import struct
from pathlib import Path

import fdt_edit

CATALOG = Path(__file__).resolve().parent.parent / "manifest/kernel-gzip.json"
STAMP = 0x5F0A6C39
# First-stage loader: fixed place and length, verbosity byte in its private
# header. It rewrites its own first sector on the card at every boot; the body
# after that sector never changes.
BOOT0_OFFSET, BOOT0_SIZE, BOOT0_DEBUG, BOOT0_BODY = 8192, 65536, 0x34, 512
# USB host controllers the kernel must not start by itself. h700_late_usb.ko
# starts every node whose status is exactly "late"; any other value but "okay"
# would leave the port dead. EHCI 2 is not in this list and must stay "okay":
# without it the OTG port does not enumerate as a device.
LATE_USB = ("ehci1-controller@0x05200000", "ohci1-controller@0x05200400", "ohci2-controller@0x05310400",
            "ehci3-controller@0x05311000", "ohci3-controller@0x05311400")
KEPT_USB = "ehci2-controller@0x05310000"
# Panel timing node. lcd_ht/lcd_vt are the totals; the porches and sync widths
# stay as the vendor set them, so only the front porches change.
PANEL_NODE = "lcd0@01c0c000"
# Android header command-line field (offset 0x40, 512 bytes, NUL padded).
# U-Boot appends it to the kernel arguments; the kernel still prints panics.
# loglevel=1 still prints a panic. The kernel waits several 10 ms ticks for
# every RCU grace period while it starts; expedited ones take microseconds,
# and rcu_normal_after_boot hands the running system back its usual behaviour.
CMDLINE = b"loglevel=1 rcupdate.rcu_expedited=1 rcupdate.rcu_normal_after_boot=1"
# A newc archive holding only its end marker, padded to one 512-byte block.
EMPTY_CPIO = (b"070701" + b"%08X" * 13 % (0, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0, 11, 0)
              + b"TRAILER!!!\0").ljust(512, b"\0")
SIZES = (8, 16, 24, 0x660, 0x670)  # kernel, ramdisk, second, recovery DTBO, DTB


def sha(blob: bytes) -> str:
    return hashlib.sha256(blob).hexdigest()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def word(blob: bytes, offset: int) -> int:
    return struct.unpack_from("<I", blob, offset)[0]


def checksum(blob: bytes, field: int) -> int:
    require(len(blob) % 4 == 0, "unaligned Allwinner package")
    return (sum(struct.unpack(f"<{len(blob) // 4}I", blob)) - word(blob, field) + STAMP) & 0xFFFFFFFF


def align(size: int) -> int:
    return (size + 2047) & ~2047


def image_id(parts: list[bytes]) -> bytes:
    digest = hashlib.sha1()
    for part in parts:
        digest.update(part)
        digest.update(struct.pack("<I", len(part)))
    return digest.digest() + bytes(12)


def changed(old: bytes, new: bytes) -> set[int]:
    require(len(old) == len(new), "patch changed a length")
    return {i for i, (a, b) in enumerate(zip(old, new)) if a != b}


def split(image: bytes) -> tuple[list[bytes], int]:
    """Android v2 components in SIZES order, and the padded image length."""
    parts, pos = [], 2048
    for at in SIZES:
        size = word(image, at)
        require(pos + size <= len(image), "truncated Android component")
        parts.append(image[pos:pos + size])
        pos += align(size)
    require(image[0x240:0x260] == image_id(parts), "Android image ID mismatch")
    return parts, pos


def join(header: bytes, parts: list[bytes], cmdline: bytes = b"") -> bytes:
    """Rebuild the image around `parts`; only sizes, command line and ID change."""
    require(len(cmdline) < 512 and b"\0" not in cmdline, "command line does not fit the Android header")
    head = bytearray(header)
    for at, part in zip(SIZES, parts):
        struct.pack_into("<I", head, at, len(part))
    head[0x40:0x240] = cmdline.ljust(512, b"\0")
    head[0x240:0x260] = image_id(parts)
    image = bytes(head) + b"".join(part + bytes(align(len(part)) - len(part)) for part in parts)
    require(split(image) == (parts, len(image)), "rebuilt Android image does not parse back")
    return image


def derive(target: str, prefix: Path, catalog: Path = CATALOG) -> dict:
    profiles = json.loads(catalog.read_text())
    require(profiles["schema"] == 1, "unsupported gzip profile catalog")
    require(target in profiles["profiles"], f"no audited gzip profile for {target}")
    profile = profiles["profiles"][target]
    require(BOOT0_OFFSET + BOOT0_SIZE <= profile["package_offset"] and
            profile["package_offset"] + profile["package_size"] <= profile["boot_partition_start"] * 512,
            "boot regions overlap")
    with prefix.open("rb") as handle:
        handle.seek(BOOT0_OFFSET)
        original_boot0 = handle.read(BOOT0_SIZE)
        handle.seek(profile["package_offset"])
        original_package = handle.read(profile["package_size"])
        handle.seek(profile["boot_partition_start"] * 512)
        original_boot = handle.read(profile["boot_partition_sectors"] * 512)
    require(sha(original_package) == profile["package_sha256"], "unknown original TOC1 package")
    require(sha(original_boot) == profile["boot_partition_sha256"], "unknown original boot partition")
    require(sha(original_boot0) == profile["boot0_sha256"], "unknown original boot0")
    package = bytearray(original_package)
    require(package[:16] == b"sunxi-package\0\0\0" and word(package, 16) == 0x89119800,
            "not the expected Allwinner TOC1")
    require(word(package, 36) == len(package) and checksum(package, 20) == word(package, 20),
            "invalid original TOC1 checksum/length")
    require(word(package, 32) == 4 and package[60:64] == b"MIE;", "unexpected TOC1 table")
    items = {}
    for i in range(4):
        pos = 64 + 368 * i
        name = bytes(package[pos:pos + 64]).split(b"\0")[0].decode("ascii")
        start, size, encryption, kind = struct.unpack_from("<4I", package, pos + 64)
        require(encryption == 0 and kind == 3 and start + size <= len(package), "invalid TOC1 item")
        require(package[pos + 364:pos + 368] == b"IIE;", "invalid TOC1 item footer")
        items[name] = (start, size)
    require(set(items) == {"u-boot", "monitor", "dtbo", "dtb"}, "unexpected TOC1 components")
    start, size = items["u-boot"]
    require((start, size) == (profile["uboot_offset"], profile["uboot_size"]), "U-Boot geometry changed")
    uboot = bytearray(package[start:start + size])
    require(sha(uboot) == profile["uboot_sha256"], "unknown U-Boot")
    require(word(uboot, 20) == len(uboot) and checksum(uboot, 12) == word(uboot, 12),
            "invalid original U-Boot checksum/length")
    selector = profile["selector_offset"]
    context = bytes.fromhex(profile["selector_context"])
    require(uboot[selector:selector + len(context)] == context, "Android selector context changed")
    require(context[:4] == bytes.fromhex("4ff40073"), "unexpected original compression selector")
    uboot[selector:selector + 4] = bytes.fromhex("40f20123")
    struct.pack_into("<I", uboot, 12, checksum(uboot, 12))
    package[start:start + size] = uboot
    struct.pack_into("<I", package, 20, checksum(package, 20))
    allowed = set(range(20, 24)) | set(range(start + 12, start + 16)) | set(range(start + selector, start + selector + 4))
    require(changed(original_package, package) <= allowed, "patch changed bytes outside the instruction and checksums")
    gzip_package = bytes(package)
    # U-Boot takes its serial verbosity from the package's own device tree.
    tree_start, tree_size = items["dtb"]
    tree = bytearray(package[tree_start:tree_start + tree_size])
    debug = tree_start + fdt_edit.replace(tree, "platform", "debug_mode", struct.pack(">I", 3), bytes(4))
    quiet = set(range(debug, debug + 4))
    for node in LATE_USB:
        held = tree_start + fdt_edit.replace(tree, node, "status", b"okay\0", b"late\0")
        quiet |= set(range(held, held + 4))
    require(fdt_edit.find(tree, KEPT_USB, "status")[1] == b"okay\0", "EHCI 2 is not enabled in the device tree")
    package[tree_start:tree_start + tree_size] = tree
    struct.pack_into("<I", package, 20, checksum(package, 20))
    require(quiet.isdisjoint(allowed) and changed(gzip_package, package) <= set(range(20, 24)) | quiet and
            changed(original_package, package) <= allowed | quiet,
            "patch changed bytes outside the instruction, debug_mode, USB status values and checksums")
    fast_v1_package = bytes(package)
    timing = profile.get("panel_timing", {})
    require(set(timing) <= {"lcd_ht", "lcd_vt"}, "unknown panel timing value")
    panel = set()
    for prop, (active, porch) in (("lcd_ht", ("lcd_x", "lcd_hbp")), ("lcd_vt", ("lcd_y", "lcd_vbp"))):
        if prop not in timing:
            continue
        old, new = timing[prop]
        used = sum(struct.unpack(">I", fdt_edit.find(tree, PANEL_NODE, name)[1])[0] for name in (active, porch))
        require(new > used, f"{prop} {new} leaves no front porch")
        at = tree_start + fdt_edit.replace(tree, PANEL_NODE, prop, struct.pack(">I", old), struct.pack(">I", new))
        panel |= set(range(at, at + 4))
    package[tree_start:tree_start + tree_size] = tree
    struct.pack_into("<I", package, 20, checksum(package, 20))
    require(changed(fast_v1_package, package) <= set(range(20, 24)) | panel and
            changed(original_package, package) <= allowed | quiet | panel,
            "panel timing changed bytes outside lcd_ht, lcd_vt and the checksum")

    boot0 = bytearray(original_boot0)
    require(boot0[4:12] == b"eGON.BT0" and word(boot0, 0x10) == BOOT0_SIZE == len(boot0), "not the expected boot0")
    require(checksum(boot0, 12) == word(boot0, 12), "invalid original boot0 checksum")
    require(boot0[BOOT0_DEBUG] == 3, "unexpected boot0 debug_mode")
    boot0[BOOT0_DEBUG] = 0
    struct.pack_into("<I", boot0, 12, checksum(boot0, 12))
    require(changed(original_boot0, boot0) <= {12, 13, 14, 15, BOOT0_DEBUG} and
            boot0[BOOT0_BODY:] == original_boot0[BOOT0_BODY:],
            "boot0 patch changed bytes outside debug_mode and its checksum")

    require(original_boot[:8] == b"ANDROID!", "not an Android boot image")
    require(word(original_boot, 36) == 2048 and word(original_boot, 40) == 2 and
            word(original_boot, 0x66C) == 1660, "unsupported Android header/page size")
    require([word(original_boot, at) for at in (12, 20, 28, 32)] ==
            [0x40080000, 0x42000000, 0x40F00000, 0x40000100], "unexpected image load addresses")
    require(original_boot[0x40:0x240] == bytes(512) and original_boot[0x260:0x660] == bytes(1024),
            "original Android command line is not empty")
    sizes = [word(original_boot, at) for at in SIZES]
    require(sizes[2:4] == [0, 0] and 0 < sizes[0] < 32 * 1024 * 1024,
            "unsupported components or kernel decoder output size")
    require(0x40080000 + sizes[0] <= 0x42000000, "kernel output overlaps ramdisk")
    parts, original_length = split(original_boot)
    kernel, original_ramdisk, dtb = parts[0], parts[1], parts[4]
    require(kernel[0x38:0x3C] == b"ARM\x64", "kernel is not an uncompressed ARM64 Image")
    stream = io.BytesIO()
    with gzip.GzipFile(fileobj=stream, mode="wb", compresslevel=9, mtime=0, filename="") as out:
        out.write(kernel)
    packed = stream.getvalue()
    require(gzip.decompress(packed) == kernel, "gzip roundtrip failed")
    header = original_boot[:2048]
    gzip_boot = join(header, [packed, original_ramdisk, b"", b"", dtb])
    boot = join(header, [packed, EMPTY_CPIO, b"", b"", dtb], CMDLINE)
    sizes_id = set(range(8, 12)) | set(range(0x240, 0x260))
    require(changed(header, gzip_boot[:2048]) <= sizes_id and changed(header, boot[:2048]) <=
            sizes_id | set(range(16, 20)) | set(range(0x40, 0x40 + len(CMDLINE))),
            "Android header changed outside sizes, command line and image ID")
    require(len(boot) < len(gzip_boot) < original_length, "compressed image did not shrink")
    # Preserve unused p4 tail exactly, matching the validated device experiment.
    expected_boot = boot + original_boot[len(boot):]
    return dict(profile=profile, package=bytes(package), uboot=bytes(uboot), boot=boot,
                original_package=original_package, original_boot=original_boot,
                expected_boot=expected_boot, kernel=kernel, ramdisk=EMPTY_CPIO, dtb=dtb,
                original_image=original_boot[:original_length], original_ramdisk=original_ramdisk,
                gzip_package=gzip_package, gzip_boot=gzip_boot, fast_v1_package=fast_v1_package,
                boot0=bytes(boot0),
                original_boot0=original_boot0, package_dtb=bytes(tree))


def prepare(target: str, prefix: Path, output: Path) -> dict:
    change = derive(target, prefix)
    p = change["profile"]
    output.mkdir(parents=True, exist_ok=True)
    for name, key in (("boot-package.bin", "package"), ("boot.img", "boot")):
        (output / name).write_bytes(change[key])
    manifest = {
        "target": target, "package_offset": p["package_offset"],
        "package_size": len(change["package"]), "boot_start": p["boot_partition_start"],
        "boot_sectors": p["boot_partition_sectors"], "boot_image_size": len(change["boot"]),
        "original_package_sha256": sha(change["original_package"]),
        "original_boot_sha256": sha(change["original_boot"]),
        "package_sha256": sha(change["package"]), "boot_image_sha256": sha(change["boot"]),
        "boot_partition_sha256": sha(change["expected_boot"]),
        "kernel_sha256": sha(change["kernel"]), "kernel_size": len(change["kernel"]),
        # The pair shipped before this one, and the vendor image, by image bytes
        # alone: what a card can hold at the start of p4 before migrating.
        "gzip_package_sha256": sha(change["gzip_package"]),
        # The package shipped before 60 Hz panel timing, with the same boot image.
        "fast_v1_package_sha256": sha(change["fast_v1_package"]),
        "gzip_boot_image_size": len(change["gzip_boot"]), "gzip_boot_image_sha256": sha(change["gzip_boot"]),
        "original_boot_image_size": len(change["original_image"]),
        "original_boot_image_sha256": sha(change["original_image"]),
        # No boot0 file is shipped: only its body can be pinned on a card, so a
        # migration edits the debug byte and checksum of the live copy.
        "boot0_offset": BOOT0_OFFSET, "boot0_size": BOOT0_SIZE, "boot0_debug_offset": BOOT0_DEBUG,
        "boot0_original_sha256": sha(change["original_boot0"]), "boot0_body_offset": BOOT0_BODY,
        "boot0_body_sha256": sha(change["original_boot0"][BOOT0_BODY:]),
    }
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    # Trusted rootfs-owned manifest: only fixed keys, target IDs, decimal sizes
    # and hexadecimal hashes. Never source data from a card-supplied manifest.
    require(all(str(v).replace("_", "").isalnum() for v in manifest.values()), "unsafe shell manifest value")
    (output / "manifest").write_text("".join(f"BOOT_{k.upper()}={v}\n" for k, v in manifest.items()))
    return manifest


def apply(target: str, prefix: Path, image: Path) -> None:
    require(image.resolve() != prefix.resolve(), "refusing to modify prepared firmware")
    change = derive(target, prefix)
    p = change["profile"]
    regions = ((BOOT0_OFFSET, change["original_boot0"], change["boot0"]),
               (p["package_offset"], change["original_package"], change["package"]),
               (p["boot_partition_start"] * 512, change["original_boot"], change["boot"]))
    with image.open("r+b") as handle:
        for offset, old, _ in regions:
            handle.seek(offset)
            require(handle.read(len(old)) == old, "image does not contain the pinned original boot bytes")
        for offset, _, new in regions:
            handle.seek(offset)
            handle.write(new)
    verify(target, prefix, image)


def verify(target: str, prefix: Path, image: Path) -> None:
    change = derive(target, prefix)
    p = change["profile"]
    with image.open("rb") as handle:
        for offset, expected in ((BOOT0_OFFSET, change["boot0"]), (p["package_offset"], change["package"]),
                                 (p["boot_partition_start"] * 512, change["expected_boot"])):
            handle.seek(offset)
            require(handle.read(len(expected)) == expected, "composed boot0, package or boot image differs from derivation")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("prepare", "apply", "verify"))
    parser.add_argument("target")
    parser.add_argument("prefix", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    if args.command == "prepare":
        print(json.dumps(prepare(args.target, args.prefix, args.output), indent=2))
    else:
        globals()[args.command](args.target, args.prefix, args.output)
        print(f"{args.target}: fast boot set {args.command} OK")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, struct.error) as error:
        raise SystemExit(f"kernel-gzip: {error}")
