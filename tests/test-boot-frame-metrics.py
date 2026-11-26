#!/usr/bin/env python3
"""A changing clock must not move the measured first menu frame."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "experiments/h700-boot-speed/fastboot"))
from frame_metrics import settled_frame


def trace(samples):
    return "\n".join(f"{at} 0 0 0 0 {digest} card" for at, digest in samples)


class FrameMetrics(unittest.TestCase):
    def test_later_clock_repaint_does_not_replace_first_menu(self):
        samples = [(1.3, "logo"), (3.8, "logo"), (3.9, "fade1"), (4.0, "fade2"),
                   (4.2, "menu"), (4.5, "menu"), (8.9, "menu"), (9.0, "new-clock"), (12, "new-clock")]
        self.assertEqual(settled_frame(trace(samples), 1.4), 4.2)

    def test_unchanged_logo_is_no_successful_frame(self):
        self.assertIsNone(settled_frame(trace([(1.3, "logo"), (12, "logo")]), 1.4))

    def test_pre_handoff_transition_is_not_frontend_completion(self):
        samples = [(0.2, "zero"), (0.4, "logo"), (1.2, "logo"), (4.1, "menu"), (4.4, "menu")]
        self.assertEqual(settled_frame(trace(samples), 1.4), 4.1)


if __name__ == "__main__":
    unittest.main()
