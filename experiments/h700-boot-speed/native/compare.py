#!/usr/bin/env python3
"""Alternate qualified clean native BROM boots with the intact gzip baseline.

Use the same BOOT_LAB_* environment as run-brom-control.py. This runner performs
device writes and restarts when invoked; output must be a fresh directory.
"""
import argparse
import gzip
import importlib.util
import json
import math
import os
import platform
import re
import shutil
import statistics
import struct
import subprocess
import sys
import time
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "spl"))
spec = importlib.util.spec_from_file_location("native_comparison_brom", HERE.parent / "spl/run-brom-control.py")
control = importlib.util.module_from_spec(spec)
spec.loader.exec_module(control)
lab, one = control.one.lab, control.one
import fdt
from frame_metrics import settled_frame
capture_spec = importlib.util.spec_from_file_location("comparison_display_capture", HERE / "capture-display.py")
display_capture = importlib.util.module_from_spec(capture_spec)
capture_spec.loader.exec_module(display_capture)

TIMINGS = ("legacy_handoff_s", "pre_kernel_s", "post_kernel_s", "combined_s",
           "settled_framebuffer_s", "settled_framebuffer_counter_s")
PHASES = ("dram_setup", "pio_loading", "monitor_loader_entry", "kernel_unpack", "fdt_setup", "handoff_bookkeeping")
BOOT0_CALIBRATION_WORDS = {0x0c: "checksum", 0xa0: "dram_tpr6", 0xa8: "dram_tpr11", 0xac: "dram_tpr12"}


def read_json(path):
    return json.loads(path.read_text())


def save(path, data):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, indent=2) + "\n")
    temporary.replace(path)


def fdt_state(blob):
    lab.require(len(blob) >= 40 and struct.unpack_from(">II", blob)[0] == 0xd00dfeed and
                struct.unpack_from(">I", blob, 4)[0] == len(blob), "incomplete kernel FDT")
    props = {(path, name): value for path, name, _, value in fdt.walk(blob)}
    generation = props.get(("/chosen", "baseos,native-boot"))
    stamps = props.get(("/chosen", "baseos,native-timing"))
    return {"sha256": lab.sha(blob), "native_generation": generation.hex() if generation else None,
            "native_stage_counter_s": [value / 24000000 for value in struct.unpack(">7Q", stamps)]
            if stamps else None}


def comparable_cmdline(command):
    # These fields describe the removed U-Boot display/logging allocations.
    return [word for word in command.split() if not word.startswith(("uboot_message=", "disp_reserve="))]


