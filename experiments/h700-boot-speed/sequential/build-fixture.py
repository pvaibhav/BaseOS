#!/usr/bin/env python3
"""Build one host-only rcS fixture for disk/RAM library comparisons."""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
FINAL_LINE = "\n: # rcS must succeed; init proceeds to respawn frontend-session\n"
LIBRARY_BLOCK = """usr_lib_image=/usr/share/baseos/usr-lib.sqfs
if [ -f "$usr_lib_image" ]; then
\t/bin/mount -t squashfs -o loop,ro,nodev,nosuid "$usr_lib_image" /usr/lib 2>/dev/null \\
\t\t|| echo 'baseos: the /usr/lib image did not mount; no dynamic program can start' > /dev/kmsg
fi
"""

FIXTURE_BLOCK = r'''# LAB sequential library fixture begin
usr_lib_image=/usr/share/baseos/usr-lib.sqfs
lab_ram_libs_marker=/etc/baseos-lab-ram-libs
lab_ram_libs_dir=/usr/share/baseos/lab-ram-libs
lab_ram_libs_mode=baseline
lab_ram_libs_status=default
lab_ram_libs_backing=disk

lab_ram_libs_log() {
	lab_ram_libs_up=unavailable
	lab_ram_libs_sectors=unavailable
	lab_ram_libs_busy=unavailable
	[ ! -r /proc/uptime ] || read -r lab_ram_libs_up _ < /proc/uptime
	[ ! -r /sys/block/mmcblk0/stat ] || read -r _ _ lab_ram_libs_sectors _ _ _ _ _ _ lab_ram_libs_busy _ < /sys/block/mmcblk0/stat
	printf 'baseos-lab-ram-libs phase=%s mode=%s status=%s backing=%s uptime=%s card_read_sectors=%s card_busy_ms=%s\n' \
		"$1" "$lab_ram_libs_mode" "$lab_ram_libs_status" "$lab_ram_libs_backing" \
		"$lab_ram_libs_up" "$lab_ram_libs_sectors" "$lab_ram_libs_busy" > /dev/kmsg
}

if [ -e "$lab_ram_libs_marker" ] || [ -L "$lab_ram_libs_marker" ]; then
	lab_ram_libs_requested=invalid
	if [ -f "$lab_ram_libs_marker" ] && [ ! -L "$lab_ram_libs_marker" ]; then
		IFS= read -r lab_ram_libs_requested < "$lab_ram_libs_marker" || :
	fi
	# Consume and persist the request before a copy or RAM mount can hang.
	if /bin/rm -f "$lab_ram_libs_marker" 2>/dev/null; then
		if /bin/sync; then
			case "$lab_ram_libs_requested" in
				baseline|ram) lab_ram_libs_mode=$lab_ram_libs_requested; lab_ram_libs_status=selected ;;
				*) lab_ram_libs_status=invalid-marker ;;
			esac
		else
			lab_ram_libs_status=marker-sync-failed
		fi
	else
		lab_ram_libs_status=marker-remove-failed
	fi
fi

lab_ram_libs_log start
if [ -f "$usr_lib_image" ]; then
	if [ "$lab_ram_libs_mode" = ram ]; then
		lab_ram_libs_status=mkdir-failed
		if /bin/mkdir -p "$lab_ram_libs_dir" 2>/dev/null; then
			lab_ram_libs_status=tmpfs-failed
			if /bin/mount -t tmpfs -o mode=700,size=64m,nodev,nosuid tmpfs "$lab_ram_libs_dir" 2>/dev/null; then
				lab_ram_libs_status=copy-failed
				if /bin/cp "$usr_lib_image" "$lab_ram_libs_dir/usr-lib.sqfs" 2>/dev/null; then
					lab_ram_libs_status=ram-mount-failed
					if /bin/mount -t squashfs -o loop,ro,nodev,nosuid "$lab_ram_libs_dir/usr-lib.sqfs" /usr/lib 2>/dev/null; then
						lab_ram_libs_backing=ram
						lab_ram_libs_status=ram-mounted
					fi
				fi
				if [ "$lab_ram_libs_backing" != ram ]; then
					/bin/rm -f "$lab_ram_libs_dir/usr-lib.sqfs" 2>/dev/null
					/bin/umount "$lab_ram_libs_dir" 2>/dev/null
				fi
			fi
		fi
	fi
	if [ "$lab_ram_libs_backing" != ram ]; then
		if /bin/mount -t squashfs -o loop,ro,nodev,nosuid "$usr_lib_image" /usr/lib 2>/dev/null; then
			[ "$lab_ram_libs_mode" != ram ] || lab_ram_libs_status=fallback-$lab_ram_libs_status
		else
			lab_ram_libs_backing=none
			lab_ram_libs_status=disk-mount-failed
			echo 'baseos: the /usr/lib image did not mount; no dynamic program can start' > /dev/kmsg
		fi
	fi
else
	lab_ram_libs_backing=directory
	lab_ram_libs_status=missing-image
fi
lab_ram_libs_log end
# LAB sequential library fixture end
'''


def sampler_from_source(source: str) -> str:
    """Read the existing literal without importing its device-control module."""
    values = [
        node.value.value
        for node in ast.parse(source).body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "SAMPLER" for target in node.targets)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    ]
    if len(values) != 1:
        raise ValueError("expected one literal SAMPLER in first-frame.py")
    return values[0]


def build_fixture(source: str, sampler: str) -> str:
    if "LAB:" in source or "baseos-lab-ram-libs" in source:
        raise ValueError("source rcS already contains lab instrumentation")
    if source.count(LIBRARY_BLOCK) != 1:
        raise ValueError("unknown /usr/lib mount block; expected the exact production block once")
    if source.count(FINAL_LINE) != 1 or not source.endswith(FINAL_LINE):
        raise ValueError("expected one final production rcS success line")
    fixture = source.replace(LIBRARY_BLOCK, FIXTURE_BLOCK, 1)
    return fixture.replace(FINAL_LINE, sampler + FINAL_LINE, 1)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--sampler-source", type=Path, default=HERE.parent / "first-frame.py")
    args = parser.parse_args()
    source = args.source.read_bytes()
    sampler_source = args.sampler_source.read_bytes()
    try:
        fixture = build_fixture(source.decode(), sampler_from_source(sampler_source.decode())).encode()
    except (ValueError, SyntaxError) as error:
        parser.error(str(error))
    # Refuse overwrites, including the supplied source or an already reviewed fixture.
    with args.output.open("xb") as stream:
        stream.write(fixture)
    args.output.chmod(0o755)
    print(json.dumps({
        "source": str(args.source), "source_sha256": hashlib.sha256(source).hexdigest(),
        "sampler_source": str(args.sampler_source),
        "sampler_source_sha256": hashlib.sha256(sampler_source).hexdigest(),
        "fixture": str(args.output), "fixture_sha256": hashlib.sha256(fixture).hexdigest(),
        "marker": "/etc/baseos-lab-ram-libs", "modes": ["baseline", "ram"],
        "tmpfs": "/usr/share/baseos/lab-ram-libs", "tmpfs_size": "64m",
        "default": "baseline", "device_access": False,
    }, indent=2))


if __name__ == "__main__":
    main()
