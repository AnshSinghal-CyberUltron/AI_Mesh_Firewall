"""Tests for the 0030_modelstate_data_audit data migration.

Pre-existing ModelState rows written before the M-20 full_clean() fix can
violate model invariants; the migration must repair every condition that
full_clean() enforces so unrelated PATCH/isolate calls stop 400ing.
"""

from __future__ import annotations

from importlib import import_module

from django.apps import apps
from django.test import TestCase

audit_model_state_rows = import_module(
    "core.migrations.0030_modelstate_data_audit"
).audit_model_state_rows


class ModelStateDataAuditMigrationTests(TestCase):
    def setUp(self):
        from auth.models import Organization

        self.org = Organization.objects.create(name="Audit Org", slug="audit-org")

    def _make_state(self, **overrides):
        from core.models import ModelState

        defaults = {"organization": self.org}
        defaults.update(overrides)
        return ModelState.objects.create(**defaults)

    def test_repairs_all_invariants_full_clean_enforces(self):
        # Rows that predate the full_clean() fix — invalid in every way the
        # model's full_clean() would reject. .objects.create() bypasses
        # validators, mirroring how the bad rows got persisted originally.
        self_loop = self._make_state(model_name="gpt-4o", fallback_model="gpt-4o")
        bad_scores = self._make_state(
            model_name="m-scores", risk_score=-5.0, threshold=150.0
        )
        bad_choices = self._make_state(
            model_name="m-choices", status="bogus", action="nuke"
        )
        valid = self._make_state(
            model_name="m-valid",
            status="isolated",
            action="reroute",
            fallback_model="gpt-4o-mini",
            risk_score=42.0,
            threshold=80.0,
        )

        audit_model_state_rows(apps, None)

        self_loop.refresh_from_db()
        self.assertEqual(self_loop.fallback_model, "")

        bad_scores.refresh_from_db()
        self.assertEqual(bad_scores.risk_score, 0.0)
        self.assertEqual(bad_scores.threshold, 100.0)

        bad_choices.refresh_from_db()
        self.assertEqual(bad_choices.status, "active")
        self.assertEqual(bad_choices.action, "block")

        # Valid row untouched.
        valid.refresh_from_db()
        self.assertEqual(valid.status, "isolated")
        self.assertEqual(valid.action, "reroute")
        self.assertEqual(valid.fallback_model, "gpt-4o-mini")
        self.assertEqual(valid.risk_score, 42.0)

        # Every repaired row now passes the exact gate used by the views.
        for state in (self_loop, bad_scores, bad_choices, valid):
            state.full_clean()  # must not raise
