#!/usr/bin/env python3
"""Enter a proved SPL through BROM, restore stock boot0 before DRAM, and reset."""
import argparse
import json
import struct
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import qualify
sys.path.insert(0, str(HERE.parent / "fastboot"))
import oneshot as one
import rtc_trace
import boot_observer
from lab_devices import PROFILES


def fnv(data):
    value = 2166136261
    for byte in data: value = ((value ^ byte) * 16777619) & 0xffffffff
    return value


def run(probe, warm, output, handoff=False, native_loader=None, rmr2_witness=False):
    lab = one.lab
    lab.require(not output.exists(), "choose a fresh evidence directory")
    if native_loader:
        qualification = qualify.qualify(probe, native_loader)
        output.mkdir(parents=True)
        (output / "spl-qualification.json").write_text(json.dumps(qualification, indent=2) + "\n")
        lab.require(qualification["passed"], "native host qualification failed:\n" +
                    "\n".join(qualification["errors"]))
    lab.identity()
    one.trial.checked_base()
    profile = PROFILES.get(lab.TARGET)
    lab.require(profile and lab.SERIAL == profile["serial"] and one.trial.PROFILE_TARGET == lab.TARGET,
                "BROM control requires an explicitly pinned lab device")
    manifest = json.loads((probe / "manifest.json").read_text())
    if rmr2_witness:
        lab.require(not handoff and not native_loader and manifest.get("rmr2_preflight") and
                    manifest.get("target") == lab.TARGET == "rg34xxsp" and not manifest["native_dram"],
                    "RMR2 witness requires its pinned no-DRAM preflight binary")
        purpose = json.loads((probe / "verification-summary.json").read_text())
        lab.require(purpose["passed"] and purpose["spl_sha256"] == manifest["spl_sha256"] and
                    purpose["witness_sha256"] == manifest["witness_sha256"] and
                    purpose["only_gp14_gp15_written"] and purpose["one_shot_cookie_consumed_before_rmr"] and
                    purpose["post_rmr_watchdog_readback_flags_verified"], "RMR2 purpose proof differs")
        for filename, proof in purpose["proofs"].items():
            lab.require(lab.sha((probe / filename).read_bytes()) == proof["sha256"], "RMR2 proof changed")
    lab.require(bool(native_loader) == bool(manifest.get("native_loader")), "matching --native-loader is required")
    native = manifest.get("native_loader")
    if native:
        lab.require(handoff and native == json.loads((native_loader / "manifest.json").read_text()),
                    "native bundle identity differs")
        loader_proof = json.loads((native_loader / "verification.json").read_text())
        cross_proof = json.loads((native_loader / "verification-from-spl.json").read_text())
        monitor_proof = json.loads((native_loader / "monitor-verification.json").read_text())
        cache_proof = json.loads((probe / "cache-verification.json").read_text())
        pll_proof = json.loads((probe / "vendor-pll-verification.json").read_text())
        lab.require(pll_proof["passed"] and pll_proof["spl_sha256"] == manifest["spl_sha256"] and
                    pll_proof["boot0_sha256"] == manifest["restore_source_sha256"],
                    "vendor media PLL power compatibility proof missing")
        lab.require(cache_proof["passed"] and cache_proof["spl_sha256"] == manifest["spl_sha256"] and
                    cache_proof["cases"][-1]["poc_rejected"], "cache cleanup mutation proof missing")
        lab.require(loader_proof["passed"] and loader_proof["loader_sha256"] == native["loader_sha256"] and
                    loader_proof["image_sha256"] == native["image_sha256"] and
                    loader_proof.get("live_fdt_static_properties_exact") and cross_proof["passed"] and
                    cross_proof["loader_sha256"] == native["loader_sha256"] and
                    cross_proof["spl_sha256"] == manifest["spl_sha256"] and
                    monitor_proof["native_loader_sha256"] == native["loader_sha256"] and
                    not monitor_proof["errors"] and monitor_proof["eret"][-1] == {
                        "entry": hex(native.get("kernel_smc_entry", 0x40080000)), "spsr": "0x3c5", "x0": "0x44000000",
                        "x1": "0x0", "x2": "0x0", "x3": "0x0"}, "native loader/monitor proof missing")
        if "kernel_smc_entry" in native:
            lab.require(monitor_proof.get("native_kernel_entry_r0_r3") == ["0x44000000", "0x0", "0x0", "0x0"],
                        "native A64 entry probe ABI proof missing")
        if native.get("entry_checksum_witness"):
            lab.require(loader_proof.get("entry_checksum_witness_verified") and
                        cross_proof.get("entry_checksum_witness_verified") and
                        monitor_proof["rtc_trace"][-1] == 0x42530033,
                        "post-clean kernel/FDT checksum witness proof missing")
        if native.get("kernel_exception_witness"):
            idmap_proof = json.loads((native_loader / "kernel-idmap-verification.json").read_text())
            lab.require(loader_proof.get("kernel_exception_witness_verified") and
                        cross_proof.get("kernel_exception_witness_verified") and idmap_proof["passed"] and
                        idmap_proof["kernel_sha256"] == native["kernel_sha256"], "early fault-vector proof missing")
        if native.get("monitor_psci_probe"):
            lab.require(monitor_proof.get("psci_query_probe", {}).get("passed"), "PSCI query proof missing")
        if native.get("kernel_initcall_probe"):
            initcall_proof = json.loads((native_loader / "initcall-verification.json").read_text())
            lab.require(initcall_proof["passed"] and initcall_proof["kernel_sha256"] == native["kernel_sha256"] and
                        initcall_proof["monitor_sha256"] == native["monitor_sha256"], "initcall ABI proof missing")
    entry = json.loads((probe / "brom-entry-verification.json").read_text())
    emulation = json.loads((probe / "verification-el3-control-restore.json").read_text())
    if handoff:
        handoff_proof = json.loads((probe / "verification-el3-handoff-restore.json").read_text())
        lab.require(handoff_proof["spl_sha256"] == manifest["spl_sha256"] and
                    handoff_proof["monitor_handoff"] and handoff_proof["stage"] == 34 and not handoff_proof["errors"],
                    "complete monitor handoff proof missing")
        if native:
            lab.require(handoff_proof.get("cache_visibility", {}).get("complete_set_way_cleans") == 1,
                        "complete native preload PoC proof missing")
    control = json.loads((warm / "manifest.json").read_text())
    runs = [json.loads(path.read_text()) for path in warm.glob("run-*.json")]
    spl, source = (probe / "spl.bin").read_bytes(), (probe / "restore-source.bin").read_bytes()
    lab.require(lab.sha(spl) == manifest["spl_sha256"] == entry["spl_sha256"] ==
                emulation["spl_sha256"] == control["spl_sha256"], "SPL proof identity differs")
    lab.require(entry["egon_checksum_valid"] and len(entry["cases"]) == 2 and emulation["restore_primary"] and
                emulation["el3_registers_modeled"] and emulation["entry_el"] == 3 and
                emulation["sram_before_dram"] and not emulation["training_modeled"] and emulation["reset"] and
                not emulation["errors"], "BROM/EL3 restore proof missing")
    lab.require(control["restores_primary"] and not control["train_native_dram"] and any(
        row.get("passed") and row.get("restore_sector") == 16 and row["normal_environment_exact"] for row in runs),
        "identical SPL has no successful primary restore hardware control")
    if manifest.get("rtc_handoff_trace"):
        lab.require(any(row.get("passed") and row.get("rtc_trace_persisted") for row in runs),
                    "RTC trace has no hardware persistence proof")
        bl33_trace = json.loads((probe / "bl33-trace-verification.json").read_text())
        monitor_trace = json.loads((probe / "monitor-trace-verification.json").read_text())
        lab.require(bl33_trace["spl_sha256"] == monitor_trace["traced_spl_sha256"] == manifest["spl_sha256"] and
                    bl33_trace["r0_r3_preserved"] and bl33_trace["monitor_repeat_safe"] and
                    monitor_trace["rtc_trace"] == [0x4a000000] and not monitor_trace["errors"] and
                    all(int(monitor_trace["el3_translation_descriptors"][address][-1], 16) & (1 << 54) == 0
                        for address in ("0x480000c0", "0x480000dc")),
                    "monitor/BL33 trace instruction proof missing")
    lab.require(len(source) == 65536 and lab.sha(source) == manifest["restore_source_sha256"], "restore source differs")
    normal = (warm / "env-normal.bin").read_bytes()
    lab.require(lab.read_region("env", one.ENV_SIZE) == normal, "normal environment differs")
    package = lab.read_region("toc1")
    lab.require(lab.sha(package) == control["patched_package_sha256"], "normal-boot package differs")
    if handoff and not native: lab.require(lab.sha(package) == manifest["handoff_package_sha256"], "SPL TOC pin differs")
    original = lab.read_region("boot0")
    lab.require(original[512:] == source[512:] and original[4:12] == b"eGON.BT0", "on-card first stage is not stock")
    lab.require(one.trial.kg.checksum(original, 12) == one.trial.kg.word(original, 12), "stock boot0 checksum differs")
    lab.require(len(spl) <= len(original) and one.trial.kg.word(spl, 16) == len(spl), "SPL BROM extent differs")
    candidate = spl + original[len(spl):]
    if not native_loader:
        output.mkdir(parents=True)
    (output / "before-boot0.bin").write_bytes(original)
    (output / "candidate-boot0.bin").write_bytes(candidate)
    (output / "normal-package.bin").write_bytes(package)
    if native and native.get("kernel_initcall_probe"):
        before_rtc = rtc_trace.collect(lab)
        lab.require(before_rtc["gp14"] == 0, "clear prior initcall trace before arming a new trial")
        (output / "before-rtc.json").write_text(json.dumps(before_rtc, indent=2) + "\n")
    before = lab.sh("cat /proc/sys/kernel/random/boot_id")
    if rmr2_witness:
        before_rtc = rtc_trace.collect(lab)
        lab.require(before_rtc["gp14"] == before_rtc["gp15"] == 0, "RMR2 witness requires its original empty RTC pair")
        (output / "before-rtc.json").write_text(json.dumps(before_rtc, indent=2) + "\n")
    observer_digest = boot_observer.install(lab) if handoff else None
    if handoff:
        observer_proof = json.loads((one.WORK / "observer-inactive-control.json").read_text())
        lab.require(observer_proof["passed"] and observer_proof["sha256"] == observer_digest and
                    observer_proof["observer_module_sha256"] == lab.sha(Path(boot_observer.__file__).read_bytes()),
                    "observer has no matching inactive-watchdog control")
    staging = []
    if native:
        # The normal recovery TOC and original primary kernel remain intact.
        # These spare extents are no longer needed by the completed warm test.
        for name, offset in (("firmware", 40), ("image", 48)):
            payload = (native_loader / f"{name}.bin").read_bytes()
            lab.require(lab.sha(payload) == native[name + "_sha256"], "native payload pin differs")
            changed = lab.device_sha("boot", len(payload), offset * 1024 * 1024) != native[name + "_sha256"]
            if changed:
                lab.write_region("boot", payload, offset * 1024 * 1024)
            staging.append({"name": name, "written": changed, "sha256": native[name + "_sha256"]})
    journal = bytearray(512)
    struct.pack_into("<4I", journal, 0, 0x50534f42, 1, 2, 10)
    struct.pack_into("<I", journal, 45 * 4, 16)
    struct.pack_into("<I", journal, 47 * 4, int(handoff))
    if rmr2_witness:
        struct.pack_into("<I", journal, 48 * 4, manifest["journal_cookie"])
    struct.pack_into("<I", journal, 508, fnv(journal[:508]))
    lab.write_region("boot", bytes(journal), 24 * 1024 * 1024)
    lab.write_region("boot", source, 24 * 1024 * 1024 + 512)
    pending = {"before_boot_id": before, "spl_sha256": manifest["spl_sha256"],
               "candidate_boot0_sha256": lab.sha(candidate), "restore_source_sha256": lab.sha(source),
               "normal_package_sha256": lab.sha(package), "native_staging": staging}
    (output / "pending.json").write_text(json.dumps(pending, indent=2) + "\n")
    started = time.monotonic()
    stopped = None
    observer = boot_observer.Observer(lab, before, output / "early-adb.jsonl") if handoff else None
    try:
        lab.write_region("boot0", candidate)
        if observer: observer.start()
        lab.reboot()
        if handoff:
            subprocess.run(lab.ADB + ["push", str(one.WORK / "watchdog-probe"), "/tmp/watchdog-probe"],
                           check=True, capture_output=True)
            stopped = lab.sh("chmod 755 /tmp/watchdog-probe; /tmp/watchdog-probe --disarm")
            lab.require("wdt_mode_after_stop=00000000" in stopped, "trial watchdog did not stop")
    except BaseException:
        # A host-side failure before reboot still leaves ADB available. Restore
        # immediately in that state, even if the candidate write was partial.
        try:
            if lab.sh("cat /proc/sys/kernel/random/boot_id", 3) == before:
                lab.write_region("boot0", original)
        except Exception:
            pass
        raise
    finally:
        observations = observer.finish() if observer else []
    recovery_elapsed = time.monotonic() - started
    lab.REGIONS["spl-result"] = ("/dev/mmcblk0", 0x56000, 512)
    result = lab.read_region("spl-result")
    words = struct.unpack("<128I", result)
    after = lab.read_region("boot0")
    data = lab.collect("brom-vendor-handoff" if handoff else "brom-restore-control")
    if handoff and lab.sh("test -s /run/lab-trace && echo present || true") == "present":
        for attempt in range(10):
            trace = lab.sh("cat /run/lab-trace")
            settled = one.settled_frame(trace, data["legacy_handoff_s"])
            if settled is not None:
                data["settled_framebuffer_s"] = settled
                data["settled_framebuffer_counter_s"] = data["pre_kernel_s"] + settled
                break
            time.sleep(1)
        (output / "frame.trace").write_text(trace + "\n")
    data.update(pending, current_el=words[35] >> 2, stage=words[3], restore_sector=words[46],
                restored_bytes=words[39], normal_environment_exact=lab.read_region("env", one.ENV_SIZE) == normal,
                stock_boot0_body_exact=after[512:] == source[512:], benchmark_valid=False,
                host_recovery_elapsed_s=recovery_elapsed, watchdog_after_recovery=stopped,
                early_boot_observer_sha256=observer_digest, early_adb_observations=observations,
                note=("Single first-stage handoff trial; repeated benchmarking is still required." if handoff else
                      "This control resets into stock boot0 before Linux; its boot time is not a candidate benchmark."))
    if observer:
        data["observer_status"] = {"attempts": observer.attempts, "failures": observer.failures,
                                   "last_error": observer.last_error}
    if handoff:
        if manifest.get("rtc_handoff_trace") or native and native.get("native_diagnostic"):
            data["rtc_general_purpose"] = rtc_trace.collect(lab)
            if native and native.get("kernel_initcall_probe"):
                data["native_linux_initcall_pc_low32"] = hex(data["rtc_general_purpose"]["gp14"])
            value = data["rtc_general_purpose"]["gp15"]
            data["rtc_handoff_stage"] = value & 0xffff if value >> 16 == 0x4253 else None
            data["monitor_and_bl33_entry_verified"] = data["rtc_handoff_stage"] == 37
            if native and data["rtc_handoff_stage"] is not None:
                stage = data["rtc_handoff_stage"]
                data["monitor_and_bl33_entry_verified"] = 40 <= stage <= 51 or bool(stage & 0x8000)
                data["native_a64_probe_entry_verified"] = 49 <= stage <= 51
                data["native_kernel_backing_bytes_verified"] = stage in (50, 51)
                data["native_fdt_backing_bytes_verified"] = stage == 51
                if stage & 0x8000: data["native_loader_error"] = stage & 0x7fff
            if native and native.get("monitor_psci_probe") and value >> 24 in (0x84, 0x94, 0xc4, 0xd4):
                data["native_linux_psci_id"] = hex(value & ~0x10000000)
                data["native_linux_return_observed_after_psci"] = bool(value & 0x10000000)
            elif value >> 16 != 0x4253 and native and native.get("kernel_exception_witness"):
                data["native_kernel_exception_pc_low32"] = hex(value)
            elif value >> 16 != 0x4253 and 0x40000000 <= value < 0xc0000000:
                data.update(rtc_handoff_stage=36, rtc_monitor_exit_pc=hex(value))
        tree = bytes.fromhex(lab.sh("od -An -tx1 -v /sys/firmware/fdt", 60))
        at = tree.find(b"BSPLv001")
        data["spl_marker_found"] = bytes.fromhex(native["generation"]) in tree if native else at >= 0
        if native:
            sys.path.insert(0, str(HERE.parent))
            import fdt
            properties = {(path, name): value for path, name, _, value in fdt.walk(tree)}
            data["spl_marker_found"] = properties.get(("/chosen", "baseos,native-boot")) == bytes.fromhex(native["generation"])
            data["native_generation"] = native["generation"]
            stamps = properties.get(("/chosen", "baseos,native-timing"))
            if stamps:
                data["native_stage_counter_s"] = [value / 24000000 for value in struct.unpack(">7Q", stamps)]
            (output / "kernel-fdt.bin").write_bytes(tree)
        elif at >= 0:
            _, ticks, memory, kind = struct.unpack_from("<4Q", tree, at)
            data.update(spl_marker_counter_s=ticks / 24000000, spl_marker_memory_bytes=memory,
                        spl_marker_dram_type=kind)
    data["stock_boot0_checksum_valid"] = one.trial.kg.checksum(after, 12) == one.trial.kg.word(after, 12)
    if rmr2_witness:
        rtc = rtc_trace.collect(lab)
        mode, vector = rtc["gp14"] & 31, rtc["gp15"]
        completed = mode in (0x10, 0x11, 0x12, 0x13, 0x16, 0x17, 0x1a, 0x1b, 0x1f) and (
            vector == 0xffffffff and mode != 0x16 or mode == 0x16 and vector & 31 == 0)
        unchanged = all(rtc[key] == before_rtc[key] for key in before_rtc if key not in ("gp14", "gp15"))
        data.update(rtc_general_purpose=rtc, rmr2_witness_completed=completed,
                    rmr2_entry_mode=hex(mode), rmr2_scr_low16=hex(rtc["gp14"] >> 16) if mode == 0x16 else None,
                    rmr2_mvbar=hex(vector) if mode == 0x16 else None,
                    rmr2_watchdog_enabled=bool(rtc["gp14"] & 0x400),
                    rmr2_watchdog_b1_retained=bool(rtc["gp14"] & 0x800),
                    rmr2_watchdog_system_reset_retained=bool(rtc["gp14"] & 0x1000),
                    other_rtc_words_unchanged=unchanged,
                    rmr2_cookie_consumed=words[48] == 0 and words[49] == manifest["journal_cookie"] and
                                         words[50] == manifest["witness_fnv"])
    data["host_restore_needed"] = not data["stock_boot0_body_exact"] or not data["stock_boot0_checksum_valid"]
    if data["host_restore_needed"]:
        lab.write_region("boot0", original)
    (output / "result.bin").write_bytes(result)
    (output / "after-boot0.bin").write_bytes(after)
    data["passed"] = (words[:3] == (0x50534f42, 1, 2) and fnv(result[:508]) == words[127] and
                      data["current_el"] == 3 and data["stage"] == (24 if rmr2_witness else 34 if handoff else 2) and data["restore_sector"] == 16 and
                      data["restored_bytes"] == 65536 and data["normal_environment_exact"] and
                      data["stock_boot0_body_exact"] and data["stock_boot0_checksum_valid"] and data["boot_id"] != before and
                      lab.device_sha("toc1", len(package)) == lab.sha(package) and
                      data["health"][:3] == ["GPU_OK", "WIFI_OK", "FB_OK"] and data["health"][-1] == "1")
    if handoff: data["passed"] = data["passed"] and data["spl_marker_found"]
    if rmr2_witness:
        data["passed"] = data["passed"] and all(data[key] for key in ("rmr2_witness_completed",
            "rmr2_watchdog_enabled", "rmr2_watchdog_b1_retained", "rmr2_watchdog_system_reset_retained",
            "other_rtc_words_unchanged", "rmr2_cookie_consumed"))
    (output / "run.json").write_text(json.dumps(data, indent=2) + "\n")
    keys = ["passed", "current_el", "stage", "restore_sector", "restored_bytes",
            "normal_environment_exact", "stock_boot0_body_exact", "boot_id"]
    if handoff:
        keys += ["spl_marker_found", "host_recovery_elapsed_s"]
        if manifest.get("rtc_handoff_trace") or native and native.get("native_diagnostic"):
            keys.append("rtc_handoff_stage")
    print(json.dumps({key: data[key] for key in keys}, indent=2))
    lab.require(data["passed"], "BROM control did not prove EL3 entry and normal recovery")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("probe", type=Path)
    parser.add_argument("warm_restore", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--handoff", action="store_true", help="continue through PMIC/DRAM and vendor monitor after restore")
    parser.add_argument("--native-loader", type=Path)
    parser.add_argument("--rmr2-witness", action="store_true", help="run the pinned restore-first A32 state witness; no stock continuation")
    args = parser.parse_args()
    run(args.probe, args.warm_restore, args.output, args.handoff, args.native_loader, args.rmr2_witness)