def checked_inputs(spl, warm, bundle, qualification):
    manifest, native = read_json(spl / "manifest.json"), read_json(bundle / "manifest.json")
    warm_manifest, qualified = read_json(warm / "manifest.json"), read_json(qualification / "run.json")
    profile = control.PROFILES.get(lab.TARGET)
    lab.require(profile and lab.TARGET == native["target"] == one.trial.PROFILE_TARGET and
                lab.SERIAL == profile["serial"] and native["package_sha256"] == profile["package_sha256"],
                "comparison requires an explicitly pinned lab environment and source package")
    source_manifest_path = one.trial.BASE_IMAGE.with_name("manifest.json")
    source_manifest = read_json(source_manifest_path)
    original_kernel_sha = source_manifest["kernel_sha256"]
    recovery_toc_sha = warm_manifest["patched_package_sha256"]
    normal_environment_sha = lab.sha((warm / "env-normal.bin").read_bytes())
    lab.require(native["native_diagnostic"] is False and not any(native.get(key) for key in (
        "entry_checksum_witness", "kernel_exception_witness", "monitor_psci_probe", "kernel_initcall_probe",
        "diagnostic_maxcpus")), "comparison requires a clean native bundle")
    lab.require(native["kernel_sha256"] == native["source_kernel_sha256"] == original_kernel_sha and
                lab.sha((bundle / "kernel.bin").read_bytes()) == original_kernel_sha,
                "native kernel differs from the original vendor kernel")
    lab.require(manifest["native_loader"] == native and warm_manifest["spl_sha256"] == manifest["spl_sha256"] and
                warm_manifest["restores_primary"] and not warm_manifest["train_native_dram"],
                "SPL, native bundle and primary warm control differ")
    warm_runs = [read_json(path) for path in warm.glob("run-*.json")]
    lab.require(any(row.get("passed") and row.get("restore_sector") == 16 and
                    row.get("normal_environment_exact") for row in warm_runs), "primary warm control is unqualified")
    lab.require(qualified["passed"] and qualified["spl_marker_found"] and
                qualified["native_generation"] == native["generation"] and
                qualified["spl_sha256"] == manifest["spl_sha256"] and not qualified["host_restore_needed"] and
                qualified["normal_environment_exact"] and qualified["stock_boot0_body_exact"] and
                qualified["stock_boot0_checksum_valid"] and qualified["boot_id"] != qualified["before_boot_id"],
                "matching clean native BROM qualification is required")
    qualified_fdt = fdt_state((qualification / "kernel-fdt.bin").read_bytes())
    lab.require(qualified_fdt["native_generation"] == native["generation"] and
                qualified_fdt["native_stage_counter_s"] is not None, "qualification FDT identity/timing differs")
    lab.require(qualified["normal_package_sha256"] == recovery_toc_sha and
                lab.sha((warm / "boot-package.bin").read_bytes()) == recovery_toc_sha,
                "baseline differs from the qualified recovery TOC")
    original_image = one.trial.BASE_IMAGE.read_bytes()
    kernel = one.trial.kg.split(original_image)[0][0]
    lab.require(lab.sha(original_image) == source_manifest["boot_image_sha256"] and
                lab.sha(gzip.decompress(kernel)) == original_kernel_sha, "baseline gzip kernel differs")
    files = {spl / "manifest.json": None, spl / "spl.bin": manifest["spl_sha256"],
             spl / "restore-source.bin": manifest["restore_source_sha256"],
             warm / "manifest.json": None, warm / "env-normal.bin": normal_environment_sha,
             warm / "boot-package.bin": recovery_toc_sha, bundle / "manifest.json": None,
             qualification / "run.json": None, qualification / "kernel-fdt.bin": qualified_fdt["sha256"],
             one.trial.BASE_IMAGE: lab.sha(original_image), source_manifest_path: None, one.trial.BASE: profile["package_sha256"],
             Path(lab.__file__): None,
             Path(control.__file__): None, Path(control.qualify.__file__): None,
             Path(sys.modules[settled_frame.__module__].__file__): None,
             Path(__file__): None, HERE / "capture-display.py": None, HERE / "display_metrics.py": None}
    for name in ("image", "firmware", "loader", "kernel", "monitor"):
        files[bundle / f"{name}.bin"] = native[name + "_sha256"]
    pinned = {}
    for path, expected in files.items():
        actual = lab.sha(path.read_bytes())
        lab.require(expected is None or actual == expected, f"input differs: {path}")
        pinned[str(path.resolve())] = actual
    return {"files": pinned, "generation": native["generation"], "spl_sha256": manifest["spl_sha256"],
            "native_manifest": native, "qualification": qualified, "qualification_fdt": qualified_fdt,
            "restore_source": str((spl / "restore-source.bin").resolve()),
            "environment_sha256": normal_environment_sha, "policy_source_environment_sha256": native["environment_sha256"],
            "recovery_toc_sha256": recovery_toc_sha,
            "gzip_image_sha256": lab.sha(original_image), "gzip_image_bytes": len(original_image),
            "original_kernel_sha256": original_kernel_sha}


def host_context():
    cores, load = os.cpu_count(), os.getloadavg()
    data = {"platform": platform.platform(), "cores": cores, "load_average": list(load),
            "uptime": subprocess.run(["uptime"], check=True, capture_output=True, text=True).stdout.strip()}
    if cores and load[0] >= cores:
        processes = subprocess.run(["ps", "-axo", "pid=,pcpu=,comm="], check=True,
                                   capture_output=True, text=True).stdout.splitlines()
        data["busy_processes"] = sorted(processes, key=lambda row: float(row.split()[1]), reverse=True)[:10]
    return data


