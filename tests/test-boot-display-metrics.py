#!/usr/bin/env python3
"""Correct framebuffer bytes must not hide a blank display-engine output."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "experiments/h700-boot-speed/native"))
from display_metrics import compare_pixels


class DisplayMetrics(unittest.TestCase):
    def test_menu_matches_despite_unused_alpha_byte(self):
        menu = bytes((1, 2, 3, 255, 4, 5, 6, 255))
        output = bytes((1, 2, 3, 0, 4, 5, 6, 0))
        self.assertEqual(compare_pixels(output, menu, 2, 1, 8), {
            "matching_rgb_fraction": 1.0, "minimum_rgb_fraction": 0.98,
            "scanout_nonuniform": True, "passed": True})

    def test_observed_blank_output_fails_with_correct_menu_in_memory(self):
        menu = bytes((1, 2, 3, 255, 4, 5, 6, 255))
        self.assertFalse(compare_pixels(bytes(8), menu, 2, 1, 8)["passed"])

    def test_two_matching_blank_buffers_do_not_prove_a_menu(self):
        self.assertFalse(compare_pixels(bytes(8), bytes(8), 2, 1, 8)["passed"])

    def test_current_page_and_stride_padding_are_respected(self):
        menu = bytes((1, 2, 3, 255, 4, 5, 6, 255))
        framebuffer = bytes(12) + menu + bytes(4)
        self.assertTrue(compare_pixels(menu, framebuffer, 2, 1, 12, 1)["passed"])

    def test_short_scanout_is_rejected(self):
        with self.assertRaises(ValueError):
            compare_pixels(bytes(4), bytes(8), 2, 1, 8)


if __name__ == "__main__":
    unittest.main()
