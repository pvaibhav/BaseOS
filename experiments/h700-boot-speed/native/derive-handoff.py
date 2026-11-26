#!/usr/bin/env python3
"""Derive the release framebuffer allocations by running the pinned vendor loader.

Host only. The checked-in contract is regenerated with --jobs 4. No native
addresses are supplied to the vendor allocator. Native refuses all RAM sizes
outside this measured set; the full parity gate crosses every DRAM bank/strap.
"""
import argparse
from concurrent.futures import ProcessPoolExecutor
import importlib.util
import json
from pathlib import Path
import struct
import gzip

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]


def replay(target):
    spec = importlib.util.spec_from_file_location("derive_parity", HERE / "verify-handoff-parity.py")
    gate = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gate)
    prefix = (ROOT / "work" / target / "boot-prefix.img").read_bytes()
    package = (ROOT / "work" / target / "boot-gzip/boot-package.bin").read_bytes()
    boot = (ROOT / "work" / target / "boot-gzip/boot.img").read_bytes()
    contract = json.loads((HERE.parent / "evidence/compatibility.json").read_text())["targets"][target]
    assert gate.sha(package) == contract["package_sha256"]
    parts = gate.gpt(prefix)
    env_at = parts["env"][0]
    env_raw = prefix[env_at:env_at + 131072]
    env = gate.environment(env_raw)
    card = gate.Card([(0, prefix), (parts["boot"][0], boot)])
    bmp, entry_at = gate.card_logo(card, parts)
    missing = gate.Card(card.extents + [(entry_at, b"\xe5")])
    words = [int(v, 0) for v in contract["dram_candidates"][0]["words"]]
    kernel_bytes = len(gzip.decompress(boot[2048:2048 + struct.unpack_from("<I", boot, 8)[0]]))
    allocations = {}
    for ram in (0x40000000, 0x80000000):
        words[7] = ((ram >> 20) << 16) | (words[7] & 0xffff)
        inputs = dict(dram_words=words, ram_bytes=ram, serial=gate.SERIAL, boot_reason=5, pe6_high=False)
        values = []
        for present, source in ((True, card), (False, missing)):
            directory = ROOT / "work/handoff-contract" / target / f"{ram >> 20}-{int(present)}"
            capture = gate.stock_capture(directory, package, source, env, inputs, kernel_bytes)
            tree = (directory / "fdt.bin").read_bytes()
            reserve, = [t for t in capture["bootargs"].split() if t.startswith("disp_reserve=")]
            size, address = [int(v, 0) for v in reserve.split("=", 1)[1].split(",")]
            raw = gate.fdt.get(tree, gate.DISPLAY, "boot_fb0")
            if present:
                fields = [int(v, 16) for v in raw.rstrip(b"\0").decode().split(",")]
                assert address == fields[0] and size == fields[1] * fields[2] * 4
            else:
                assert raw is None, "vendor publishes boot_fb0 without a logo"
            assert not any(base < address + size and address < base + length for base, length in capture["reservations"])
            values.append(address)
        assert values[0] == values[1], "logo presence changes vendor allocation"
        allocations[str(ram)] = values[0]
    offsets = {0x40000000 + int(ram) - address for ram, address in allocations.items()}
    assert len(offsets) == 1, "vendor allocation is not a constant offset for the proven RAM sizes"
    return target, dict(package_sha256=gate.sha(package), environment_sha256=gate.sha(env_raw),
                        prefix_sha256=gate.sha(prefix), logo_sha256=gate.sha(bmp),
                        framebuffer_from_ram_end=offsets.pop(), allocations=allocations)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--jobs", type=int, default=4)
    parser.add_argument("--target", action="append")
    parser.add_argument("--output", type=Path, default=HERE / "vendor-handoff.json")
    args = parser.parse_args()
    targets = args.target or sorted(json.loads((HERE.parent / "evidence/compatibility.json").read_text())["targets"])
    result = dict(version=1, ram_bytes=[0x40000000, 0x80000000],
                  derivation="actual vendor reserve/initr_malloc/env hash table/display/FAT/boot sequence, with and without logo",
                  models={})
    with ProcessPoolExecutor(max_workers=args.jobs) as pool:
        for target, record in pool.map(replay, targets):
            result["models"][target] = record
            print(target, json.dumps(record), flush=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
