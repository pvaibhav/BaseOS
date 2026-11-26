#!/usr/bin/env python3
"""Execute vendor flash init, late init and normal boot with the splash trial."""
import importlib.util
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("adapter_build", HERE / "build.py")
b = importlib.util.module_from_spec(spec)
spec.loader.exec_module(b)
spec = importlib.util.spec_from_file_location("vendor_trace", HERE.parent / "uboot-trace.py")
t = importlib.util.module_from_spec(spec)
spec.loader.exec_module(t)
output = Path(sys.argv[1])
package = (output / "boot-package.bin").read_bytes()
proof = json.loads((output / "manifest.json").read_text())
b.require(proof["panel_trial"] and b.kg.sha(package) == proof["patched_package_sha256"], "panel artifact differs")
prefix = b.ROOT / "work/rg34xxsp/boot-prefix.img"
derived = b.kg.derive("rg34xxsp", prefix)
environment = t.harness.stored_environment(prefix)
environment["baseos_skip_logo"] = "yes"
derived["uboot"] = package[0x800:0x100800]
derived["package"] = package
quick = proof.get("quick_recovery")
if quick:
    from oneshot import parse_env
    environment = parse_env((output / "env-armed_yes.bin").read_bytes())
target = t.harness.Target("rg34xxsp", derived, environment)
card = bytearray(t.card_image("rg34xxsp", derived, target.partition))
if quick:
    at = 0x42000 * 512
    card[at:at + 0x20000] = (output / "env-armed_yes.bin").read_bytes()
    sector = 0x62000 if proof.get("dual_slots") else 0x5A000
    start, end = sector * 512, (sector + 0x4800) * 512
    card.extend(b"\xff" * max(0, end - len(card)))
    image = (output / "boot.img").read_bytes()
    card[start:start + len(image)] = image
machine = t.Tracer(target.code, target.tree, environment, bytes(card), 0)
saves = []
def save():
    b.require(machine.env["baseos_skip_logo"] == "no", "normal panel path not restored")
    saves.append(dict(machine.env))
    return 0
machine.replace(0x5DCB8, save)
if quick:
    def write_sector():
        b.require(machine.arg(0) == 0x42000 and machine.arg(1) == 1, "recovery write extent differs")
        at = machine.arg(0) * 512
        card[at:at + 512] = machine.read(machine.arg(2), 512)
        b.require(bytes(card[at:at + 0x20000]) == (output / "env-normal.bin").read_bytes(), "recovery result differs")
        machine.sd.card.image = bytes(card)
        saves.append(dict(machine.env))
        return 1
    machine.replace(0x3FE3C, write_sector)
result = {}
result["init"] = machine.run(machine.sunxi_flash_init_offset(), "init")
result["pre"] = [machine.run(t.DISPLAY_INIT, "pre"), machine.run(t.BOOTREASON_INIT, "pre")]
result["late"] = machine.run(t.BOARD_LATE_INIT, "late")
result["main"] = machine.run(t.MAIN_LOOP, "main")
b.require(machine.jump and len(saves) == 1 and machine.env.get("lcd_type") == "boe", "panel trial did not execute")
b.require("disp_reserve=" not in machine.env["bootargs"], "stale boot framebuffer advertised")
result.update(machine.report())
result["jump"] = machine.jump
result["normal_logo_persisted"] = True
result["no_disp_reserve"] = True
result["package_sha256"] = b.kg.sha(package)
(output / "panel-trace.json").write_text(json.dumps(result, indent=2) + "\n")
print(json.dumps({key: result[key] for key in ("jump", "normal_logo_persisted", "no_disp_reserve", "phases")}, indent=2))
