#!/usr/bin/env python3
"""Decode the bounded static Linux 4.9 printk ring exported in a recovery FDT."""
import argparse
import json
import re
import struct
from pathlib import Path


def read(tree, output):
    assert not output.exists()
    assert tree[:4] == bytes.fromhex("d00dfeed") and struct.unpack_from(">I", tree, 4)[0] == len(tree)
    candidates = []
    for found in re.finditer(b"BSKLOG01", tree):
        at = found.start()
        if at + 64 > len(tree): continue
        header = struct.unpack_from("<16I", tree, at)
        if header[2:5] == (1, 0x4000, 0x4116a7f0) and at + 64 + header[3] == len(tree):
            candidates.append((at, header))
    assert len(candidates) == 1, "missing or ambiguous recovery log export"
    at, header = candidates[0]
    ring = tree[at + 64:]
    first, end = header[5:7]
    sequence = header[8] | header[9] << 32
    next_sequence = header[10] | header[11] << 32
    messages, visited = [], set()
    offset = first
    error = None if first % 4 == end % 4 == 0 and 0 <= first < len(ring) and 0 <= end < len(ring) else "invalid ring bounds"
    while error is None and offset != end and len(messages) < 4096:
        if offset in visited or offset % 4 or not 0 <= offset <= len(ring) - 16:
            error = f"invalid ring index {offset:#x}"
            break
        visited.add(offset)
        ticks, length, text_length, dictionary_length, facility, flags = struct.unpack_from("<Q3H2B", ring, offset)
        if length == 0:
            offset = 0
            continue
        if length % 4 or length < 16 or offset + length > len(ring) or text_length + dictionary_length > length - 16:
            error = f"invalid record at {offset:#x}"
            break
        text = ring[offset + 16:offset + 16 + text_length].decode("utf-8", errors="replace")
        messages.append({"seconds": ticks / 1e9, "sequence": sequence, "text": text})
        sequence += 1
        offset += length
    report = {"export_found": True, "first_index": first, "next_index": end,
              "first_sequence": header[8] | header[9] << 32, "next_sequence": next_sequence,
              "rtc_gp15": hex(header[7]), "ring_consistent": error is None and sequence == next_sequence,
              "error": error, "records": len(messages),
              "warning": "Static ring only; reset may lose dirty cache lines. Multicore auto-expansion can move the active ring."}
    output.mkdir(parents=True)
    (output / "ring.bin").write_bytes(ring)
    (output / "records.json").write_text(json.dumps(messages, indent=2) + "\n")
    (output / "metadata.json").write_text(json.dumps(report, indent=2) + "\n")
    (output / "kernel.log").write_text("\n".join(f"[{message['seconds']:12.6f}] {message['text']}" for message in messages) + "\n")
    (output / "strings.txt").write_text("\n".join(match[0].decode("ascii") for match in re.finditer(rb"[\x20-\x7e]{8,}", ring)) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("fdt", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    read(args.fdt.read_bytes(), args.output)
