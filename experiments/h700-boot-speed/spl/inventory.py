#!/usr/bin/env python3
"""Read and pin a lab device's current firmware before preparing SPL trials."""
import argparse
import json
import os
import re
import struct
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
DEVICES = {"rg34xxsp": "1c00103dc2840671ed3", "rg40xxv": "5c001040f4438852493",
           "rgsp": "ac001089c89588720d2"}


def snapshot(target: str, output: Path, ssh_host=None, known_hosts=None):
    if output.exists():
        raise SystemExit("choose a fresh inventory directory; pinned snapshots are never overwritten")
    os.environ.update(BOOT_LAB_TARGET=target, BOOT_LAB_SERIAL=DEVICES[target], BOOT_LAB_WORK=str(output))
    sys.path.insert(0, str(HERE.parent))
    import lab
    import fdt
    from kernel_gzip import checksum, word

    if ssh_host:
        assert known_hosts and known_hosts.is_file(), "supply the task's existing SSH host-key record"
        assert re.fullmatch(r"[A-Za-z0-9_.@-]+", ssh_host)
        ssh = ["sshpass", "-e", "ssh", "-o", f"UserKnownHostsFile={known_hosts.resolve()}",
               "-o", "StrictHostKeyChecking=yes", "-o", "ConnectTimeout=8", ssh_host]
        def remote(command, timeout=30):
            return subprocess.run(ssh + [command], check=True, capture_output=True, timeout=timeout).stdout
        def shell(command, timeout=30):
            return remote(command, timeout).decode().strip()
        def read_region(name, size=None):
            device, sector, limit = lab.REGIONS[name]
            size = size or limit
            assert 0 < size <= limit and size % 512 == 0
            blob = remote(f"dd if={device} bs=512 skip={sector} count={size // 512} 2>/dev/null", 300)
            lab.require(len(blob) == size, f"short SSH read of {name}")
            return blob
        # Bind SSH reads to the same model, serial, and partition geometry checks.
        lab.sh, lab.read_region = shell, read_region
    else:
        assert target != "rgsp", "RGSP inventory is authorized through explicit SSH only"
    lab.identity()
    output.mkdir(parents=True)
    boot_id = lab.sh("cat /proc/sys/kernel/random/boot_id")
    report = {"target": target, "serial": DEVICES[target], "boot_id": boot_id,
              "release": lab.sh("cat /etc/baseos-release"), "cmdline": lab.sh("cat /proc/cmdline"),
              "meminfo": lab.sh("head -5 /proc/meminfo"), "files": {}}

    def save(name, blob):
        (output / name).write_bytes(blob)
        report["files"][name] = {"bytes": len(blob), "sha256": lab.sha(blob)}

    for name, size in (("boot0", 65536), ("toc1", 1310720), ("env", 0x20000), ("boot", 64 * 1024 * 1024)):
        save(name + ".bin", lab.read_region(name, size))
    # This includes the actual GPT and all first-stage copies, rather than
    # assuming the prepared image's second eGON copy is a BROM fallback.
    if ssh_host:
        prefix = remote("dd if=/dev/mmcblk0 bs=512 count=73728 iflag=direct 2>/dev/null", 300)
    else:
        lab.sh("dd if=/dev/mmcblk0 of=/tmp/spl-prefix.bin bs=512 count=73728 iflag=direct 2>/dev/null", 120)
        subprocess.run(lab.ADB + ["pull", "/tmp/spl-prefix.bin", str(output / "prefix.bin")],
                       check=True, capture_output=True, timeout=300)
        lab.sh("rm -f /tmp/spl-prefix.bin")
        prefix = (output / "prefix.bin").read_bytes()
    lab.require(len(prefix) == 73728 * 512, "short prefix snapshot")
    save("prefix.bin", prefix)
    tree = bytes.fromhex(lab.sh("od -An -tx1 -v /sys/firmware/fdt", 60))
    lab.require(fdt.total_size(tree) == len(tree), "short live FDT snapshot")
    save("live.dtb", tree)
    report["dram"] = {name: struct.unpack(">I", value)[0] for path, name, _, value in fdt.walk(tree)
                      if path == "/dram" and name.startswith("dram_") and len(value) == 4}
    report["panel"] = {name: value.rstrip(b"\0").decode("ascii") if name.endswith("name") else
                       struct.unpack(">I", value)[0] for path, name, _, value in fdt.walk(tree)
                       if path.endswith("lcd0@01c0c000") and name in ("lcd_driver_name", "lcd_x", "lcd_y")}
    report["egon_copies"] = []
    for offset in range(0, len(prefix) - 0x20, 512):
        if prefix[offset + 4:offset + 12] != b"eGON.BT0":
            continue
        size = word(prefix, offset + 0x10)
        lab.require(size >= 512 and size % 4 == 0 and offset + size <= len(prefix), "invalid eGON extent")
        blob = prefix[offset:offset + size]
        report["egon_copies"].append({"sector": offset // 512, "bytes": size, "sha256": lab.sha(blob),
                                      "checksum_valid": checksum(blob, 12) == word(blob, 12),
                                      "dram_type": word(blob, 0x3c)})
    report["prepared_package_matches"] = report["files"]["toc1.bin"]["sha256"] == lab.sha(
        (ROOT / "work" / target / "boot-gzip/boot-package.bin").read_bytes())
    report["lcd_type"] = re.search(r"(?:^| )lcd_type=([^ ]+)", report["cmdline"]).group(1)
    lab.require(lab.sh("cat /proc/sys/kernel/random/boot_id") == boot_id, "device rebooted during inventory")
    (output / "inventory.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({key: report[key] for key in ("target", "serial", "dram", "panel", "lcd_type",
                                                  "egon_copies", "prepared_package_matches")}, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("target", choices=DEVICES)
    parser.add_argument("output", type=Path)
    parser.add_argument("--ssh", help="explicit SSH destination; password supplied through SSHPASS")
    parser.add_argument("--known-hosts", type=Path)
    args = parser.parse_args()
    snapshot(args.target, args.output.resolve(), args.ssh, args.known_hosts)
