#!/usr/bin/env python3
"""Artifact gates against prepared firmware; optional target arguments."""
import gzip
import json
from pathlib import Path
import struct
import sys
import tempfile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import fdt_edit
import kernel_gzip as k
from device_profile import load_profiles

# The pair shipped before this one ("gzip-v1": selector patch and gzip kernel,
# vendor ramdisk, empty command line), as (TOC1 package, boot image) SHA-256
# derived by tools/kernel_gzip.py at a4cb68d. Cards in use carry these bytes.
GZIP_V1 = {
    "rg28xx": ("1919d06cf724783c09f93274a130ebfddd3a20a33ebf1b5c88641da8a68a0a0c",
               "a920f91a8b841d3fd5b6397e6246f2ccd8d6e4038dbee1aafcdacecb99fd9e6b"),
    "rg34xx": ("91941e094c0072b2d001808f70d9e5d26d31e6ac5c5f283cf890ed7505803468",
               "04374a018bb206aa7762d21cd43ea2bd9bc7f10e288aa98208d211d2086a2af7"),
    "rg34xxsp": ("59611c5b712ee6ddce1b26e77ec19ee06bdb0823c6cc07bd8017a5a4df46ec2a",
                 "9985294dd591a6272549528b9f7b8a5a369775d39e223d3042d97a907c4eecc1"),
    "rg35xxh": ("3caecfdfb95c39bfd4a18052bd5c24934db2de85585ce482b8198b67031ec375",
                "9f833f5cdf01cb90c279a217ee6a5711e49744302268dd8db232afd03449e4ae"),
    "rg35xxplus": ("e831acfc47844b2806a6e8e3c249bae0ad83243580648b981e96c77f384da52a",
                   "6fa5738b0df4f775ad6d89a040cfa6c2d24e38a158475e27389ec7870ac83741"),
    "rg35xxpro": ("9d4b0d4691223db8b68ced1e295a0ece50fecfcc74c4582ef5e88ef3284da08e",
                  "b073440f7c312030d990a83d36a1948100cf5a545c3598c42f724009553bd530"),
    "rg35xxsp": ("0e1deb0b4fdccf4da5147d6de2bfa5b99b3d211382fd6878d3954da1d61b0a38",
                 "b79b837b00e2da3dbccf3ff06a101d5e503265a54f35c5ffedeef208ab304a3a"),
    "rg40xxh": ("dab9cfcaaba51a23813105b9835947a38ed7d2fc348fc64c1539b159dffee713",
                "7998b63d174a3e98b349d4c827bb2b1d8767e380cb00a208257bdd36993f3100"),
    "rg40xxv": ("f54623367c0abb11a887fe9fd688d18b841d31431b2f3f656e27487b5da871e4",
                "7203a80c68bc5a8f3c4e5e49358c78c4fda2f4791d146608ba7606c4c7e9e568"),
    "rgcubexx": ("db1783572b2cc489bf4315c4da4b8e3c386d629b8f31b2022e1f3943e3dc3885",
                 "7a4afd8daa3cbe0c889ecd635cb4c49839540af4428813fec2076adacc467922"),
    "rgsp": ("fa745599c9eb95110f03c6c426e6d08e0317146d51059c6a790e25f604ea0942",
             "2b9aeb76ace6c781e21aec1edc200cab967b0bf0d162c54588a9c339caf7490c"),
}
# The fast package as shipped before 60 Hz panel timing, derived by
# tools/kernel_gzip.py at 4a59bc3. Cards in use carry these bytes.
FAST_V1 = {
    "rg28xx": "ffabd96eae65a94b0a0dc6ce33db7314688b3ead6169d09553b463a8e58fd490",
    "rg34xx": "56371d984e29e74db63009ce27f829eb0a55ce9007ee5e40c52479b22418af3d",
    "rg34xxsp": "32f92785f3e0819acbd60776e2ef839c1fd1924b31960b98363a93c85300db7e",
    "rg35xxh": "7d4648c429ca5c21b084bb40147b3ababbd0d200bb5a2c2c05beef26902c0c55",
    "rg35xxplus": "b3cc7ea963177fb9110e26e7772847b0da084fb275d6e955c1bc7c9103ced8be",
    "rg35xxpro": "04d64e1772066fdd11e05727e89576b783340dd2b5a1238e9248c5be41a1d605",
    "rg35xxsp": "de38acd6633b7012498c121686f93956fbf625b879fffcea5e85ba5e08dd850c",
    "rg40xxh": "d022ec52d5cd91aee7b9336d45dc7c9f6825573793e6e6175902af37b695aa80",
    "rg40xxv": "ea7031f8e64712d9c5a0d8530e4b5b588527f397ebda8239ac4c718c50cd886a",
    "rgcubexx": "dcd1998046d5174d1f9ffb4e20cbe10fd0e32ce111ef880d9a6d5c9ba6a1e972",
    "rgsp": "f6664df7c8960297f2e690c10345220471f37282eb564355df1872fcd855cd15",
}
# The fast package and boot image that booted on the RG SP: fast-rcu-lateusb
# in experiments/h700-boot-speed/evidence/variants.json.
PROVEN = {"rgsp": ("f6664df7c8960297f2e690c10345220471f37282eb564355df1872fcd855cd15",
                   "95f32dc31133c524c3001de825b145244b9362acb98acd406a99aa6379406e99")}
