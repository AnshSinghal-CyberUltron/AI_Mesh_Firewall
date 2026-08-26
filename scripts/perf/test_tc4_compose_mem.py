#!/usr/bin/env python3
"""T-C4: compose mem_limit sum is computed, never a literal."""

from __future__ import annotations

import unittest
from pathlib import Path

from tc4_compose_mem import REPO, evaluate, host_ram_mib, sum_mib

HERE = Path(__file__).resolve()


class Tc4ComposeMemTests(unittest.TestCase):
    def test_source_does_not_hardcode_the_wrong_draft_sum(self):
        helper = (HERE.parent / "tc4_compose_mem.py").read_text()
        draft_wrong = "22" + "656"
        draft_profile = "24" + "320"
        self.assertNotIn(draft_wrong, helper)
        self.assertNotIn(draft_profile, helper)
        self.assertNotIn("22" + ".6 GiB", helper)

    def test_live_compose_sum_fits_80pct_of_this_host(self):
        report = evaluate(REPO / "docker-compose.yml")
        self.assertGreater(report["sum_mib"], 0)
        self.assertTrue(report["ok"], report)

    def test_prod_sum_is_computed_from_the_file(self):
        total = sum_mib(REPO / "docker-compose.prod.yml")
        self.assertGreater(total, 1000)
        recomputed = sum_mib(REPO / "docker-compose.prod.yml")
        self.assertEqual(total, recomputed)

    def test_host_ram_is_read_from_proc(self):
        self.assertGreater(host_ram_mib(), 1024)


if __name__ == "__main__":
    unittest.main()
