#!/usr/bin/env python3
"""The boot files and the USB module must agree on which controllers start late.

A node the build marks "late" that the module does not know stays off for good,
so check the two lists against each other and against every target's derived
device tree. Needs prepared firmware in work/<target>; optional target names.
"""
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import fdt_edit
import kernel_gzip as k

source = (ROOT / "src/h700-late-usb/h700_late_usb.c").read_text()
module = set(re.findall(r'"(allwinner,sunxi-[eo]hci\d)"', source))
assert '#define HELD_BACK "late"' in source
# The module must never be unloaded: the status values it hands out are its own.
assert "module_exit" not in source

# Node names follow <ehci|ohci><port>-controller@<address> on every target.
named = {"allwinner,sunxi-" + node.split("-")[0] for node in k.LATE_USB}
assert module == named and len(named) == len(k.LATE_USB) == 5, (module, named)
assert "allwinner,sunxi-" + k.KEPT_USB.split("-")[0] not in module
# An OHCI controller hands ports over to its EHCI companion, which must exist
# first: EHCI 2 at kernel start, the others in the order the module lists them.
order = re.findall(r'"allwinner,sunxi-([eo])hci(\d)"', source)
for port in {port for _, port in order}:
    kinds = [kind for kind, p in order if p == port]
    assert kinds in (["e", "o"], ["o"]), (port, kinds)
    assert kinds == ["e", "o"] or port == k.KEPT_USB[4], port

catalog = json.loads(k.CATALOG.read_text())
for target in sys.argv[1:] or catalog["profiles"]:
    tree = k.derive(target, ROOT / "work" / target / "boot-prefix.img")["package_dtb"]
    compatible = {path: value.rstrip(b"\0").decode() for path, name, _, value in fdt_edit.walk(tree)
                  if name == "compatible"}
    late = {path for path, name, _, value in fdt_edit.walk(tree) if name == "status" and value == b"late\0"}
    assert {path.split("/")[-1] for path in late} == set(k.LATE_USB), (target, late)
    assert {compatible[path] for path in late} == module, target
    hosts = {path: fdt_edit.find(tree, path.split("/")[-1], "status")[1]
             for path, value in compatible.items() if re.fullmatch(r"allwinner,sunxi-[eo]hci\d", value)}
    assert len(hosts) == 8 and all(status == b"okay\0" for path, status in hosts.items() if path not in late), target
    print(f"{target}: five host controllers held back, all known to the module; three left as the vendor set them")

sdc1_source = (ROOT / "src/h700-late-usb/h700_late_sdc1.c").read_text()
assert '#define HELD_BACK "late"' in sdc1_source
assert "module_exit" not in sdc1_source
assert '"allwinner,sunxi-mmc-v4p1x"' in sdc1_source and '"sdc1"' in sdc1_source
print("late USB and sdc1 tests passed")
