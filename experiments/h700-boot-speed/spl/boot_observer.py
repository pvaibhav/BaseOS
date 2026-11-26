"""Observe early ADB boots while keeping an already-armed watchdog recoverable."""
import argparse
import json
import subprocess
import threading
import time
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
REMOTE = "/usr/sbin/baseos-boot-observer"


def install(lab):
    binary = ROOT / "work/h700-loader/mainline/watchdog-observer"
    digest = lab.sha(binary.read_bytes())
    existing = lab.sh(f"if [ -e {REMOTE} ]; then sha256sum {REMOTE} | cut -d' ' -f1; else echo absent; fi")
    lab.require(existing in ("absent", digest), "another boot observer occupies the lab helper path")
    if existing == "absent":
        lab.deploy(argparse.Namespace(mode="755", files=[str(binary), REMOTE]))
    return digest


class Observer:
    def __init__(self, lab, before, journal=None):
        self.lab, self.before = lab, before
        self.journal = journal
        self.rows = []
        self.attempts, self.failures, self.last_error = 0, 0, None
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self.observe, daemon=True)

    def observe(self):
        started = time.monotonic()
        while not self.stop.is_set() and time.monotonic() - started < 60:
            self.attempts += 1
            try:
                boot = self.lab.sh("cat /proc/sys/kernel/random/boot_id", 4)
                if boot != self.before:
                    output = self.lab.sh(REMOTE + " --reload", 4)
                    row = {"elapsed_s": time.monotonic() - started, "boot_id": boot, "watchdog": output}
                    self.rows.append(row)
                    if self.journal:
                        with self.journal.open("a") as stream: stream.write(json.dumps(row) + "\n")
            except (RuntimeError, subprocess.TimeoutExpired) as error:
                self.failures += 1
                self.last_error = str(error)[:400]
            self.stop.wait(1)

    def start(self):
        self.thread.start()

    def finish(self):
        self.stop.set()
        if self.thread.ident is not None: self.thread.join(9)
        return self.rows


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--control", type=Path, required=True)
    args = parser.parse_args()
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    import lab
    lab.identity()
    digest = install(lab)
    initial = lab.sh(REMOTE + " --reload")
    lab.require("wdt_mode=00000000" in initial and "wdt_mode_after_reload=00000000" in initial,
                "observer control requires an inactive watchdog")
    before = lab.sh("cat /proc/sys/kernel/random/boot_id")
    observer = Observer(lab, "control-distinct-boot-id")
    observer.start()
    observer.stop.wait(3)
    rows = observer.finish()
    lab.require(rows and all(row["boot_id"] == before and "wdt_mode_after_reload=00000000" in row["watchdog"]
                            for row in rows), "observer thread failed to capture the unchanged live boot")
    data = {"sha256": digest, "observer_module_sha256": lab.sha(Path(__file__).read_bytes()),
            "result": initial, "observations": rows, "passed": True}
    args.control.write_text(json.dumps(data, indent=2) + "\n")
    print(f"Observer captured {len(rows)} samples; inactive watchdog remained disabled.")
