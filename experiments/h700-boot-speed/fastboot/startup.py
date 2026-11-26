#!/usr/bin/env python3
"""Install/restore the lab's HDMI module and shared first-frame sampler."""
import argparse
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import lab
WORK = HERE.parents[2] / "work/h700-loader"
OUT = WORK / "startup"
spec = importlib.util.spec_from_file_location("frame_sampler", HERE.parent / "first-frame.py")
frame = importlib.util.module_from_spec(spec)
spec.loader.exec_module(frame)


def install():
    lab.identity()
    OUT.mkdir(exist_ok=True)
    original = OUT / "original-rcS"
    if not original.exists():
        subprocess.run(lab.ADB + ["pull", "/etc/init.d/rcS", str(original)], check=True, capture_output=True)
        (OUT / "original-rcS.sha256").write_text(lab.sha(original.read_bytes()) + "\n")
    lab.require(lab.sha(original.read_bytes()) == (OUT / "original-rcS.sha256").read_text().strip(), "rcS backup changed")
    candidate = OUT / "trial-rcS"
    frame.hook(str(original), str(candidate))
    text = candidate.read_text()
    last = frame.LAST_LINE.findall(text)
    lab.require(len(last) == 1, "startup terminator differs")
    text = text.replace(last[0], "\n/sbin/insmod /usr/share/baseos/lab-fast/h700_late_hdmi.ko > /run/lab-late-hdmi.log 2>&1 &\n" + last[0])
    candidate.write_text(text)
    known = {lab.sha(original.read_bytes()), lab.sha(candidate.read_bytes())}
    lab.require(lab.sh("sha256sum /etc/init.d/rcS").split()[0] in known, "unknown startup script; refusing overwrite")
    module = WORK / "late-hdmi/h700_late_hdmi.ko"
    proof = json.loads((module.parent / "manifest.json").read_text())
    lab.require(lab.sha(module.read_bytes()) == proof["module_sha256"] and proof["validated_imports"] == 12,
                "HDMI module differs from its vendor ABI proof")
    lab.sh("mkdir -p /usr/share/baseos/lab-fast")
    lab.deploy(SimpleNamespace(mode="644", files=[str(module), "/usr/share/baseos/lab-fast/h700_late_hdmi.ko"]))
    lab.deploy(SimpleNamespace(mode="755", files=[str(candidate), "/etc/init.d/rcS"]))
    (OUT / "installed.json").write_text(json.dumps({"rcS_sha256": lab.sha(candidate.read_bytes()),
                                                  "module_sha256": proof["module_sha256"]}, indent=2) + "\n")


def restore():
    lab.identity()
    original = OUT / "original-rcS"
    lab.require(lab.sha(original.read_bytes()) == (OUT / "original-rcS.sha256").read_text().strip(), "rcS backup changed")
    lab.deploy(SimpleNamespace(mode="755", files=[str(original), "/etc/init.d/rcS"]))
    lab.sh("rm -f /usr/share/baseos/lab-fast/h700_late_hdmi.ko; rmdir /usr/share/baseos/lab-fast; sync")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("install", "restore"))
    args = parser.parse_args()
    install() if args.command == "install" else restore()