def compare_stock_boot0(source, current):
    # Stock boot0 saves trained DRAM values back to these header words on boot.
    # Every byte outside this exact whitelist, including the code, stays pinned.
    extent_exact = len(source) == len(current) == 65536
    changed = [{"offset": f"0x{offset:03x}", "name": BOOT0_CALIBRATION_WORDS.get(offset),
                "original": f"0x{one.trial.kg.word(source, offset):08x}",
                "current": f"0x{one.trial.kg.word(current, offset):08x}"}
               for offset in range(0, min(len(source), len(current)) - 3, 4)
               if source[offset:offset + 4] != current[offset:offset + 4]]
    allowed = extent_exact and all(int(row["offset"], 16) in BOOT0_CALIBRATION_WORDS for row in changed)
    fields = {name: {"original": source[offset:offset + size].hex(),
                     "current": current[offset:offset + size].hex(),
                     "unchanged": source[offset:offset + size] == current[offset:offset + size]}
              for name, offset, size in (("entry", 0, 4), ("magic", 4, 8), ("size", 16, 4),
                                         ("dram_clock", 0x38, 4), ("dram_type", 0x3c, 4))}
    checksum_valid = extent_exact and one.trial.kg.checksum(current, 12) == one.trial.kg.word(current, 12)
    source_checksum_valid = extent_exact and one.trial.kg.checksum(source, 12) == one.trial.kg.word(source, 12)
    body_exact = extent_exact and source[512:] == current[512:]
    return {"original_sha256": lab.sha(source), "current_sha256": lab.sha(current),
            "original_bytes": len(source), "current_bytes": len(current), "extent_exact": extent_exact,
            "allowed_changed_words": {f"0x{offset:03x}": name for offset, name in BOOT0_CALIBRATION_WORDS.items()},
            "changed_words": changed, "only_calibration_words_changed": allowed, "header_fields": fields,
            "original_executable_body_sha256": lab.sha(source[512:]),
            "current_executable_body_sha256": lab.sha(current[512:]), "body_exact": body_exact,
            "original_checksum_valid": source_checksum_valid, "checksum_valid": checksum_valid,
            "passed": allowed and body_exact and checksum_valid and source_checksum_valid and
                      all(field["unchanged"] for field in fields.values())}


def observe(directory, pins):
    directory.mkdir(parents=True, exist_ok=True)
    boot0 = lab.read_region("boot0")
    tree = bytes.fromhex(lab.sh("od -An -tx1 -v /sys/firmware/fdt", 60))
    (directory / "stock-boot0.bin").write_bytes(boot0)
    (directory / "live-fdt.bin").write_bytes(tree)
    processes = [line for line in lab.sh("ps").splitlines()
                 if re.search(r"(?:System/slot|nextui\.elf)(?:\s|$)", line)]
    lab.require(len(processes) == 1 and processes[0].split()[0].isdigit(), "expected one running frontend")
    pid = int(processes[0].split()[0])
    software = {"release_startup_sha256": lab.sh(
        "sha256sum /etc/baseos-release /etc/init.d/rcS /usr/sbin/frontend-session"),
        "frontend_executable": lab.sh(f"readlink /proc/{pid}/exe"),
        "frontend_sha256": lab.sh(f"sha256sum /proc/{pid}/exe").split()[0],
        "root_partition": lab.sh("cat /sys/class/block/mmcblk0p5/start /sys/class/block/mmcblk0p5/size"),
        "comparable_cmdline": comparable_cmdline(lab.sh("cat /proc/cmdline"))}
    lines = lab.sh("uptime; cat /proc/uptime; cat /proc/loadavg; cat /sys/devices/system/cpu/online;"
                   " grep -c '^processor' /proc/cpuinfo").splitlines()
    lab.require(len(lines) == 5, "device load census is incomplete")
    device = {"uptime": lines[0], "uptime_s": float(lines[1].split()[0]),
              "load_average": [float(value) for value in lines[2].split()[:3]],
              "online_cpus": lines[3], "cores": int(lines[4])}
    if device["load_average"][0] >= device["cores"]:
        device["busy_processes"] = lab.sh("top -b -n 1 | head -20")
    source = Path(pins["restore_source"]).read_bytes()
    boot0_comparison = compare_stock_boot0(source, boot0)
    state = {"boot_id": lab.sh("cat /proc/sys/kernel/random/boot_id"), "software": software, "device": device,
             "boot0_sha256": boot0_comparison["current_sha256"],
             "boot0_original_sha256": boot0_comparison["original_sha256"],
             "stock_boot0_comparison": boot0_comparison, "stock_boot0_body_exact": boot0_comparison["body_exact"],
             "stock_boot0_checksum_valid": boot0_comparison["checksum_valid"],
             "environment_sha256": lab.sha(lab.read_region("env", one.ENV_SIZE)),
             "recovery_toc_sha256": lab.device_sha("toc1", 1310720),
             "gzip_image_sha256": lab.device_sha("boot", pins["gzip_image_bytes"]), "fdt": fdt_state(tree)}
    save(directory / "state.json", state)
    lab.require(boot0_comparison["passed"] and
                boot0_comparison["original_sha256"] == pins["files"][pins["restore_source"]] and
                state["environment_sha256"] == pins["environment_sha256"] and
                state["recovery_toc_sha256"] == pins["recovery_toc_sha256"] and
                state["gzip_image_sha256"] == pins["gzip_image_sha256"], "normal recovery state differs")
    return state


