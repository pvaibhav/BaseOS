#!/usr/bin/env python3
"""Run one proved SRAM trial without writing boot0 or the primary boot image."""
import argparse
import json
import struct
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE.parent / "fastboot"))
import oneshot as one
import rtc_trace

RESULT_OFFSET = 24 * 1024 * 1024
IMAGE_OFFSET = 48 * 1024 * 1024


def fnv(data):
    value = 2166136261
    for byte in data: value = ((value ^ byte) * 16777619) & 0xffffffff
    return value


def read_result():
    one.lab.sh(f"dd if=/dev/mmcblk0p4 of=/tmp/spl-result.bin bs=512 skip={RESULT_OFFSET // 512} count=1 iflag=direct 2>/dev/null")
    local = one.WORK / "spl-result.bin"
    subprocess.run(one.lab.ADB + ["pull", "/tmp/spl-result.bin", str(local)], check=True, capture_output=True)
    result = local.read_bytes()
    one.lab.require(len(result) == 512, "result read extent differs")
    return result


def run(directory: Path, probe_dir: Path, control_dir: Path | None, os_only=False):
    one.lab.identity()
    manifest = json.loads((directory / "manifest.json").read_text())
    one.trial.checked_base()
    one.lab.require(manifest.get("target", "rg34xxsp") == one.lab.TARGET and
                    manifest["original_package_sha256"] == one.trial.BASE_SHA, "trial target differs")
    proof = json.loads((probe_dir / "verification.json").read_text())
    if manifest.get("autodetect"):
        selection = json.loads((probe_dir / "selection-verification.json").read_text())
        one.lab.require(selection["probe_sha256"] == manifest["probe_sha256"], "DRAM selection proof differs")
    if manifest["operation"] == 2:
        one.lab.require(proof["spl_sha256"] == manifest["spl_sha256"] and
                        (proof["no_dram_mapped"] or proof.get("native_dram_model") and proof.get("sram_before_dram")) and
                        proof["reset"], "early SD control proof differs")
        bridge = json.loads((directory / "handoff-verification.json").read_text())
        one.lab.require(bridge["sha256"] == manifest["bridge_sha256"], "SMC handoff proof differs")
    else:
        one.lab.require(proof["probe_sha256"] == manifest["probe_sha256"] and len(proof["cases"]) == 4,
                        "SRAM control proof differs")
    one.lab.require(manifest["verification"]["bootm_sram_copy_exact"] and
                    manifest["verification"]["gzip_fallback_exact"], "bootm proof missing")
    if manifest["operation"] == 1:
        one.lab.require(control_dir is not None, "DRAM trial requires a hardware control directory")
        control = json.loads((control_dir / "manifest.json").read_text())
        runs = [json.loads(path.read_text()) for path in control_dir.glob("run-*.json")]
        one.lab.require(control["operation"] == 0 and control["probe_sha256"] == manifest["probe_sha256"]
                        and any(run.get("passed") and run["normal_environment_exact"] for run in runs),
                        "this probe has no successful SRAM/SD hardware control")
    if manifest.get("train_native_dram"):
        one.lab.require(control_dir is not None and manifest.get("native_dram"), "native DRAM trial needs its control")
        control = json.loads((control_dir / "manifest.json").read_text())
        runs = [json.loads(path.read_text()) for path in control_dir.glob("run-*.json")]
        one.lab.require(control.get("native_dram") and not control.get("train_native_dram") and
                        control["spl_sha256"] == manifest["spl_sha256"] and any(run.get("passed") for run in runs),
                        "native SPL has no matching successful hardware control")
    if manifest.get("restores_primary"):
        one.lab.require(control_dir is not None and proof.get("restore_primary"), "primary restore needs an emulated and hardware control")
        control = json.loads((control_dir / "manifest.json").read_text())
        runs = [json.loads(path.read_text()) for path in control_dir.glob("run-*.json")]
        one.lab.require(not control.get("restores_primary") and control["spl_sha256"] == manifest["spl_sha256"] and
                        any(run.get("passed") for run in runs), "SPL has no matching scratch restore control")
        source = (probe_dir / "restore-source.bin").read_bytes()
        one.lab.require(one.lab.read_region("boot0")[512:] == source[512:],
                        "warm restore would change boot0 executable; refusing")
    image = (directory / "boot.img").read_bytes()
    one.lab.require(one.lab.sha(image) == manifest["boot_image_sha256"], "trial image differs")
    armed = (directory / "env-armed_yes.bin").read_bytes()
    normal = (directory / "env-normal.bin").read_bytes()
    for key in ("armed_yes", "normal"):
        blob = armed if key == "armed_yes" else normal
        one.lab.require(one.lab.sha(blob) == manifest["recovery_environments"][key], "recovery input differs")
        one.parse_env(blob)
    current_env = one.lab.read_region("env", one.ENV_SIZE)
    if current_env != normal:
        saved = (one.WORK / "baseline-env.bin").read_bytes()
        one.lab.require(current_env == saved and one.lab.sha(saved) ==
                        (one.WORK / "baseline-env.sha256").read_text().strip(), "normal environment differs")
        one.lab.require(one.recovery_env.images(one.parse_env(saved))["normal"] == normal,
                        "trial normal environment is not derived from this device's backup")
        one.lab.write_region("env", normal)
    before = one.lab.sh("cat /proc/sys/kernel/random/boot_id")
    boot0_before = one.lab.device_sha("boot0", 65536)
    baseline = one.trial.image_backup()
    previous = read_result()
    one.lab.require(previous == baseline[RESULT_OFFSET:RESULT_OFFSET + 512] or
                    previous[:4] == b"BOSP" and fnv(previous[:508]) == struct.unpack_from("<I", previous, 508)[0],
                    "unknown result sector; refusing write")
    result = bytearray(512)
    struct.pack_into("<3I", result, 0, 0x50534f42, 1, manifest["operation"])
    struct.pack_into("<I", result, 44 * 4, int(manifest.get("train_native_dram", False)))
    struct.pack_into("<I", result, 45 * 4, 16 if manifest.get("restores_primary") else 0)
    struct.pack_into("<I", result, 508, fnv(result[:508]))
    one.lab.write_region("boot", bytes(result), RESULT_OFFSET)
    if manifest["operation"] == 2:
        restore = (probe_dir / "restore-source.bin").read_bytes()
        one.lab.require(len(restore) == 65536 and one.lab.sha(restore) == manifest["restore_source_sha256"],
                        "early SD restore source differs")
        one.lab.write_region("boot", restore, RESULT_OFFSET + 512)
    one.trial.install(directory / "boot-package.bin", directory / "package-proof.json")
    one.lab.write_region("boot", image, IMAGE_OFFSET)
    one.lab.require(one.lab.device_sha("boot", len(one.trial.BASE_IMAGE.read_bytes())) ==
                    one.lab.sha(one.trial.BASE_IMAGE.read_bytes()), "primary boot image differs")
    one.lab.write_region("env", armed[:512], env_image=armed)
    started = time.monotonic()
    one.lab.reboot(require_frontend=not os_only)
    elapsed = time.monotonic() - started
    result = read_result()
    words = struct.unpack("<128I", result)
    data = one.lab.collect("sram-control" if manifest["operation"] == 0 else "sram-dram", require_frontend=not os_only)
    data.update(operation=manifest["operation"], stage=words[3], sctlr=hex(words[4]), cpsr=hex(words[5]),
                dram_bytes=words[9], dram_ticks=(words[10] - words[8]) & 0xffffffff,
                restart_host_elapsed_s=elapsed, result_sha256=one.lab.sha(result),
                boot0_before_sha256=boot0_before, boot0_after_sha256=one.lab.device_sha("boot0", 65536),
                normal_environment_exact=one.lab.read_region("env", one.ENV_SIZE) == normal)
    if manifest["operation"] == 2:
        data["early_mmc_ticks"] = data.pop("dram_ticks")
        data.update(current_el=words[35] >> 2, card_clock_hz=words[36], bus_width=words[37], mirror_bytes=words[39])
        data["restore_sector"] = words[46]
        if manifest.get("native_dram"):
            data.update(dram_ticks=(words[43] - words[42]) & 0xffffffff,
                        dram_attempts=words[40], selected_dram_type=words[41])
    elif manifest.get("autodetect"):
        data.update(dram_attempts=words[40], selected_dram_type=words[41])
    one.lab.require(words[:3] == (0x50534f42, 1, manifest["operation"]) and
                    fnv(result[:508]) == words[127], "result protocol/CRC differs")
    data["passed"] = words[3] == 2
    if json.loads((probe_dir / "manifest.json").read_text()).get("rtc_handoff_trace"):
        data["rtc_general_purpose"] = rtc_trace.collect(one.lab)
        data["rtc_trace_persisted"] = data["rtc_general_purpose"]["gp15"] == 0x42530002
        data["passed"] = data["passed"] and data["rtc_trace_persisted"]
    (directory / ("run-" + data["boot_id"] + ".bin")).write_bytes(result)
    (directory / ("run-" + data["boot_id"] + ".json")).write_text(json.dumps(data, indent=2) + "\n")
    one.lab.require(before != data["boot_id"] and "baseos_trial=" not in data["cmdline"] and
                    data["normal_environment_exact"], "normal boot fallback was not observed")
    one.lab.require(data["health"][:3] == ["GPU_OK", "WIFI_OK", "FB_OK"] and
                    (os_only or data["health"][-1] == "1"), "fallback health differs")
    keys = ["operation", "stage", "passed", "normal_environment_exact", "boot_id"]
    keys += (["current_el", "card_clock_hz", "bus_width", "mirror_bytes", "early_mmc_ticks"]
             if manifest["operation"] == 2 else ["sctlr", "cpsr", "dram_bytes", "dram_ticks"])
    if manifest.get("autodetect"): keys += ["dram_attempts", "selected_dram_type"]
    print(json.dumps({key: data[key] for key in keys}, indent=2))
    one.lab.require(data["passed"], "SRAM trial did not reach its completion checkpoint")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("probe", type=Path)
    parser.add_argument("--control", type=Path)
    parser.add_argument("--os-only", action="store_true", help="require healthy OS recovery on a card without a frontend")
    args = parser.parse_args()
    run(args.directory, args.probe, args.control, args.os_only)
