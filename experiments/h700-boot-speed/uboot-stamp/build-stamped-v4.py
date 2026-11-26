#!/usr/bin/env python3
"""Keep pre-logo stamps away from the bitmap load at 0x40000000.

The v3 block was erased by the very operation it measured. Use an otherwise
unused DRAM page instead; the same final stub carries it into the FDT. Build
and execute every stub and the real vendor bootm flow before emitting a report.
Only the pinned RG34XX SP package is supported.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("stamp_v3", HERE / "build-stamped-v3.py")
v3 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(v3)

v3.v1.STAMP_BASE = v3.STAMP_BASE = 0x4FFF0100
v3.SCRATCH = v3.STAMP_BASE - 8
OUT = v3.v1.REPO / "work/h700-loader/stamp-v4"
OUT.mkdir(parents=True, exist_ok=True)
v3.HERE = OUT


if __name__ == "__main__":
    info, uboot = v3.build()
    old = OUT / "boot-package-stamped-v3.bin"
    output = OUT / "boot-package-stamped-v4.bin"
    old.replace(output)
    info["output"] = output.name
    info["scratch"] = hex(v3.SCRATCH)
    info["verification"] = v3.verify(uboot, info)
    (OUT / "stamps-v4.json").write_text(json.dumps(info, indent=2) + "\n")
    print(json.dumps({key: info[key] for key in
                      ("output", "scratch", "patched_package_sha256", "verification")}, indent=2))
