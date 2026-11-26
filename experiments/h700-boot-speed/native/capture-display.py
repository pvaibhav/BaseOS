#!/usr/bin/env python3
"""Capture fb0 memory and the display engine's writeback on a pinned lab device."""
import argparse
import json
import struct
import subprocess
import sys
import uuid
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from display_metrics import compare_pixels
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import lab
from lab_devices import PROFILES


def png(path, raw, width, height, stride, yoffset=0):
    lab.require(len(raw) >= stride * (yoffset + height) and stride >= width * 4, "short pixel buffer")
    rows = []
    for y in range(yoffset, yoffset + height):
        line = raw[y * stride:y * stride + width * 4]
        rows.append(b"\0" + b"".join(bytes((line[x + 2], line[x + 1], line[x])) for x in range(0, len(line), 4)))
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xffffffff)
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(b"".join(rows))) + chunk(b"IEND", b""))


def capture(output):
    lab.require(not output.exists(), "choose a fresh capture directory")
    lab.require(lab.TARGET in PROFILES and lab.SERIAL == PROFILES[lab.TARGET]["serial"], "explicit lab identity required")
    lab.identity()
    output.mkdir(parents=True)
    before = lab.sh("cat /proc/sys/kernel/random/boot_id")
    values = lab.sh("cat /sys/class/disp/disp/attr/xres /sys/class/disp/disp/attr/yres;"
                    " cat /sys/class/graphics/fb0/stride /sys/class/graphics/fb0/pan").splitlines()
    lab.require(len(values) == 4, "incomplete display geometry")
    width, height, stride = map(int, values[:3])
    xoffset, yoffset = map(int, values[3].split(","))
    lab.require(0 < width <= 1920 and 0 < height <= 1920 and xoffset == 0 and yoffset >= 0, "unsupported display geometry")
    remote = "/tmp/baseos-scanout-" + uuid.uuid4().hex
    state = lab.sh("cat /sys/class/disp/disp/attr/sys")
    (output / "display-before.txt").write_text(state + "\n")
    # Sysfs consumes each write as a request. BusyBox printf may split the
    # filename and newline into separate writes, so submit one bounded block.
    request = output / "request.txt"
    request.write_text(remote + ".argb8888\n")
    subprocess.run(lab.ADB + ["push", str(request), remote + ".request"], check=True, capture_output=True)
    lab.sh(f"dd if='{remote}.request' of=/sys/class/disp/disp/attr/capture_dump bs=512 count=1 2>/dev/null", 15)
    (output / "dmesg.txt").write_text(lab.sh("dmesg") + "\n")
    # The BSP sysfs store returns success even on internal failure. Validate the
    # exact fresh output and retain its kernel messages instead of trusting it.
    lab.require(int(lab.sh(f"stat -c %s '{remote}.argb8888'")) == width * height * 4, "scanout capture is incomplete")
    lab.sh(f"dd if=/dev/fb0 of='{remote}.fb' bs=4096 2>/dev/null", 20)
    for name, suffix in (("scanout.bin", ".argb8888"), ("framebuffer.bin", ".fb")):
        subprocess.run(lab.ADB + ["pull", remote + suffix, str(output / name)], check=True, capture_output=True, timeout=30)
    after = lab.sh("cat /proc/sys/kernel/random/boot_id")
    (output / "dmesg.txt").write_text(lab.sh("dmesg") + "\n")
    (output / "display-after.txt").write_text(lab.sh("cat /sys/class/disp/disp/attr/sys") + "\n")
    lab.require(before == after, "device rebooted during display capture")
    raw, fb = (output / "scanout.bin").read_bytes(), (output / "framebuffer.bin").read_bytes()
    png(output / "scanout.png", raw, width, height, width * 4)
    png(output / "framebuffer.png", fb, width, height, stride, yoffset)
    report = {"target": lab.TARGET, "serial": lab.SERIAL, "boot_id": before,
              "width": width, "height": height, "fb_stride": stride, "fb_yoffset": yoffset,
              "scanout_sha256": lab.sha(raw), "framebuffer_sha256": lab.sha(fb),
              "pixel_validation": compare_pixels(raw, fb, width, height, stride, yoffset),
              "scope": "DE writeback pixels and fb0 memory; downstream panel output still requires physical observation"}
    (output / "capture.json").write_text(json.dumps(report, indent=2) + "\n")
    lab.sh(f"rm -f '{remote}.argb8888' '{remote}.fb' '{remote}.request'")
    print(json.dumps(report, indent=2))
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    capture(parser.parse_args().output.resolve())