EMPTY = (b"070701" + b"00000000" * 4 + b"00000001" + b"00000000" * 6 + b"0000000B" + b"00000000"
         + b"TRAILER!!!\0").ljust(512, b"\0")


def word(blob, at):
    return struct.unpack_from("<I", blob, at)[0]


def span(start, size):
    return set(range(start, start + size))


def diff(old, new):
    assert len(old) == len(new)
    return {i for i, (a, b) in enumerate(zip(old, new)) if a != b}


def rejects(error, call, *args):
    try:
        call(*args)
    except ValueError as exc:
        assert error in str(exc), exc
    else:
        raise AssertionError(f"accepted what must fail with {error!r}")


def put(path, offset, blob):
    with path.open("r+b") as f:
        f.seek(offset)
        f.write(blob)


def get(path, offset, size):
    with path.open("rb") as f:
        f.seek(offset)
        return f.read(size)


def check(target):
    prefix = ROOT / "work" / target / "boot-prefix.img"
    d = k.derive(target, prefix)
    p, boot, image, partition = d["profile"], d["boot"], d["original_image"], d["original_boot"]

    # Boot image: the same kernel behind gzip, an empty ramdisk, the quiet
    # command line and the same DTB. Only sizes, command line and ID differ.
    packed = boot[2048:2048 + word(boot, 8)]
    assert gzip.decompress(packed) == d["kernel"] == image[2048:2048 + word(image, 8)]
    at = 2048 + k.align(len(packed))
    assert word(boot, 16) == 512 and boot[at:at + 2048] == EMPTY + bytes(1536)
    assert d["ramdisk"] == k.EMPTY_CPIO == EMPTY
    vendor = 2048 + k.align(word(image, 8))
    assert d["original_ramdisk"] == image[vendor:vendor + word(image, 16)] and d["original_ramdisk"][:2] == b"\x1f\x8b"
    dtb = image[vendor + k.align(word(image, 16)):][:word(image, 0x670)]
    assert d["dtb"] == dtb == boot[at + 2048:at + 2048 + word(boot, 0x670)] and dtb[:4] == bytes.fromhex("d00dfeed")
    assert len(boot) == at + 2048 + k.align(len(dtb)) < len(d["gzip_boot"]) < len(image)
    assert k.CMDLINE == b"loglevel=1 rcupdate.rcu_expedited=1 rcupdate.rcu_normal_after_boot=1"
    assert boot[0x40:0x240] == k.CMDLINE.ljust(512, b"\0")
    assert image[0x40:0x240] == bytes(512) == d["gzip_boot"][0x40:0x240]
    assert diff(image[:2048], boot[:2048]) <= span(8, 4) | span(16, 4) | span(0x40, len(k.CMDLINE)) | span(0x240, 32)
    assert boot[0x240:0x260] == k.image_id([packed, EMPTY, b"", b"", dtb])
    assert d["expected_boot"] == boot + partition[len(boot):]

    # TOC1: the selector patch, the device-tree debug_mode value, 3 -> 0, and
    # the status of five USB host controllers, "okay" -> "late". Nothing else
    # in the dtb item changes; both checksums follow.
    package, old, previous = d["package"], d["original_package"], d["gzip_package"]
    items = {old[e:e + 64].split(b"\0")[0]: struct.unpack_from("<2I", old, e + 64) for e in range(64, 64 + 4 * 368, 368)}
    start, size = items[b"dtb"]
    tree = old[start:start + size]
    debug, value = fdt_edit.find(tree, "platform", "debug_mode")
    assert value == bytes([0, 0, 0, 3]) and fdt_edit.find(d["package_dtb"], "platform", "debug_mode") == (debug, bytes(4))
    held = set()
    assert len(k.LATE_USB) == 5 and k.KEPT_USB not in k.LATE_USB
    for node in k.LATE_USB:
        at, status = fdt_edit.find(tree, node, "status")
        assert status == b"okay\0" and fdt_edit.find(d["package_dtb"], node, "status") == (at, b"late\0")
        held |= span(at, 4)
    assert fdt_edit.find(d["package_dtb"], k.KEPT_USB, "status")[1] == b"okay\0"
    # Every other host controller node keeps the status the vendor gave it.
    for path, name, at, status in fdt_edit.walk(tree):
        if name == "status" and "hci" in path and path.split("/")[-1] not in k.LATE_USB:
            assert fdt_edit.find(d["package_dtb"], path.split("/")[-1], "status") == (at, status)
    # 60 Hz panel timing: lcd_ht and lcd_vt only, and only on profiled targets.
    panel = set()
    for prop, (stock, sixty) in p.get("panel_timing", {}).items():
        at, current = fdt_edit.find(tree, k.PANEL_NODE, prop)
        assert current == struct.pack(">I", stock) and fdt_edit.find(d["package_dtb"], k.PANEL_NODE, prop) == (at, struct.pack(">I", sixty))
        panel |= {i for i in span(at, 4) if tree[i] != d["package_dtb"][i]}
    assert bool(panel) == bool(p.get("panel_timing"))
    fast_v1 = d["fast_v1_package"]
    assert k.sha(fast_v1) == FAST_V1[target]
    assert diff(fast_v1, package) <= span(20, 4) | {start + i for i in panel} and (panel or fast_v1 == package)
    tree_edits = {debug + 3} | held | panel
    assert d["package_dtb"] == package[start:start + size] and diff(tree, d["package_dtb"]) == tree_edits
    selector = p["uboot_offset"] + p["selector_offset"]
    gzip_only = span(20, 4) | span(p["uboot_offset"] + 12, 4) | span(selector, 4)
    in_package = {start + at for at in tree_edits}
    assert diff(old, previous) <= gzip_only and diff(old, package) <= gzip_only | in_package
    assert in_package <= diff(previous, package) <= span(20, 4) | in_package
    assert package[selector:selector + 4] == previous[selector:selector + 4] == bytes.fromhex("40f20123")
    assert d["uboot"] == package[p["uboot_offset"]:][:p["uboot_size"]] and k.checksum(d["uboot"], 12) == word(d["uboot"], 12)
    assert k.checksum(previous, 20) == word(previous, 20) and k.checksum(package, 20) == word(package, 20)
    # Every edit is a whole aligned word, so the checksum moves by the sum of
    # the differences between the new and old little-endian words.
    delta = 0
    for at in sorted({a & ~3 for a in tree_edits}):
        assert (start + at) % 4 == 0
        delta += word(d["package_dtb"], at) - word(tree, at)
    assert word(package, 20) == (word(previous, 20) + delta) & 0xFFFFFFFF
    rejects("matched 0", fdt_edit.find, tree, "platform", "no_such_property")
    rejects("offsets", fdt_edit.replace, bytearray(tree), "platform", "debug_mode", value, bytes(8))
    rejects("device tree", fdt_edit.find, b"\0" + tree[1:], "platform", "debug_mode")

    # boot0: one header byte and its checksum. Everything past the first
    # sector, which is all a card keeps unchanged, stays as prepared.
    boot0, first = d["boot0"], d["original_boot0"]
    assert first == get(prefix, 8192, 65536) and k.sha(first) == p["boot0_sha256"]
    assert len(boot0) == 65536 == word(boot0, 0x10) and boot0[4:12] == b"eGON.BT0"
    assert (first[0x34], boot0[0x34]) == (3, 0)
    assert k.checksum(boot0, 12) == word(boot0, 12) == (word(first, 12) - 3) & 0xFFFFFFFF
    assert {0x34} <= diff(first, boot0) <= {12, 13, 14, 15, 0x34} and boot0[512:] == first[512:]

    # The previous shipped pair is still derived byte for byte, and the fast
    # pair equals what was booted on hardware.
    assert (k.sha(previous), k.sha(d["gzip_boot"])) == GZIP_V1[target]
    assert d["gzip_boot"][2048:2048 + word(d["gzip_boot"], 8)] == packed
    proven = (k.sha(package), k.sha(boot)) == PROVEN.get(target)
    assert proven or target not in PROVEN

    # Corruption must be rejected against the unchanged trust anchor, without
    # touching the prepared source. A sparse file needs only the audited regions.
    at_boot0, at_package, at_boot = k.BOOT0_OFFSET, p["package_offset"], p["boot_partition_start"] * 512
    originals = ((at_boot0, first), (at_package, old), (at_boot, partition))
    with tempfile.TemporaryDirectory() as tmp:
        damaged = Path(tmp) / "prefix"
        damaged.touch()
        for offset, blob in originals:
            put(damaged, offset, blob)

        def flipped(offset):
            put(damaged, offset, bytes([get(damaged, offset, 1)[0] ^ 1]))

        for offset, region in ((at_boot + 2048, "boot partition"), (at_boot + 0x40, "boot partition"),
                               (at_package + selector, "TOC1"), (at_package + start + debug + 3, "TOC1"),
                               (at_boot0 + 0x34, "boot0"), (at_boot0 + 65535, "boot0")):
            flipped(offset)
            rejects("unknown original " + region, k.derive, target, damaged)
            flipped(offset)

        # Firmware the catalog trusts is still refused when a value the patch
        # relies on differs: a debug_mode other than 3 (in the device tree or
        # boot0), a stale boot0 checksum, a command line already in use.
        def repinned(key, offset, blob, error):
            catalog = json.loads(k.CATALOG.read_text())
            catalog["profiles"][target][key] = k.sha(blob)
            (Path(tmp) / "catalog.json").write_text(json.dumps(catalog))
            put(damaged, offset, blob)
            rejects(error, k.derive, target, damaged, Path(tmp) / "catalog.json")

        for level in (0, 1):
            loud = bytearray(old)
            struct.pack_into(">I", loud, start + debug, level)
            struct.pack_into("<I", loud, 20, k.checksum(loud, 20))
            repinned("package_sha256", at_package, loud, "platform:debug_mode")
        put(damaged, at_package, old)
        quiet = bytearray(first)
        quiet[0x34] = 0
        repinned("boot0_sha256", at_boot0, quiet, "boot0 checksum")
        struct.pack_into("<I", quiet, 12, k.checksum(quiet, 12))
        repinned("boot0_sha256", at_boot0, quiet, "boot0 debug_mode")
        put(damaged, at_boot0, first)
        argued = bytearray(partition)
        argued[0x40] = ord("x")
        repinned("boot_partition_sha256", at_boot, argued, "command line is not empty")
        put(damaged, at_boot, partition)

        # apply() writes nothing unless all three pinned originals are present,
        # then installs boot0, package and image; verify() checks every region.
        with patch.object(k, "derive", return_value=d):
            for offset in (at_boot0 + 0x34, at_package + selector, at_boot + 2048):
                flipped(offset)
                rejects("pinned original", k.apply, target, prefix, damaged)
                flipped(offset)
                assert all(get(damaged, at, len(blob)) == blob for at, blob in originals)
            k.apply(target, prefix, damaged)
            for offset, blob in ((at_boot0, boot0), (at_package, package), (at_boot, d["expected_boot"])):
                assert get(damaged, offset, len(blob)) == blob
            for offset in (at_boot0 + 0x34, at_boot0 + 65535, at_package + start + debug + 3, at_boot + 0x40,
                           at_boot + len(boot)):
                flipped(offset)
                rejects("differs from derivation", k.verify, target, prefix, damaged)
                flipped(offset)
            k.verify(target, prefix, damaged)

            # prepare() ships the pair and manifests; boot0 is described, not shipped.
            out = Path(tmp) / "out"
            expected = dict(
                target=target, package_offset=at_package, package_size=len(package),
                boot_start=p["boot_partition_start"], boot_sectors=p["boot_partition_sectors"],
                boot_image_size=len(boot), original_package_sha256=p["package_sha256"],
                original_boot_sha256=p["boot_partition_sha256"], package_sha256=k.sha(package),
                boot_image_sha256=k.sha(boot), boot_partition_sha256=k.sha(d["expected_boot"]),
                kernel_sha256=k.sha(d["kernel"]), kernel_size=len(d["kernel"]),
                gzip_package_sha256=GZIP_V1[target][0], fast_v1_package_sha256=FAST_V1[target], gzip_boot_image_size=len(d["gzip_boot"]),
                gzip_boot_image_sha256=GZIP_V1[target][1], original_boot_image_size=len(image),
                original_boot_image_sha256=k.sha(image), boot0_offset=8192, boot0_size=65536,
                boot0_debug_offset=52, boot0_original_sha256=p["boot0_sha256"], boot0_body_offset=512,
                boot0_body_sha256=k.sha(first[512:]))
            assert k.prepare(target, prefix, out) == expected == json.loads((out / "manifest.json").read_text())
            assert (out / "manifest").read_text() == "".join(f"BOOT_{key.upper()}={value}\n" for key, value in expected.items())
            assert sorted(f.name for f in out.iterdir()) == ["boot-package.bin", "boot.img", "manifest", "manifest.json"]
            assert (out / "boot.img").read_bytes() == boot and (out / "boot-package.bin").read_bytes() == package
            rejects("unsafe shell manifest value", k.prepare, target + ";reboot", prefix, Path(tmp) / "unsafe")
            assert not (Path(tmp) / "unsafe/manifest").exists()
    print(f"{target}: {len(image)} -> {len(d['gzip_boot'])} -> {len(boot)} bytes; previous pair, patch bounds, "
          f"corruption gates PASS{'; matches the RG SP hardware bytes' if proven else ''}")


catalog = json.loads(k.CATALOG.read_text())
assert set(catalog["profiles"]) == set(GZIP_V1) == {p["id"] for p in load_profiles()}
for target in sys.argv[1:] or catalog["profiles"]:
    check(target)
