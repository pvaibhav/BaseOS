#!/usr/bin/env python3
"""Compare candidate, package, embedded and actual working kernel FDTs."""
import argparse
import hashlib
import json
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import fdt


def properties(blob): return {(path, name): value for path, name, _, value in fdt.walk(blob)}


def reservations(blob):
    at = struct.unpack_from(">I", blob, 16)[0]
    result = []
    while True:
        address, size = struct.unpack_from(">QQ", blob, at)
        at += 16
        if not address and not size: return result
        result.append([hex(address), hex(size)])


def census(target, reference, candidate, output):
    root = Path(__file__).resolve().parents[3]
    prepared = root / "work" / target / "boot-gzip"
    package = (prepared / "boot-package.bin").read_bytes()
    image = (prepared / "boot.img").read_bytes()
    for index in range(struct.unpack_from("<I", package, 32)[0]):
        at = 64 + index * 368
        if package[at:at + 64].split(b"\0")[0] == b"dtb":
            start, size = struct.unpack_from("<2I", package, at + 64)
            toc = package[start:start + size]
            break
    offset = 2048
    for field in (8, 16, 24, 1632): offset += (struct.unpack_from("<I", image, field)[0] + 2047) & ~2047
    embedded = image[offset:offset + struct.unpack_from("<I", image, 1648)[0]]
    blobs = {"prepared_toc": toc, "original_embedded": embedded,
             "working_reference": reference.read_bytes(), "native_candidate": candidate.read_bytes()}
    maps = {name: properties(blob) for name, blob in blobs.items()}
    different = []
    for key in sorted(set().union(*(mapping.keys() for mapping in maps.values()))):
        values = {name: mapping.get(key) for name, mapping in maps.items()}
        if len(set(values.values())) > 1:
            different.append({"path": key[0], "property": key[1],
                              "values_hex": {name: value.hex() if value is not None else None for name, value in values.items()},
                              "candidate_matches_reference": values["native_candidate"] == values["working_reference"]})
    report = {"premise": "Matching Image/FDT bytes and four argument registers suffices for vendor Linux entry.",
              "target": target, "sha256": {name: hashlib.sha256(blob).hexdigest() for name, blob in blobs.items()},
              "property_counts": {name: len(mapping) for name, mapping in maps.items()},
              "memory_reservations": {name: reservations(blob) for name, blob in blobs.items()},
              "candidate_reference_differences": sum(not row["candidate_matches_reference"] for row in different),
              "differences": different}
    output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({key: value for key, value in report.items() if key != "differences"}, indent=2))
    for row in different:
        if not row["candidate_matches_reference"]: print(row["path"], row["property"])


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("target")
    parser.add_argument("reference", type=Path)
    parser.add_argument("candidate", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    census(args.target, args.reference, args.candidate, args.output)
