"""exclude_shadow keeps every v1 row, including rows whose metadata has no ``source`` key.

PR #100 review finding 4: ``qs.exclude(metadata__source="gateway_v2")`` compiles to
``NOT (metadata -> 'source' = '"gateway_v2"')``. For a row with no ``source`` key that
expression is NULL, so the row disappeared from the v1 KPI and rollup totals.
"""
from __future__ import annotations

from django.test import TestCase

from policy.models import EnforcementEvent
from policy.v2_shadow import exclude_shadow, only_shadow


class ExcludeShadowTests(TestCase):
    def setUp(self):
        from auth.models import Organization

        self.org = Organization.objects.create(name="Shadow Org", slug="shadow-org")

    def _row(self, metadata):
        return EnforcementEvent.objects.create(
            organization=self.org, action="block", metadata=metadata
        )

    def test_rows_without_a_source_key_stay_in_v1_totals(self):
        no_source = self._row({"request_id": "iso-1"})  # e.g. isolation events
        empty = self._row({})
        v1_source = self._row({"source": "security_scan"})
        null_source = self._row({"source": None})
        self._row({"source": "gateway_v2", "request_id": "v2-1"})

        kept = set(exclude_shadow(EnforcementEvent.objects.all()).values_list("pk", flat=True))

        self.assertEqual(kept, {no_source.pk, empty.pk, v1_source.pk, null_source.pk})

    def test_only_shadow_is_the_exact_complement(self):
        self._row({"request_id": "iso-1"})
        self._row({})
        self._row({"source": "security_scan"})
        v2 = self._row({"source": "gateway_v2"})
        everything = EnforcementEvent.objects.all()

        shadow = set(only_shadow(everything).values_list("pk", flat=True))
        primary = set(exclude_shadow(everything).values_list("pk", flat=True))

        self.assertEqual(shadow, {v2.pk})
        self.assertEqual(primary | shadow, set(everything.values_list("pk", flat=True)))
        self.assertFalse(primary & shadow)
