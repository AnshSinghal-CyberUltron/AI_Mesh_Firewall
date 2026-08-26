"""T-C7: analytics views must not project raw metadata or use .iterator() as a bound."""

from __future__ import annotations

import re
from pathlib import Path

from django.test import SimpleTestCase

_CONTROL = Path(__file__).resolve().parents[2]
_VIEW_FILES = (
    _CONTROL / "policy" / "security_views.py",
    _CONTROL / "core" / "dashboard_views.py",
    _CONTROL / "policy" / "analytics_views.py",
)

_VALUES_METADATA = re.compile(
    r"\.values\s*\((?:[^)]|\n)*?['\"]metadata['\"]",
    re.MULTILINE,
)
_ITERATOR = re.compile(r"\.iterator\s*\(")


class AnalyticsProjectionAuditTests(SimpleTestCase):
    def test_no_raw_metadata_projection_in_analytics_views(self):
        hits = []
        for path in _VIEW_FILES:
            text = path.read_text()
            for match in _VALUES_METADATA.finditer(text):
                line_no = text[: match.start()].count("\n") + 1
                hits.append(f"{path.name}:{line_no}")
        self.assertEqual(
            hits,
            [],
            msg="T-C7: analytics querysets must not project a raw metadata blob: " + ", ".join(hits),
        )

    def test_no_iterator_used_as_a_memory_bound(self):
        hits = []
        for path in _VIEW_FILES:
            text = path.read_text()
            for match in _ITERATOR.finditer(text):
                line_no = text[: match.start()].count("\n") + 1
                hits.append(f"{path.name}:{line_no}")
        self.assertEqual(
            hits,
            [],
            msg="T-C7: .iterator() is not a memory bound (cursors disabled): " + ", ".join(hits),
        )
