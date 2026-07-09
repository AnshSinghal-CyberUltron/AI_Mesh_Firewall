"""#38 regression: PIIDetector._anonymize must not leak raw PII on overlapping spans.

The old algorithm spliced right-to-left but de-duped only EXACT spans, so two
detectors matching the same digit run (partially-overlapping spans) corrupted the
output and LEAKED RAW DIGITS (e.g. "<SSN>ARD>56789XY"). The fix merges overlapping
spans into disjoint union intervals before splicing.

pii_detector.py is stdlib-only, so this runs standalone (no Django needed).
"""

import re
import unittest

from security_engines.pii_detector import PIIDetector


def _ent(t, s, e, text):
    return {"type": t, "start": s, "end": e, "text": text[s:e]}


class AnonymizeOverlapTests(unittest.TestCase):
    def test_overlapping_spans_do_not_leak_raw_digits(self):
        text = "SSN123456789XY"  # SSN 0..12 overlaps a short CREDIT_CARD 3..7
        out = PIIDetector._anonymize(
            text,
            {
                "SSN": [_ent("SSN", 0, 12, text)],
                "CREDIT_CARD": [_ent("CREDIT_CARD", 3, 7, text)],
            },
        )
        self.assertIsNone(re.search(r"\d", out), f"raw digits leaked: {out!r}")

    def test_nested_span_no_leak(self):
        text = "AAAA11112222BBBB"
        out = PIIDetector._anonymize(
            text,
            {"X": [_ent("X", 4, 12, text)], "Y": [_ent("Y", 6, 10, text)]},
        )
        self.assertIsNone(re.search(r"\d", out), f"raw digits leaked: {out!r}")

    def test_non_overlapping_both_redacted(self):
        text = "a EMAIL b PHONE c"
        out = PIIDetector._anonymize(
            text,
            {"E": [_ent("EMAIL", 2, 7, text)], "P": [_ent("PHONE", 10, 15, text)]},
        )
        self.assertIn("<EMAIL>", out)
        self.assertIn("<PHONE>", out)

    def test_exact_duplicate_single_replacement(self):
        text = "0123456789 tail"
        out = PIIDetector._anonymize(
            text,
            {"A": [_ent("CC", 0, 10, text)], "B": [_ent("CC", 0, 10, text)]},
        )
        self.assertEqual(out, "<CC> tail")

    def test_no_entities_unchanged(self):
        self.assertEqual(PIIDetector._anonymize("hello world", {}), "hello world")


if __name__ == "__main__":
    unittest.main()