def finish_run(data, state, before, side, pins, software, seen, directory):
    lab.require(data["boot_id"] == state["boot_id"] and data["boot_id"] != before and
                data["boot_id"] not in seen, "boot ID is stale or changed during collection")
    lab.require(state["software"] == software, "rootfs/startup/frontend/kernel command line changed")
    health = data["health"]
    lab.require(len(health) == 8 and health[:6] == pins["qualification"]["health"][:6] and
                health[:3] == ["GPU_OK", "WIFI_OK", "FB_OK"] and health[-1] == "1" and
                all(int(value) > 0 for value in health[3:6]), "boot health differs from qualification")
    generation = state["fdt"]["native_generation"]
    lab.require(generation == (pins["generation"] if side == "native" else None), "boot path/FDT generation differs")
    if side == "native":
        lab.require(data["passed"] and not data["host_restore_needed"] and data["native_generation"] == generation and
                    data["before_boot_id"] == before,
                    "native BROM recovery failed")
    trace = lab.sh("cat /run/lab-trace")
    (directory / "comparison-frame.trace").write_text(trace + "\n")
    collected = [path for path in (lab.WORK / "runs").glob(data["label"] + "-*.json")
                 if read_json(path)["boot_id"] == data["boot_id"]]
    lab.require(len(collected) == 1, "cannot identify this boot's collected raw evidence")
    for suffix in (".json", ".dmesg", ".bootevent"):
        shutil.copyfile(collected[0].with_suffix(suffix), directory / ("collected" + suffix))
    settled = settled_frame(trace, data["legacy_handoff_s"])
    lab.require(settled is not None, "no settled post-handoff framebuffer in the captured trace")
    data.update(settled_framebuffer_s=settled, settled_framebuffer_counter_s=data["pre_kernel_s"] + settled)
    metrics = {key: float(data[key]) for key in TIMINGS}
    metrics.update({"initcall_ms/" + key: float(value) for key, value in data["initcalls_ms"].items()})
    stages = state["fdt"]["native_stage_counter_s"]
    if stages:
        lab.require(all(math.isfinite(value) for value in stages) and stages == sorted(stages), "invalid native stage times")
        metrics.update({"native_phase_s/" + name: stages[i + 1] - stages[i] for i, name in enumerate(PHASES)})
    lab.require(all(math.isfinite(value) and value >= 0 for value in metrics.values()), "invalid timing metric")
    display = display_capture.capture(directory / "display-output")
    lab.require(display["boot_id"] == data["boot_id"] and display["pixel_validation"]["passed"],
                "display-engine output does not match the active frontend framebuffer")
    return {"boot_id": data["boot_id"], "before_boot_id": before, "health": health, "warnings": data["warnings"],
            "metrics": metrics, "state": state, "collected": data, "display": display, "passed": True}


def summarize(runs):
    summary = {}
    for side in ("baseline", "native"):
        selected = [row for row in runs if row["side"] == side and row["status"] == "passed"]
        keys = sorted({key for row in selected for key in row["result"]["metrics"]})
        summary[side] = {key: {"n": len(values), "median": statistics.median(values),
                               "min": min(values), "max": max(values)} for key in keys
                         if (values := [row["result"]["metrics"][key] for row in selected
                                        if key in row["result"]["metrics"]])}
    return summary


