#!/usr/bin/env python3
"""Report macOS card-open permissions; never write any card bytes."""
import json
import os
import sys

disk = sys.argv[1]
assert disk.startswith("/dev/disk") and disk[9:].isdigit(), disk
result = {"uid": os.getuid(), "euid": os.geteuid(), "devices": {}}
for device in (disk, disk.replace("/disk", "/rdisk"), disk + "s4", disk.replace("/disk", "/rdisk") + "s4"):
    modes = {}
    for name, mode in (("read", os.O_RDONLY), ("write-only-open", os.O_WRONLY), ("read-write-open", os.O_RDWR)):
        try:
            fd = os.open(device, mode)
            try:
                modes[name] = {"opened": True}
                if mode == os.O_RDONLY:
                    modes[name]["read_bytes"] = len(os.read(fd, 512))
            finally:
                os.close(fd)
        except OSError as error:
            modes[name] = {"errno": error.errno, "error": str(error)}
    result["devices"][device] = modes
print(json.dumps(result, indent=2))
