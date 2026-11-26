#!/usr/bin/env python3
"""Compare every live FDT property with its vendor package and verify DRAM bindings."""
import argparse
import hashlib
import json
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import fdt

# Preserve the vendor's legacy binding order, including its names for the
# newer H616 parameter layout. Both live devices expose this exact mapping.
DRAM_PROPERTIES = (["dram_clk", "dram_type", "dram_zq", "dram_odt_en", "dram_para1", "dram_para2"] +
                   [f"dram_mr{i}" for i in range(4)] + [f"dram_tpr{i}" for i in range(14)])


def inspect(directory):
    inventory = json.loads((directory / "inventory.json").read_text())
    package = (directory / "toc1.bin").read_bytes()
    entries = {}
    for index in range(struct.unpack_from("<I", package, 32)[0]):
        at = 64 + index * 368
        name = package[at:at + 64].split(b"\0", 1)[0].decode()
        offset, size = struct.unpack_from("<2I", package, at + 64)
        entries[name] = package[offset:offset + size]
    live = (directory / "live.dtb").read_bytes()
    before = {(path, name): value for path, name, _, value in fdt.walk(entries["dtb"])}
    after = {(path, name): value for path, name, _, value in fdt.walk(live)}
    words = struct.unpack_from("<32I", (directory / "boot0.bin").read_bytes(), 0x38)
    for index, name in enumerate(DRAM_PROPERTIES):
        assert after["/dram", name] == struct.pack(">I", words[index]), (inventory["target"], name)
    assert len(after["/memory@40000000", "reg"]) == 16
    base, size = struct.unpack(">2Q", after["/memory@40000000", "reg"])
    assert base == 0x40000000 and size in (0x40000000, 0x80000000)
    bootargs = after["/chosen", "bootargs"].rstrip(b"\0").decode()
    assert f"androidboot.serialno={inventory['serial']}" in bootargs.split()
    changes = []
    for path, name in sorted(before.keys() | after.keys()):
        old, new = before.get((path, name)), after.get((path, name))
        if old != new:
            changes.append({"path": path, "property": name,
                            "before_hex": old.hex() if old is not None else None,
                            "after_hex": new.hex() if new is not None else None})
    return {"target": inventory["target"], "serial": inventory["serial"],
            "package_sha256": hashlib.sha256(package).hexdigest(),
            "live_fdt_sha256": hashlib.sha256(live).hexdigest(),
            "properties_before": len(before), "properties_after": len(after),
            "dram_bindings_exact": True, "ram_bytes": size, "bootargs": bootargs, "changes": changes}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("inventories", nargs="+", type=Path)
    args = parser.parse_args()
    report = {"dram_parameter_binding": DRAM_PROPERTIES, "devices": list(map(inspect, args.inventories))}
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps([{key: row[key] for key in ("target", "dram_bindings_exact", "ram_bytes")}
                      for row in report["devices"]], indent=2))