def compare(spl, warm, bundle, qualification, output, pairs=5):
    lab.require(pairs >= 5, "a comparison needs at least five pairs")
    lab.require(not output.exists(), "comparison output already exists; choose a fresh directory")
    output.mkdir(parents=True)
    report = {"claim": "Clean native first-stage boot reduces warm counter-origin-to-frontend and settled-framebuffer time.",
              "limiter": "Qualification-only native stage timestamps separate DRAM setup, PIO loading and unpacking; kernel/userspace are shared.",
              "pairs": pairs, "runs": [], "status": "checking", "benchmark_valid": False,
              "attempted_boots": 0, "healthy_boots": 0, "failed_boots": 0,
              "limitations": ["USB-connected warm restarts; counter origin is not physical power-button time.",
                              "Settled framebuffer timing is sampled; later DE writeback must match its active pixels, without measuring panel photons.",
                              "Same current rootfs partition/release/startup/frontend hashes; no whole-rootfs or mutable-userdata hash.",
                              "First-stage staging, ADB recovery and validation wall time are outside reported counter metrics.",
                              "Both sides retain the same installed frame sampler; native trials also use the qualified watchdog observer."]}
    save(output / "comparison.json", report)
    active = None
    try:
        pins = checked_inputs(spl, warm, bundle, qualification)
        report["pins"] = pins
        report["host_start"] = host_context()
        lab.identity()
        initial = observe(output / "start", pins)
        report["start"] = initial
        report["status"] = "running"
        save(output / "comparison.json", report)
        seen, before = {initial["boot_id"]}, initial["boot_id"]
        for pair in range(1, pairs + 1):
            pair_dir = output / f"pair-{pair:02d}"
            pair_dir.mkdir()
            for side in ("baseline", "native"):
                active = {"pair": pair, "side": side, "status": "running", "before_boot_id": before}
                report["runs"].append(active)
                report["attempted_boots"] = len(report["runs"])
                save(output / "comparison.json", report)
                save(pair_dir / f"{side}-pending.json", active)
                for filename, digest in pins["files"].items():
                    lab.require(lab.sha(Path(filename).read_bytes()) == digest, f"input changed: {filename}")
                lab.require(lab.sh("cat /proc/sys/kernel/random/boot_id") == before,
                            "device rebooted between comparison attempts")
                directory = pair_dir / side
                if side == "baseline":
                    directory.mkdir()
                    lab.reboot()
                    data = lab.collect(f"native-match-{output.name}-{pair:02d}-baseline")
                else:
                    control.run(spl, warm, directory, handoff=True, native_loader=bundle)
                    data = read_json(directory / "run.json")
                state = observe(directory, pins)
                result = finish_run(data, state, before, side, pins, initial["software"], seen, directory)
                save(directory / "comparison-run.json", result)
                active.update(status="passed", result=result)
                report["healthy_boots"] += 1
                seen.add(result["boot_id"])
                before = result["boot_id"]
                report["summary"] = summarize(report["runs"])
                save(output / "comparison.json", report)
                print(json.dumps({"pair": pair, "side": side, "boot_id": before, "metrics": result["metrics"]}), flush=True)
                active = None
        lab.require(len(report["runs"]) == pairs * 2 and all(row["status"] == "passed" for row in report["runs"]),
                    "not every attempted boot passed")
        baseline = report["summary"]["baseline"]["settled_framebuffer_counter_s"]
        native = report["summary"]["native"]["settled_framebuffer_counter_s"]
        report.update(status="complete", benchmark_valid=True,
                      verdict="faster" if native["max"] < baseline["min"] else
                              "slower" if native["min"] > baseline["max"] else "no measurable difference",
                      verdict_rule="Conservative nonoverlapping observed ranges; no significance test is claimed.",
                      healthy_boots=len(report["runs"]), failed_boots=0, host_end=host_context())
        save(output / "comparison.json", report)
        print(json.dumps(report["summary"], indent=2), flush=True)
    except BaseException as error:
        failure = {"type": type(error).__name__, "message": str(error), "traceback": traceback.format_exc()}
        if active is not None:
            active.update(status="failed", failure=failure)
            pair_dir = output / f"pair-{active['pair']:02d}"
            save(pair_dir / f"{active['side']}-failure.json", failure)
        report.update(status="failed", benchmark_valid=False, verdict="inconclusive", failure=failure,
                      failed_boots=sum(row["status"] == "failed" for row in report["runs"]),
                      summary=summarize(report["runs"]))
        save(output / "comparison.json", report)
        raise
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("spl", type=Path)
    parser.add_argument("warm_restore", type=Path)
    parser.add_argument("native_bundle", type=Path)
    parser.add_argument("qualification", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--pairs", type=int, default=5)
    args = parser.parse_args()
    compare(args.spl.resolve(), args.warm_restore.resolve(), args.native_bundle.resolve(),
            args.qualification.resolve(), args.output.resolve(), args.pairs)
