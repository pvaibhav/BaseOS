#!/usr/bin/env python3
"""Check the DRAM banks the native loader is built with, for every prepared model.

Usage: python test-dram-candidates.py [-v]
Reads only work/<target>/boot-prefix.img (the stock boot0 at sector 16).
Synthetic cases need no prepared inputs.
"""
import importlib.util
import struct
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
spec = importlib.util.spec_from_file_location("early_mmc_builder", HERE / "build-early-mmc.py")
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)

TARGETS = ("rg28xx", "rg34xx", "rg34xxsp", "rg35xxh", "rg35xxplus", "rg35xxpro",
           "rg35xxsp", "rg40xxh", "rg40xxv", "rgcubexx", "rgsp")
OFFSETS = (0x38, 0x9f4, 0xa74)
DEFAULTS = (0x1b1f1e1c, 0x06060606)


def stock_boot0(target):
    return (ROOT / "work" / target / "boot-prefix.img").read_bytes()[8192:8192 + 65536]


def banks(header):
    return [struct.unpack_from("<32I", header, offset) for offset in OFFSETS]


def header_with(*blocks):
    header = bytearray(65536)
    for offset, block in zip(OFFSETS, blocks):
        struct.pack_into("<32I", header, offset, *block)
    return bytes(header)


def bank(kind, tpr11, tpr12, clock=672):
    words = [0] * 32
    words[0], words[1], words[6], words[26], words[28], words[29] = clock, kind, 0x310a, 0x40808080, tpr11, tpr12
    return tuple(words)


class Synthetic(unittest.TestCase):
    def test_zero_delay_lpddr4_gets_defaults_and_nothing_else_changes(self):
        zero = bank(8, 0, 0)
        got = builder.dram_candidates(header_with(bank(7, 0, 0), bank(3, 0, 0, 792), zero))
        self.assertEqual(got[:2], [bank(7, 0, 0), bank(3, 0, 0, 792)])
        self.assertEqual(got[2], zero[:28] + DEFAULTS + zero[30:])

    def test_partly_tuned_and_tuned_lpddr4_are_untouched(self):
        for tpr11, tpr12 in ((0x1b20201b, 0x05050605), (0, 0x05050605), (0x1b20201b, 0)):
            block = bank(8, tpr11, tpr12)
            self.assertEqual(builder.dram_candidates(header_with(block, block, block)), [block])

    def test_duplicates_collapse_after_the_fill(self):
        zero = bank(8, 0, 0)
        filled = zero[:28] + DEFAULTS + zero[30:]
        self.assertEqual(builder.dram_candidates(header_with(zero, filled, zero)), [filled])


@unittest.skipUnless(all((ROOT / "work" / t / "boot-prefix.img").exists() for t in TARGETS),
                     "prepared model inputs are not linked into work/")
class Fleet(unittest.TestCase):
    def test_every_model(self):
        for target in TARGETS:
            with self.subTest(target=target):
                raw = banks(stock_boot0(target))
                got = builder.dram_candidates(stock_boot0(target))
                expected = []
                for block in raw:
                    if block[1] == 8 and block[28] == block[29] == 0:
                        block = block[:28] + DEFAULTS + block[30:]
                    if block not in expected: expected.append(block)
                self.assertEqual(got, expected)
                # The first bank (the one vendor boot0 tunes per model) is never changed.
                self.assertEqual(got[0], raw[0])
                self.assertFalse([b for b in got if b[1] == 8 and b[28] == b[29] == 0])

    def test_which_models_change(self):
        filled = {}
        for target in TARGETS:
            raw = banks(stock_boot0(target))
            got = builder.dram_candidates(stock_boot0(target))
            filled[target] = [i for i, block in enumerate(got) if block not in raw]
        # Every boot0 has the same generic zero-delay LPDDR4 bank at 0xa74, except
        # RG28XX, whose 0xa74 bank is DDR3. It is the last candidate everywhere.
        self.assertEqual(filled["rg28xx"], [])
        for target in TARGETS[1:]:
            self.assertEqual(filled[target], [2], target)


if __name__ == "__main__":
    unittest.main(argv=sys.argv[:1] + sys.argv[1:])
