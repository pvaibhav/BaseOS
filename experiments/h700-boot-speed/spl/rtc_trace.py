"""Collect the bounded RTC diagnostic after a lab restart."""
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def collect(lab):
    helper = ROOT / "work/h700-loader/mainline/rtc-trace"
    subprocess.run(lab.ADB + ["push", str(helper), "/tmp/baseos-rtc-trace"],
                   check=True, capture_output=True)
    output = lab.sh("chmod 755 /tmp/baseos-rtc-trace; /tmp/baseos-rtc-trace")
    values = {name: int(value, 16) for name, value in (line.split("=") for line in output.splitlines())}
    lab.require(set(values) == {f"gp{i}" for i in range(16)}, "RTC register census incomplete")
    return values
