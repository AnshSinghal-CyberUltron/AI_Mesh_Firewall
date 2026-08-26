#!/usr/bin/env python3
"""T-C3 recycle-count math + harness contract (no live Docker)."""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("live_tc3", HERE / "live_tc3.py")
TC3 = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(TC3)


class Tc3RecycleMathTests(unittest.TestCase):
    def test_production_60min_cadence_does_not_hit_2000(self):
        # 4 heavies / 10s * 3600s = 1440 cluster requests / 3 workers ≈ 480.
        lo, hi = TC3.expected_worker_recycles(480, 2000, 100)
        self.assertEqual((lo, hi), (0, 0))

    def test_jitter_zero_exact_threshold(self):
        self.assertEqual(TC3.expected_worker_recycles(30, 30, 0), (1, 1))
        self.assertEqual(TC3.expected_worker_recycles(59, 30, 0), (1, 1))
        self.assertEqual(TC3.expected_worker_recycles(60, 30, 0), (2, 2))

    def test_jitter_widens_min_bound(self):
        lo, hi = TC3.expected_worker_recycles(100, 30, 10)
        self.assertEqual(hi, 100 // 30)
        self.assertEqual(lo, 100 // 40)
        self.assertLessEqual(lo, hi)

    def test_disabled_max_requests_is_zero(self):
        self.assertEqual(TC3.expected_worker_recycles(10_000, 0, 100), (0, 0))
        self.assertEqual(TC3.expected_worker_recycles(0, 30, 0), (0, 0))

    def test_parse_cmdline(self):
        cmd = (
            "gunicorn main_app.asgi:application --workers 3 "
            "--max-requests 2000 --max-requests-jitter 100"
        )
        self.assertEqual(TC3.parse_max_requests_from_cmdline(cmd), (2000, 100))

    def test_harness_defaults_are_the_plan_gate(self):
        src = (HERE / "live_tc3.py").read_text()
        self.assertIn('TC3_DURATION_S", "3600"', src)
        self.assertIn('TC3_POLL_S", "10"', src)
        self.assertIn("not the OOM fix", src)
        self.assertIn("DURATION_S = int", src)

    def test_plateau_rejects_monotonic_climb(self):
        samples = [{"t": 60 + i * 15, "max_rss_kb": 100_000 + i * 2000} for i in range(8)]
        result = TC3.rss_plateau(samples, warmup_s=60)
        self.assertFalse(result["ok"])
        self.assertTrue(result["strictly_monotonic_up"])

    def test_plateau_accepts_flat_rss(self):
        samples = [{"t": 60 + i * 15, "max_rss_kb": 174_000 + (i % 2) * 40} for i in range(12)]
        result = TC3.rss_plateau(samples, warmup_s=60)
        self.assertTrue(result["ok"])
        self.assertLess(result["spread_kb"], 1024)


if __name__ == "__main__":
    unittest.main()
