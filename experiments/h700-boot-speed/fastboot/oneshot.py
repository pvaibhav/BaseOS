#!/usr/bin/env python3
"""One-shot raw-MMC boot trials; the primary gzip image stays bootable.

prepare LABEL PACKAGE IMAGE [--proof MANIFEST] builds and emulates a trial.
stage LABEL stages the image/package, then arms the environment last.
run LABEL safely restarts, disarms a running trial watchdog and saves evidence.
restore restores the exact saved environment, boot partition and stock package.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import re
import struct
import subprocess
import sys
import time
import zlib
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(ROOT / "tools"))
import lab
import trial
import kernel_gzip as kg
import recovery_env
from frame_metrics import settled_frame
spec = importlib.util.spec_from_file_location("oneshot_harness", ROOT / "tests/test-fast-boot-emulation.py")
h = importlib.util.module_from_spec(spec)
spec.loader.exec_module(h)
WORK = trial.WORK
OFFSET = 32 * 1024 * 1024
SECTOR = 303104 + OFFSET // 512
ORIGINAL = "sunxi_flash read 45000000 boot;bootm 45000000"
ENV_SIZE = 0x20000


def parse_env(blob: bytes) -> dict[str, str]:
    lab.require(len(blob) == ENV_SIZE and kg.word(blob, 0) == zlib.crc32(blob[4:]), "environment CRC differs")
    lab.require(b"\0\0" in blob[4:], "unterminated environment")
    items = blob[4:].split(b"\0\0", 1)[0].split(b"\0")
    # This vendor's original export has one leading NUL before its first key.
    if items and items[0] == b"": items = items[1:]
    pairs = [item.decode("latin1").split("=", 1) for item in items]
    lab.require(all(len(pair) == 2 for pair in pairs), "invalid environment entry")
    result = dict(pairs)
    lab.require(len(result) == len(pairs), "duplicate environment key")
    return result


def encode_env(values: dict[str, str]) -> bytes:
    data = b"\0".join((key + "=" + value).encode("latin1") for key, value in values.items()) + b"\0\0"
    lab.require(len(data) <= ENV_SIZE - 4, "trial environment does not fit")
    data = data.ljust(ENV_SIZE - 4, b"\0")
    blob = struct.pack("<I", zlib.crc32(data)) + data
    lab.require(parse_env(blob) == values, "environment does not round-trip")
    return blob


def scripts(sectors: int, reset_test: bool = False, diagnostic: bool = False, vendor_reader: bool = False) -> dict[str, str]:
    result = {
        "boot_normal": "run baseos_trial_restore baseos_trial_device baseos_trial_read baseos_trial_boot;run boot_normal",
        "baseos_trial_restore": f"setenv boot_normal '{ORIGINAL}';saveenv",
        "baseos_trial_device": "mmc dev 0",
        "baseos_trial_read": f"mmc read 45000000 {SECTOR:x} {sectors:x}",
        "baseos_trial_boot": ("mw.l 30090b4 1;mw.l 30090b8 b1;mw.l 30090b0 14af;sleep 60;run boot_normal"
                              if reset_test else "setenv boot_from_partion boot;bootm 45000000"),
    }
    if vendor_reader:
        result["boot_normal"] = "run baseos_trial_restore baseos_trial_read baseos_trial_boot;run boot_normal"
        result["baseos_trial_read"] = "sunxi_nand_test"
    if diagnostic:
        result["baseos_checkpoint"] = "armed"
        result["boot_normal"] = ("run baseos_trial_restore baseos_mark_restore baseos_trial_device "
                                 "baseos_mark_device baseos_trial_read baseos_mark_read baseos_trial_boot;run boot_normal")
        for name in ("restore", "device", "read"):
            result["baseos_mark_" + name] = f"setenv baseos_checkpoint {name}-ok;saveenv"
    return result


def verify_scripts(tree: bytes, code: bytes, environment: dict, sectors: int, reset_test: bool,
                   diagnostic: bool, vendor_reader: bool, quick_blobs: dict | None = None,
                   read_sector: int = SECTOR, read_count: int = 0x4800, slot_offset: int | None = None) -> list:
    results = []
    for save_ok, read_ok in ((True, True), (False, True), (True, False)):
        commands = scripts(sectors, reset_test, diagnostic, vendor_reader)
        if quick_blobs: commands = parse_env(quick_blobs["armed_no"])
        m = h.Machine(code, tree, dict(environment, **commands), 0)
        if slot_offset is not None: m.put32(h.UBOOT + slot_offset, read_sector)
        current = bytearray(quick_blobs["armed_no"]) if quick_blobs else None
        events = []
        def save():
            lab.require(m.env["boot_normal"] == ORIGINAL, "normal boot was not restored before saveenv")
            checkpoint = m.env.get("baseos_checkpoint", "armed")
            events.append("save-normal" if checkpoint == "armed" else checkpoint)
            return 0 if save_ok or checkpoint != "armed" else 1
        def mmc():
            words = [m.text(m.get32(m.arg(3) + 4 * i)) for i in range(m.arg(2))]
            if words == ["mmc", "dev", "0"]:
                events.append("trial-device")
                return 0
            lab.require(words == commands["baseos_trial_read"].split(), "raw MMC command differs")
            events.append("trial-read")
            return 0 if read_ok else 1
        def setenv():
            words = [m.text(m.get32(m.arg(3) + 4 * i)) for i in range(m.arg(2))]
            lab.require(words[:2] in (["setenv", "boot_normal"], ["setenv", "boot_from_partion"],
                                     ["setenv", "baseos_checkpoint"]),
                        f"unexpected setenv: {words}")
            m.env[words[1]] = " ".join(words[2:])
            return 0
        def memory_write():
            words = [m.text(m.get32(m.arg(3) + 4 * i)) for i in range(m.arg(2))]
            events.append(" ".join(words))
            return 0
        m.replace(0x5DCB8, save)
        m.replace(0xE600, setenv)
        m.replace(0xA89C, mmc)
        def phyread():
            if quick_blobs and m.arg(0) == 0x42000:
                lab.require(m.arg(1) == 1, "recovery read differs")
                m.uc.mem_write(m.arg(2), bytes(current[:512]))
                return 1
            lab.require([m.arg(i) for i in range(3)] == [read_sector, read_count, 0x45000000], "vendor read extent differs")
            events.append("trial-read")
            return read_count if read_ok else 0
        m.replace(0x3FDF8, phyread)
        def phywrite():
            lab.require(m.arg(0) == 0x42000 and m.arg(1) == 1, "recovery write differs")
            events.append("save-normal")
            if not save_ok: return 0
            current[:512] = m.read(m.arg(2), 512)
            lab.require(bytes(current) == quick_blobs["normal"], "saved environment differs")
            return 1
        if quick_blobs: m.replace(0x3FE3C, phywrite)
        m.replace(0xA3C8, memory_write)
        m.replace(0xA814, lambda: events.append("sleep") or 0)
        m.replace(h.BOOTM, lambda: events.append("bootm") or 0)
        m.replace(h.SUNXI_FLASH, lambda: events.append("normal-read") or 0)
        m.command(0x12B00, "run", "boot_normal")
        expected = ["save-normal"]
        if save_ok:
            if diagnostic: expected.append("restore-ok")
            if not vendor_reader: expected += ["trial-device"]
            if diagnostic: expected.append("device-ok")
            expected.append("trial-read")
            if read_ok:
                if diagnostic: expected.append("read-ok")
                expected += (["mw.l 30090b4 1", "mw.l 30090b8 b1", "mw.l 30090b0 14af", "sleep",
                              "normal-read", "bootm"] if reset_test else ["bootm"])
        expected += ["normal-read", "bootm"]
        lab.require(events == expected, f"unsafe simple-parser path: {events} != {expected}")
        results.append({"save_ok": save_ok, "read_ok": read_ok, "events": events})
    return results


def directory(label: str) -> Path:
    lab.require(re.fullmatch(r"[a-z0-9-]+", label), "invalid trial label")
    return WORK / ("oneshot-" + label)


def prepare(label: str, package_path: Path, image_path: Path, proof_path: Path | None, reset_test: bool,
            diagnostic: bool, skip_logo: bool):
    output = directory(label)
    output.mkdir(parents=True, exist_ok=True)
    package, image = package_path.read_bytes(), image_path.read_bytes()
    base_package, base_image = trial.checked_base(), trial.BASE_IMAGE.read_bytes()
    if proof_path:
        proof = json.loads(proof_path.read_text())
        lab.require(proof["original_package_sha256"] == trial.BASE_SHA
                    and proof["patched_package_sha256"] == lab.sha(package)
                    and (proof["boot_image_sha256"] == lab.sha(image) or image == base_image)
                    and proof["verification"].get("gzip_fallback_exact"), "candidate proof/fallback differs")
    else:
        lab.require(package == base_package and image == base_image, "unproved candidate")
        proof = {"original_package_sha256": trial.BASE_SHA, "patched_package_sha256": trial.BASE_SHA,
                 "verification": {"gzip_fallback_exact": True}}
    if skip_logo:
        trace_path = package_path.parent / "panel-trace.json"
        trace = json.loads(trace_path.read_text())
        lab.require(proof.get("panel_trial") and trace["normal_logo_persisted"] and trace["no_disp_reserve"]
                    and trace["jump"]["entry"] == 0x40080000
                    and trace.get("package_sha256", lab.sha(package)) == lab.sha(package),
                    "full panel-path trace is required")
    # Preserve the exact proof input before adding only an observable header marker.
    parts, _ = kg.split(image)
    if parts[0][:4] == b"BOL4":
        lab.require(proof.get("watchdog_armed_seconds") == 16, "LZ4 trial requires a proved watchdog")
    marker = "baseos_trial=" + label
    cmdline = image[0x40:0x240].split(b"\0", 1)[0] + b" " + marker.encode()
    image = kg.join(image[:2048], parts, cmdline)
    sector = 0x62000 if proof.get("dual_slots") and skip_logo else SECTOR
    offset = (sector - 303104) * 512
    lab.require(offset + max(len(image), 0x4800 * 512) <= 64 * 1024 * 1024, "trial image exceeds boot partition")
    prefix = ROOT / "work/rg34xxsp/boot-prefix.img"
    derived = kg.derive("rg34xxsp", prefix)
    environment = h.stored_environment(prefix)
    code = package[0x800:0x100800]
    target = h.Target("rg34xxsp", dict(derived, uboot=code, package=package), environment)
    original_init = h.Machine.__init__
    def mapped_init(machine, *args, **kwargs):
        original_init(machine, *args, **kwargs)
        machine.uc.mem_map(0x03009000, 4096)
    h.Machine.__init__ = mapped_init
    try:
        normal = h.boot(h.Target("rg34xxsp", derived, environment), base_image)
        candidate = h.boot(target, image)
    finally:
        h.Machine.__init__ = original_init
    lab.require(candidate["jump"] == normal["jump"] and candidate["loaded"], "candidate handoff differs")
    lab.require(candidate["machine"].read(0x40080000, len(derived["kernel"])) == derived["kernel"], "kernel differs")
    props = lambda tree: {(path, name): value for path, name, _, value in h.fdt.walk(tree)}
    old, new = props(normal["tree"]), props(candidate["tree"])
    edits = proof.get("fdt_edits", [])
    for edit in edits:
        key = (edit["node"], edit["property"])
        lab.require(old[key] == edit["before"].encode() + b"\0"
                    and new[key] == edit["after"].encode() + b"\0", "declared FDT edit differs")
        old[key] = new[key]
    lab.require(new.pop(("/chosen", "bootargs")) == old.pop(("/chosen", "bootargs"))[:-1]
                + b" " + marker.encode() + b"\0" and new == old, "FDT differs beyond trial marker")
    vendor_reader = proof.get("vendor_trial_reader", False)
    quick_blobs = None
    if proof.get("quick_recovery"):
        lab.require(proof["verification"].get("quick_recovery_cases") and proof["recovery_test"] == reset_test,
                    "one-sector recovery controls are required")
        quick_blobs = recovery_env.images(parse_env(env_backup()), reset_test)
        lab.require({name: lab.sha(blob) for name, blob in quick_blobs.items()} == proof["recovery_environments"],
                    "compiled recovery environment differs")
        (output / "normal-env.bin").write_bytes(quick_blobs["normal"])
        (output / "prepared-env.bin").write_bytes(quick_blobs["armed_yes" if skip_logo else "armed_no"])
    lab.require(not diagnostic or not vendor_reader, "diagnostic MMC path and vendor reader cannot be mixed")
    control = verify_scripts(target.tree, code, environment, len(image) // 512, reset_test, diagnostic,
                             vendor_reader, quick_blobs, sector,
                             proof.get("fast_read_sectors" if skip_logo else "gzip_read_sectors", 0x4800),
                             proof.get("symbols", {}).get("trial_sector"))
    package_proof = {key: proof[key] for key in ("original_package_sha256", "patched_package_sha256", "verification")}
    package_proof["fdt_edits"] = edits
    for name, blob in (("boot-package.bin", package), ("boot.img", image)):
        path = output / name
        lab.require(not path.exists() or path.read_bytes() == blob, "immutable trial output differs")
        path.write_bytes(blob)
    (output / "package-proof.json").write_text(json.dumps(package_proof, indent=2) + "\n")
    report = {"label": label, "marker": marker, "package_sha256": lab.sha(package),
              "image_sha256": lab.sha(image), "image_bytes": len(image), "sector": sector,
              "kernel_exact": True, "fdt_marker_only": not edits and not skip_logo,
              "fdt_edits": edits, "reset_test": reset_test,
              "skip_logo": skip_logo,
              "runtime_late_hdmi": bool(proof.get("runtime_late_hdmi") and skip_logo),
              "quick_recovery": bool(quick_blobs),
              "scripts": (parse_env(quick_blobs["armed_no"]) if quick_blobs
                          else scripts(len(image) // 512, reset_test, diagnostic, vendor_reader)),
              "parser_failure_controls": control}
    if quick_blobs:
        report["prepared_env_sha256"] = lab.sha((output / "prepared-env.bin").read_bytes())
    if edits or report["runtime_late_hdmi"]:
        report["hdmi_module_sha256"] = json.loads((WORK / "late-hdmi/manifest.json").read_text())["module_sha256"]
    (output / "manifest.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({key: value for key, value in report.items() if key != "scripts"}, indent=2), flush=True)


def env_backup() -> bytes:
    path = WORK / "baseline-env.bin"
    if not path.exists():
        blob = lab.read_region("env", ENV_SIZE)
        values = parse_env(blob)
        lab.require(values["boot_normal"] == ORIGINAL and values["bootcmd"] == "run setargs_nand boot_normal",
                    "environment is not the expected original")
        path.write_bytes(blob)
        (WORK / "baseline-env.sha256").write_text(lab.sha(blob) + "\n")
    blob = path.read_bytes()
    lab.require(lab.sha(blob) == (WORK / "baseline-env.sha256").read_text().strip(), "env backup changed")
    parse_env(blob)
    return blob


def restore_env():
    lab.identity()
    blob = env_backup()
    if lab.device_sha("env", len(blob)) != lab.sha(blob): lab.write_region("env", blob)


def stage(label: str):
    lab.identity()
    output = directory(label)
    report = json.loads((output / "manifest.json").read_text())
    if report.get("fdt_edits") or report.get("runtime_late_hdmi"):
        lab.require(lab.sh("sha256sum /usr/share/baseos/lab-fast/h700_late_hdmi.ko").split()[0] == report["hdmi_module_sha256"]
                    and "h700_late_hdmi.ko" in lab.sh("cat /etc/init.d/rcS"), "HDMI recovery module/startup missing")
    image = (output / "boot.img").read_bytes()
    offset = (report["sector"] - 303104) * 512
    lab.require(lab.sha(image) == report["image_sha256"], "staged image changed")
    original = env_backup()
    if not report.get("quick_recovery"): restore_env()
    trial.image_backup()
    # Package supports gzip before the environment selects the staged image.
    trial.install(output / "boot-package.bin", output / "package-proof.json")
    if lab.device_sha("boot", len(image), offset) != lab.sha(image):
        lab.write_region("boot", image, offset)
    lab.require(lab.device_sha("boot", len(trial.BASE_IMAGE.read_bytes())) == lab.sha(trial.BASE_IMAGE.read_bytes()),
                "primary gzip image changed")
    if report.get("quick_recovery"):
        armed = (output / "prepared-env.bin").read_bytes()
        lab.require(lab.sha(armed) == report["prepared_env_sha256"], "prepared environment changed")
    else:
        values = parse_env(original)
        values.update(report["scripts"])
        if report.get("skip_logo"): values["baseos_skip_logo"] = "yes"
        armed = encode_env(values)
    (output / "armed-env.bin").write_bytes(armed)
    current = lab.read_region("env", ENV_SIZE)
    if current[512:] == armed[512:]:
        lab.write_region("env", armed[:512], env_image=armed)
    else:
        lab.write_region("env", armed)
    print("one-shot armed; primary gzip remains intact", flush=True)


def run(label: str):
    lab.identity()
    output = directory(label)
    report = json.loads((output / "manifest.json").read_text())
    armed = output / "armed-env.bin"
    lab.require(armed.exists() and lab.device_sha("env", ENV_SIZE) == lab.sha(armed.read_bytes()),
                "trial is not armed; refusing restart")
    started = time.monotonic()
    lab.reboot()
    elapsed = time.monotonic() - started
    helper = WORK / "watchdog-probe"
    subprocess.run(lab.ADB + ["push", str(helper), "/tmp/watchdog-probe"], check=True, capture_output=True)
    watchdog = lab.sh("chmod 755 /tmp/watchdog-probe; /tmp/watchdog-probe --disarm")
    lab.require("wdt_mode_after_stop=00000000" in watchdog, "trial watchdog did not stop")
    data = lab.collect("oneshot-" + label)
    data["trial_active"] = report["marker"] in data["cmdline"].split()
    data["watchdog"] = watchdog
    data["restart_host_elapsed_s"] = elapsed
    data["package_sha256"] = lab.device_sha("toc1", len(trial.checked_base()))
    data["staged_image_sha256"] = lab.device_sha("boot", report["image_bytes"], (report["sector"] - 303104) * 512)
    data["primary_image_sha256"] = lab.device_sha("boot", len(trial.BASE_IMAGE.read_bytes()))
    data["load"] = lab.sh("uptime; cat /sys/devices/system/cpu/online")
    data["display"] = lab.sh("cat /sys/class/disp/disp/attr/sys")
    if lab.sh("test -s /run/lab-trace && echo present || true") == "present":
        trace = lab.sh("cat /run/lab-trace")
        (output / ("run-" + data["boot_id"] + ".trace")).write_text(trace + "\n")
        first = settled_frame(trace, data["legacy_handoff_s"])
        if first is not None:
            data["settled_framebuffer_s"] = first
            data["settled_framebuffer_counter_s"] = data["pre_kernel_s"] + first
    if report.get("fdt_edits") or report.get("runtime_late_hdmi"):
        data["hdmi"] = lab.sh("dmesg | grep h700_late_hdmi; cat /run/lab-late-hdmi.log")
    # Readback proves that U-Boot saved the original normal path before trial boot.
    current = lab.read_region("env", ENV_SIZE)
    data["env_after_sha256"] = lab.sha(current)
    data["normal_restored"] = parse_env(current)["boot_normal"].rstrip() == ORIGINAL
    if report.get("quick_recovery"):
        data["normal_restored"] &= current == (output / "normal-env.bin").read_bytes()
    data["checkpoint"] = parse_env(current).get("baseos_checkpoint")
    data["logo_normal_restored"] = parse_env(current).get("baseos_skip_logo") != "yes"
    data["boot_framebuffer_advertised"] = any(word.startswith("disp_reserve=") for word in data["cmdline"].split())
    (output / ("run-" + data["boot_id"] + ".json")).write_text(json.dumps(data, indent=2) + "\n")
    lab.require(data["normal_restored"], "one-shot did not persist normal boot")
    lab.require(data["package_sha256"] == report["package_sha256"]
                and data["staged_image_sha256"] == report["image_sha256"]
                and data["primary_image_sha256"] == lab.sha(trial.BASE_IMAGE.read_bytes()), "boot-area readback differs")
    lab.require(data["health"][:6] == ["GPU_OK", "WIFI_OK", "FB_OK", "6", "3", "6"]
                and data["health"][-1] == "1", "device/frontend health differs")
    lab.require("lcd output" in data["display"] and "unblank" in data["display"] and "err:0" in data["display"],
                "display engine is not running normally")
    if report.get("fdt_edits") or report.get("runtime_late_hdmi"):
        lab.require("started 1 held-back HDMI devices, 0 failed" in data["hdmi"], "late HDMI probe did not complete")
    lab.require(data["trial_active"] != report["reset_test"], "trial selection/recovery was not observed")
    if report.get("skip_logo"):
        lab.require(data["logo_normal_restored"] and not data["boot_framebuffer_advertised"],
                    "panel trial/fallback was not observed")
    if report["reset_test"]:
        lab.require(20 < elapsed < 50, "restart timing does not prove the 16-second watchdog reset")
    print(json.dumps({key: data[key] for key in ("boot_id", "trial_active", "normal_restored", "watchdog")}, indent=2))
    return data


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prepare_args = commands.add_parser("prepare")
    prepare_args.add_argument("label")
    prepare_args.add_argument("package", type=Path)
    prepare_args.add_argument("image", type=Path)
    prepare_args.add_argument("--proof", type=Path)
    prepare_args.add_argument("--reset-test", action="store_true")
    prepare_args.add_argument("--diagnostic", action="store_true")
    prepare_args.add_argument("--skip-logo", action="store_true")
    for command in ("stage", "run"):
        commands.add_parser(command).add_argument("label")
    commands.add_parser("restore")
    args = parser.parse_args()
    if args.command == "prepare": prepare(args.label, args.package, args.image, args.proof,
                                          args.reset_test, args.diagnostic, args.skip_logo)
    elif args.command == "stage": stage(args.label)
    elif args.command == "run": run(args.label)
    else:
        restore_env()
        trial.install(None, None)
        lab.write_region("boot", trial.image_backup())
