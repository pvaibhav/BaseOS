#!/usr/bin/env python3
"""Alternate already-proved one-shot images; keep every run and failure."""
import argparse
import json
import statistics
import time
from pathlib import Path

import oneshot


def compare(baseline: str, candidate: str, pairs: int, output: Path):
    oneshot.lab.require(not output.exists(), "comparison output already exists")
    report = {
        "claim": "The retained-boot0 fast path reduces counter-to-frontend and first-menu time.",
        "limiter": "Unchanged boot0 takes about 2.61 seconds before U-Boot entry.",
        "baseline": baseline, "candidate": candidate, "pairs": pairs, "runs": [],
    }
    def save():
        output.write_text(json.dumps(report, indent=2) + "\n")
    save()
    # Stage both large images before measuring. Subsequent arms write one sector.
    for label in (baseline, candidate):
        oneshot.stage(label)
    time.sleep(10)
    try:
        for pair in range(pairs):
            for label in (baseline, candidate):
                oneshot.stage(label)
                data = oneshot.run(label)
                data.update(label=label, pair=pair)
                report["runs"].append(data)
                save()
                print(json.dumps({key: data.get(key) for key in (
                    "label", "pair", "boot_id", "pre_kernel_s", "combined_s",
                    "settled_framebuffer_counter_s")}), flush=True)
    except Exception as error:
        report["failure"] = repr(error)
        save()
        raise
    report["summary"] = {}
    for label in (baseline, candidate):
        runs = [run for run in report["runs"] if run["label"] == label]
        report["summary"][label] = {
            key: {"median": statistics.median(values), "min": min(values), "max": max(values)}
            for key in ("pre_kernel_s", "combined_s", "settled_framebuffer_counter_s")
            if (values := [run[key] for run in runs if key in run])
        }
    save()
    print(json.dumps(report["summary"], indent=2), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("baseline")
    parser.add_argument("candidate")
    parser.add_argument("output", type=Path)
    parser.add_argument("--pairs", type=int, default=5)
    args = parser.parse_args()
    oneshot.lab.require(args.pairs >= 5, "a comparison needs at least five pairs")
    compare(args.baseline, args.candidate, args.pairs, args.output)
