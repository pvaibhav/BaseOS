#!/usr/bin/env python3
"""Print the U-Boot counter stamps left in low DRAM by boot-package-stamped.bin.

Input is the text of, on the device:
    dd if=/dev/mem bs=4096 skip=262144 count=1 | od -An -tx4 -v
(physical page 0x40000000; magic "STAM" at +0xF8, slot n at +0x100 + 8n,
each a 64-bit CNTPCT value at 24 MHz, low word first).

    read-stamps.py dump.txt                      # saved od output ("-" = stdin)
    read-stamps.py --adb [-s SERIAL]             # read it now over adb (read-only)
    read-stamps.py dump.txt --pre-kernel 1.234   # or the whole log line:
    read-stamps.py dump.txt --pre-kernel "BaseOS boot stages: pre-kernel 1.234 s"
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys

HZ = 24_000_000
MAGIC, MAGIC_WORD, FIRST_SLOT_WORD = 0x5354414D, 0xF8 // 4, 0x100 // 4
SLOTS = [
    "logo routine returned (board_late_init 0x2B22)",
    "vendor post-logo steps done (main_loop called, 0xF13E)",
    "card read starts (do_sunxi_flash entry, 0xC1FC)",
    "card read done, bootm starts (do_bootm entry, 0x7E80)",
    "kernel unpacked (boot_prep_linux called, 0x152A)",
    "about to jump to the kernel (boot_jump_linux, 0x1482)",
]
DEVICE_COMMAND = "dd if=/dev/mem bs=4096 skip=262144 count=1 2>/dev/null | od -An -tx4 -v"


def words_of(text: str) -> list[int]:
    words = [int(token, 16) for line in text.splitlines() for token in line.split()
             if re.fullmatch(r"[0-9a-fA-F]{8}", token)]
    if len(words) < FIRST_SLOT_WORD + 12:
        sys.exit(f"only {len(words)} words of input; expected the od -tx4 dump of one 4096-byte page")
    return words


def pre_kernel_seconds(value: str) -> float:
    match = (re.search(r"pre-kernel\s+([0-9.]+)\s*s", value)
             or re.search(r"Startup:\s+([0-9.]+)\s*s before Linux", value)
             or re.fullmatch(r"\s*([0-9.]+)\s*s?\s*", value))
    if not match:
        sys.exit(f"cannot read a pre-kernel time from {value!r}")
    return float(match.group(1))


def ms(ticks: int) -> str:
    return f"{ticks * 1000 / HZ:10.3f} ms"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("dump", nargs="?", help="file holding the od output, or - for stdin")
    parser.add_argument("--adb", action="store_true", help="run the read-only dd|od on the device through adb")
    parser.add_argument("-s", dest="serial", help="adb device serial (with --adb)")
    parser.add_argument("--pre-kernel", help='seconds, or the line "BaseOS boot stages: pre-kernel X s"')
    args = parser.parse_args()
    if args.adb:
        command = ["adb"] + (["-s", args.serial] if args.serial else []) + ["shell", DEVICE_COMMAND]
        text = subprocess.run(command, check=True, capture_output=True, text=True).stdout
    elif args.dump:
        text = sys.stdin.read() if args.dump == "-" else open(args.dump).read()
    else:
        parser.error("give a dump file, - for stdin, or --adb")
    words = words_of(text)
    if words[MAGIC_WORD] != MAGIC:
        print(f"WARNING: magic at 0x400000F8 is {words[MAGIC_WORD]:08x}, not {MAGIC:08x} ('STAM'): stub 0 did not"
              " run on this boot, or the page was reused since; the values below are probably not stamps")
    stamps = [words[FIRST_SLOT_WORD + 2 * n] | words[FIRST_SLOT_WORD + 2 * n + 1] << 32 for n in range(6)]
    print(f"{'slot':4}  {'counter':>18}  {'seconds':>11}  {'since previous':>16}  meaning")
    previous = None
    for n, (value, meaning) in enumerate(zip(stamps, SLOTS)):
        delta = ""
        if value and previous:
            delta = ms(value - previous) + ("  (NOT increasing)" if value < previous else "")
        print(f"{n:4}  {value:#18x}  {value / HZ:11.6f}  {delta:>16}  {meaning}" + ("  [not written]" if not value else ""))
        previous = value or previous
    written = [v for v in stamps if v]
    if len(written) >= 2:
        print(f"first to last stamp: {ms(written[-1] - written[0]).strip()}")
    if args.pre_kernel is not None and written:
        origin = round(pre_kernel_seconds(args.pre_kernel) * HZ)
        last = max(n for n, v in enumerate(stamps) if v)
        print(f"kernel clock origin (pre-kernel x 24e6): {origin:#x}; from stamp {last} to it: {ms(origin - stamps[last]).strip()}"
              f"; from stamp 0 to it: {ms(origin - stamps[0]).strip() if stamps[0] else 'n/a'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
