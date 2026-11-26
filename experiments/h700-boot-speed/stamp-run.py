#!/usr/bin/env python3
"""Install a counter-stamped U-Boot package on the lab RG34XX SP, restart, read the stamps.

Uses the lab tooling of the boot-speed worktree for identity checks, verified
region writes and the safe restart. The stamped package comes from
work/rg34xxsp-stamp/ (built and verified under Unicorn first).

    . work/lab/rg34sp.env   (in the boot-speed worktree) then
    python3 stamp-run.py install    write the stamped package (after comparing the card with the build)
    python3 stamp-run.py restart    restart the device the safe way
    python3 stamp-run.py read       read the stamps and the boot log, print the split
    python3 stamp-run.py restore    write the build's own package back
"""
from __future__ import annotations

import importlib.util
import json
import os
import struct
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
LAB_PY = HERE.parents[2] / "boot-speed/experiments/h700-boot-speed/lab.py"
STAMP_DIR = HERE.parents[1] / "work/rg34xxsp-stamp"
BUILD_PACKAGE = HERE.parents[1] / "work/rg34xxsp/boot-gzip/boot-package.bin"
STAMP_BASE = 0x40000100
COUNTER_HZ = 24_000_000
SLOTS = ["board_init_r sequence starts", "power/PMIC init starts", "after power init",
         "display driver init starts", "after display init and stdio", "flash hook / burn key",
         "initr_sunxi_flash (card init, IR, key)", "after flash init", "boot0 update check",
         "board_late_init starts (logo next)", "logo routine returned", "main_loop entered",
         "card read starts", "card read done, bootm", "kernel unpacked, prep", "kernel jump"]

spec = importlib.util.spec_from_file_location("lab", LAB_PY)
lab = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lab)


def sha(blob: bytes) -> str:
    return lab.sha(blob)


def package_path() -> Path:
    name = sys.argv[2] if len(sys.argv) > 2 else "boot-package-stamped-v2.bin"
    return STAMP_DIR / name


def install() -> None:
    lab.identity()
    stamped = package_path().read_bytes()
    build = BUILD_PACKAGE.read_bytes()
    info = json.loads((STAMP_DIR / "stamps.json").read_text())
    lab.require(len(stamped) == 1310720, "stamped package has the wrong size")
    lab.require(info["original_package_sha256"] == sha(build), "stamped package was not derived from the build's package")
    on_card = lab.device_sha("toc1", len(build))
    if on_card == sha(stamped):
        print("stamped package already on the card")
        return
    known = {sha(build)} | {sha(path.read_bytes()) for path in STAMP_DIR.glob("boot-package-stamped*.bin")}
    lab.require(on_card in known, f"card holds an unexpected boot package {on_card[:16]}; not touching it")
    lab.write_region("toc1", stamped)
    print("installed; restart with: stamp-run.py restart")


def restore() -> None:
    lab.identity()
    build = BUILD_PACKAGE.read_bytes()
    if lab.device_sha("toc1", len(build)) == sha(build):
        print("build package already on the card")
        return
    lab.write_region("toc1", build)
    print("restored the build's package; restart with: stamp-run.py restart")


def read() -> None:
    """The stamps sit in the last 64 bytes of the device tree the kernel received (v2 package)."""
    dump = subprocess.run(lab.ADB + ["shell", "od -An -tx1 -v /sys/firmware/fdt"], check=True, capture_output=True, timeout=120).stdout
    tree = bytes.fromhex("".join(dump.decode().split()))
    total = struct.unpack(">I", tree[4:8])[0]
    lab.require(len(tree) == total, f"fdt read {len(tree)} bytes, header says {total}")
    at = tree.rfind(b"MATS", max(0, total - 16384))
    lab.require(at >= 0, "stamp magic not found in the tail of the device tree")
    words = struct.unpack("<34I", tree[at:at + 136])
    magic, stamps = words[0], [(words[2 + 2 * n] | words[3 + 2 * n] << 32) for n in range(16)]
    log = lab.sh("grep -h -e 'boot stages' -e 'Startup: .* before Linux' /mnt/sdcard/baseos-boot.log /mnt/SDCARD/baseos-boot.log 2>/dev/null | tail -1; cat /proc/sys/kernel/random/boot_id", 30)
    print("magic", f"{magic:#x}", "(expected 0x5354414d)")
    print("boot log:", log.strip())
    previous = None
    for name, value in zip(SLOTS, stamps):
        delta = "" if previous is None or not value else f"  +{(value - previous) / COUNTER_HZ * 1000:8.1f} ms"
        print(f"  {name:34s} {value / COUNTER_HZ:9.4f} s{delta}")
        if value:
            previous = value
    pre_kernel = None
    for token in log.split():
        pass
    import re
    # 1.5.2 and later: "Startup: X s before Linux, ..."; earlier: "pre-kernel X s".
    m = re.search(r"pre-kernel ([0-9.]+) s", log) or re.search(r"Startup: ([0-9.]+) s before Linux", log)
    if m and previous:
        pre_kernel = float(m.group(1))
        print(f"  kernel clock origin (boot log)      {pre_kernel:9.4f} s  +{(pre_kernel - previous / COUNTER_HZ) * 1000:8.1f} ms")


def main() -> int:
    command = sys.argv[1] if len(sys.argv) > 1 else "read"
    {"install": install, "restore": restore, "restart": lab.reboot, "read": read}[command]()
    return 0


if __name__ == "__main__":
    sys.exit(main())
