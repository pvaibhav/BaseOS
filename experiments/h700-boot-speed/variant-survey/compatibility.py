#!/usr/bin/env python3
"""Generate the replacement-loader contract from all eleven prepared firmwares."""
import argparse
import hashlib
import json
import re
import struct
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE.parent))
import fdt

TARGETS = ("rg28xx", "rg34xx", "rg34xxsp", "rg35xxh", "rg35xxplus", "rg35xxpro",
           "rg35xxsp", "rg40xxh", "rg40xxv", "rgcubexx", "rgsp")
TYPES = {3: "DDR3", 7: "LPDDR3", 8: "LPDDR4"}


def digest(blob):
    return hashlib.sha256(blob).hexdigest()


def parameters(boot0, offset):
    words = struct.unpack_from("<32I", boot0, offset)
    if words[0] not in (672, 792) or words[1] not in TYPES:
        raise ValueError(f"unknown DRAM candidate at {offset:#x}")
    return {"offset": hex(offset), "type": TYPES[words[1]], "clock_mhz": words[0],
            "words": [hex(word) for word in words]}


def generate(inventories):
    traces = {}
    evidence = ROOT / "experiments/h700-boot-speed/evidence"
    for level, name in (("high", "uboot-trace-all.json"), ("low", "uboot-trace-pe6-low.json")):
        for run in json.loads((evidence / name).read_text()):
            if run["jump"]["entry"] != 0x40080000:
                raise ValueError(f"{run['target']} vendor trace did not reach the kernel")
            token = re.search(r"(?:^| )lcd_type=([^ ]+)", run["bootargs"]).group(1)
            traces.setdefault(run["target"], {})[level] = token
    result = {"scope": "Firmware contracts; no claim of replacement-loader hardware support.",
              "targets": {}, "lab_devices": [],
              "release_gates": ["Runtime DRAM type, rank, width and size detection in SRAM",
                                "Bounded retry after failed DRAM training",
                                "Per-model panel behavior matches both vendor strap traces",
                                "PMIC detection and vendor monitor/standby ABI preserved",
                                "Complete boot and device health on both lab devices",
                                "Cold-boot coverage beyond warm SRAM trials"]}
    for target in TARGETS:
        prefix = (ROOT / "work" / target / "boot-prefix.img").read_bytes()
        boot0 = prefix[8192:8192 + 65536]
        if boot0[4:12] != b"eGON.BT0":
            raise ValueError(f"{target}: missing boot0")
        package = (ROOT / "work" / target / "boot-gzip/boot-package.bin").read_bytes()
        count = struct.unpack_from("<I", package, 32)[0]
        items = {}
        for index in range(count):
            start = 64 + 368 * index
            name = package[start:start + 64].split(b"\0", 1)[0].decode("ascii")
            offset, length = struct.unpack_from("<2I", package, start + 64)
            if name in items or offset + length > len(package):
                raise ValueError(f"{target}: invalid TOC1 item {name}")
            items[name] = package[offset:offset + length]
        tree, overlay = items["dtb"], items["dtbo"]
        panel = {name: value.rstrip(b"\0").decode("ascii") if name.endswith("name") else
                 struct.unpack(">I", value)[0] for path, name, _, value in fdt.walk(tree)
                 if path.endswith("lcd0@01c0c000") and name in ("lcd_driver_name", "lcd_x", "lcd_y")}
        pmics = sorted({value.rstrip(b"\0").decode("ascii") for blob in (tree, overlay)
                        for _, name, _, value in fdt.walk(blob)
                        if name == "compatible" and b"axp" in value.lower()})
        result["targets"][target] = {"boot0_sha256": digest(boot0), "package_sha256": digest(package),
            "dram_candidates": [parameters(boot0, offset) for offset in (0x38, 0x9f4, 0xa74)],
            "panel": panel, "vendor_pe6_lcd_type": traces[target], "pmic_compatibles": pmics,
            "monitor_sha256": digest(items["monitor"]), "overlay_sha256": digest(overlay)}
    for directory in inventories:
        report = json.loads((directory / "inventory.json").read_text())
        for name, info in report["files"].items():
            blob = (directory / name).read_bytes()
            if len(blob) != info["bytes"] or digest(blob) != info["sha256"]:
                raise ValueError(f"pinned inventory changed: {directory / name}")
        boot0 = (directory / "boot0.bin").read_bytes()
        selected = parameters(boot0, 0x38)
        if TYPES[report["dram"]["dram_type"]] != selected["type"]:
            raise ValueError("live FDT and trained boot0 disagree on DRAM type")
        result["lab_devices"].append({"target": report["target"], "serial": report["serial"],
            "boot_id": report["boot_id"], "selected_dram": selected, "panel": report["panel"],
            "lcd_type": report["lcd_type"], "meminfo": report["meminfo"],
            "egon_copies": report["egon_copies"], "inventory_sha256": digest((directory / "inventory.json").read_bytes())})
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--inventory", type=Path, action="append", default=[])
    args = parser.parse_args()
    report = generate(args.inventory)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({target: {"dram_types": sorted({candidate["type"] for candidate in entry["dram_candidates"]}),
                               "panel": entry["panel"], "lcd_type": entry["vendor_pe6_lcd_type"]}
                      for target, entry in report["targets"].items()}, indent=2))
