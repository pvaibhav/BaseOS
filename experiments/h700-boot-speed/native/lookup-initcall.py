#!/usr/bin/env python3
"""Resolve a GP14 initcall witness using the inspected vendor Image's relocations."""
import argparse
import bisect
import hashlib
import json
import struct
from pathlib import Path


KERNEL_SHA256 = "1332da28ef5dd078963c98cc4e11de816b8fddc7c7b75dab93b79eda68d01bf0"
SYMBOLS_SHA256 = "9c01c0d4ce35eb1ac282d4a05063a099ee56bb32b199b495d0902b2a3f94f4b3"
IMAGE_PA = 0x40080000
VA_OFFSET = 0xffffff7fc8000000
FAULT_OFFSET = 0x4098d640 - IMAGE_PA
FAULT_BYTES = 0x9c0
INITCALL_HOOK = 0x3954
ORIGINAL_HOOK = 0xa9b77bfd
DIAGNOSTIC_HOOK = 0x1424274b


def require(condition, message):
    if not condition:
        raise ValueError(message)


def read_kernel(path):
    kernel = path.read_bytes()
    digest = hashlib.sha256(kernel).hexdigest()
    if digest == KERNEL_SHA256:
        return kernel, digest, "pinned original Image"
    manifest_path = path.with_name("manifest.json")
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text())
        require(manifest.get("kernel_sha256") == digest, "kernel SHA256 does not match adjacent manifest")
        require(manifest.get("source_kernel_sha256") == KERNEL_SHA256,
                "manifest does not identify the pinned source kernel")
    require(len(kernel) >= FAULT_OFFSET + FAULT_BYTES, "kernel is too short for the known diagnostic layout")
    original = bytearray(kernel)
    hook = struct.unpack_from("<I", original, INITCALL_HOOK)[0]
    require(hook in (ORIGINAL_HOOK, DIAGNOSTIC_HOOK), "unrecognized do_one_initcall patch")
    original[FAULT_OFFSET:FAULT_OFFSET + FAULT_BYTES] = bytes(FAULT_BYTES)
    struct.pack_into("<I", original, INITCALL_HOOK, ORIGINAL_HOOK)
    require(hashlib.sha256(original).hexdigest() == KERNEL_SHA256,
            "restoring known diagnostic padding/hook does not reconstruct the pinned Image")
    return bytes(original), digest, "known diagnostic patches reconstruct pinned Image"


def read_symbols(path):
    raw = path.read_bytes()
    require(hashlib.sha256(raw).hexdigest() == SYMBOLS_SHA256,
            "symbols SHA256 does not match the inspected kernel-kallsyms.txt snapshot")
    symbols, functions = {}, {}
    for line in raw.decode().splitlines():
        fields = line.split()
        if len(fields) == 3:
            address, kind, name = fields
            symbols[name] = int(address, 16)
            if kind in "tTwW":
                functions.setdefault(int(address, 16), name)
    return symbols, functions


def initcalls(kernel, symbols, functions):
    start, end = symbols["__initcall_start"], symbols["__initcall_end"]
    require((end - start) // 8 == 743 and (end - start) % 8 == 0,
            "unexpected pinned initcall table bounds")
    levels = [(start, "early")]
    levels += [(symbols[f"__initcall{level}_start"], str(level)) for level in range(8)]
    levels.append((symbols["__initcallrootfs_start"], "rootfs"))
    levels.sort()
    level_starts = [address for address, _ in levels]
    relocations = {}
    position = 0
    while True:
        position = kernel.find(struct.pack("<Q", 0x403), position)
        if position < 0:
            break
        if position >= 8 and position + 16 <= len(kernel) and position % 8 == 0:
            place, _, addend = struct.unpack_from("<3Q", kernel, position - 8)
            if start <= place < end:
                require(place % 8 == 0 and place not in relocations, "invalid or duplicate initcall relocation")
                relocations[place] = (addend, position - 8)
        position += 1
    require(len(relocations) == 743, "expected 743 R_AARCH64_RELATIVE initcall relocations")
    rows = []
    for index, place in enumerate(range(start, end, 8)):
        require(struct.unpack_from("<Q", kernel, place - VA_OFFSET - IMAGE_PA)[0] == 0,
                "pinned initcall slot must be populated by relocation")
        target, relocation_offset = relocations[place]
        require(target in functions, f"initcall {index} relocation has no exact function symbol")
        level_start, level = levels[bisect.bisect_right(level_starts, place) - 1]
        rows.append({"name": functions[target], "pa": hex(target - VA_OFFSET), "va": hex(target),
                     "low32": hex(target & 0xffffffff), "index": index, "level": level,
                     "index_in_level": (place - level_start) // 8, "table_va": hex(place),
                     "relocation_file_offset": hex(relocation_offset), "relocation_type": "R_AARCH64_RELATIVE"})
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("kernel", type=Path)
    parser.add_argument("symbols", type=Path)
    parser.add_argument("value", type=lambda value: int(value, 0), help="GP14 low32 VA, full VA, or physical address")
    parser.add_argument("--table-json", type=Path, help="also write all 743 relocated initcalls and input bindings")
    args = parser.parse_args()
    try:
        require(0 <= args.value < 1 << 64, "witness value must fit in 64 bits")
        kernel, digest, binding = read_kernel(args.kernel)
        symbols, functions = read_symbols(args.symbols)
        rows = initcalls(kernel, symbols, functions)
        matches = [row for row in rows if args.value in (int(row["pa"], 16), int(row["va"], 16), int(row["low32"], 16))]
        require(bool(matches), f"0x{args.value:x} is not an exact initcall target in the pinned kernel")
        if args.table_json:
            args.table_json.write_text(json.dumps({"kernel_sha256": digest, "source_kernel_sha256": KERNEL_SHA256,
                "symbols_sha256": SYMBOLS_SHA256, "binding": binding, "index_base": 0, "initcalls": rows}, indent=2) + "\n")
        for row in matches:
            print(f"{row['name']} PA={row['pa']} VA={row['va']} "
                  f"initcall={row['index']} level={row['level']}[{row['index_in_level']}]")
    except (OSError, ValueError, KeyError, struct.error) as error:
        parser.error(str(error))


if __name__ == "__main__":
    main()
