"""F4 (PR #115, reviews A-S2 / B-S4): every org's v2 plan document follows the publisher.

Before: ``publish_for_org`` ran only on an org or FirewallConfig save, so after a release or a
``V2_PLAN_NEW_RULES_MODE`` change (including the rollback to ``off``) an org that did not save
kept its old document. ``core.v2_plan_rollout.reconcile`` republishes every active org once when
the publisher's fingerprint changes, and is a no-op otherwise.
"""

from __future__ import annotations

import json
import os
from io import StringIO
from pathlib import Path
from typing import Any
from unittest import mock

from auth.models import Organization
from core import v2_plan_rollout as rollout
from core.models import V2PlanDocument
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings

STALE = '{"org_id":"x","rules":[],"streaming_mode":"incremental"}'


class FakeRedis:
    """The four commands the rollout uses, with ``SET NX`` semantics."""

    def __init__(self) -> None:
        self.data: dict[str, bytes] = {}

    def get(self, name: str) -> bytes | None:
        return self.data.get(name)

    def set(self, name: str, value: str, *, nx: bool = False, ex: int | None = None) -> bool | None:
        del ex
        if nx and name in self.data:
            return None
        self.data[name] = value.encode()
        return True

    def expire(self, name: str, time: int) -> bool:
        del time
        return name in self.data

    def delete(self, *names: str) -> int:
        return sum(1 for name in names if self.data.pop(name, None) is not None)


def _active() -> int:
    """Active orgs, including the ones migrations seed (auth 0005 backfills a default org)."""
    return Organization.objects.filter(is_active=True).count()


def _rules(org: Organization) -> dict[str, dict[str, Any]]:
    body = json.loads(V2PlanDocument.objects.get(organization=org).body)
    return {row["rule_id"]: row for row in body["rules"]}


@override_settings(V2_PLAN_NEW_RULES_MODE="monitor")
class ReconcileTests(TestCase):
    def setUp(self) -> None:
        self.redis = FakeRedis()
        self.stale = Organization.objects.create(name="Stale", slug="f4-stale")
        self.fresh = Organization.objects.create(name="Never saved", slug="f4-never-saved")
        self.inactive = Organization.objects.create(name="Gone", slug="f4-gone", is_active=False)
        # The signal publish runs on commit, which a TestCase never reaches: no document yet.
        V2PlanDocument.objects.create(organization=self.stale, body=STALE, revision=1)
        V2PlanDocument.objects.create(organization=self.inactive, body=STALE, revision=1)

    def test_a_changed_publisher_republishes_every_active_org_once(self) -> None:
        note = rollout.reconcile(client=self.redis)
        self.assertEqual(note["status"], "swept")
        self.assertEqual((note["published"], note["failed"]), (_active(), 0))
        stale = V2PlanDocument.objects.get(organization=self.stale)
        self.assertEqual(stale.revision, 2)
        self.assertIn("fw-pii-input-ext", _rules(self.stale))
        self.assertIn("fw-pii-input-ext", _rules(self.fresh))
        self.assertEqual(V2PlanDocument.objects.get(organization=self.inactive).body, STALE)
        self.assertEqual(self.redis.get(rollout.MARKER_KEY), rollout.fingerprint().encode())
        self.assertIsNone(self.redis.get(rollout.LOCK_KEY))

    def test_an_unchanged_publisher_is_a_no_op(self) -> None:
        rollout.reconcile(client=self.redis)
        revisions = list(V2PlanDocument.objects.order_by("pk").values_list("revision", flat=True))
        with mock.patch("core.firewall_v2_publish.publish_for_org") as publish:
            note = rollout.reconcile(client=self.redis)
        self.assertEqual(note["status"], "current")
        publish.assert_not_called()
        self.assertEqual(
            list(V2PlanDocument.objects.order_by("pk").values_list("revision", flat=True)),
            revisions,
        )

    def test_a_forced_sweep_of_unchanged_documents_raises_no_revision(self) -> None:
        rollout.reconcile(client=self.redis)
        note = rollout.reconcile(client=self.redis, force=True)
        self.assertEqual(note["status"], "swept")
        self.assertEqual(V2PlanDocument.objects.get(organization=self.stale).revision, 2)

    def test_the_gate_flip_and_the_rollback_reach_an_org_that_never_saves(self) -> None:
        rollout.reconcile(client=self.redis)
        self.assertEqual(_rules(self.stale)["fw-prompt_injection-input-ext"]["action"], "flag")
        with override_settings(V2_PLAN_NEW_RULES_MODE="enforce"):
            self.assertEqual(rollout.reconcile(client=self.redis)["status"], "swept")
        self.assertEqual(_rules(self.stale)["fw-prompt_injection-input-ext"]["action"], "block")
        self.assertEqual(_rules(self.stale)["fw-prompt_injection-input-ext"]["mode"], "enforce")
        with override_settings(V2_PLAN_NEW_RULES_MODE="off"):
            self.assertEqual(rollout.reconcile(client=self.redis)["status"], "swept")
        self.assertFalse([rid for rid in _rules(self.stale) if rid.endswith("-ext")])
        self.assertFalse([rid for rid in _rules(self.fresh) if rid.endswith("-ext")])

    def test_another_runner_holding_the_lock_means_no_work_here(self) -> None:
        self.redis.set(rollout.LOCK_KEY, "elsewhere")
        note = rollout.reconcile(client=self.redis)
        self.assertEqual(note["status"], "locked")
        self.assertEqual(V2PlanDocument.objects.get(organization=self.stale).body, STALE)
        self.assertIsNone(self.redis.get(rollout.MARKER_KEY))
        self.assertEqual(self.redis.get(rollout.LOCK_KEY), b"elsewhere")

    def test_a_failed_org_leaves_the_marker_unset_so_the_sweep_repeats(self) -> None:
        from core import firewall_v2_publish

        real = firewall_v2_publish.publish_for_org

        def flaky(org: Any) -> dict[str, object]:
            if org.slug == "f4-never-saved":
                return {"status": "unavailable", "org_id": org.slug}
            return real(org)

        with mock.patch("core.firewall_v2_publish.publish_for_org", side_effect=flaky):
            note = rollout.reconcile(client=self.redis)
        self.assertEqual(
            (note["status"], note["published"], note["failed"]), ("incomplete", _active() - 1, 1)
        )
        self.assertIsNone(self.redis.get(rollout.MARKER_KEY))
        self.assertIsNone(self.redis.get(rollout.LOCK_KEY))
        self.assertEqual(rollout.reconcile(client=self.redis)["status"], "swept")
        self.assertIn("fw-pii-input-ext", _rules(self.fresh))

    def test_the_sweep_pages_by_primary_key(self) -> None:
        for n in range(3):
            Organization.objects.create(name=f"Paged {n}", slug=f"f4-paged-{n}")
        batches: list[int] = []
        with mock.patch.object(rollout, "BATCH", 2):
            published, failed = rollout.sweep(lambda: batches.append(1))
        active = _active()
        self.assertGreaterEqual(active, 5)
        self.assertEqual((published, failed), (active, 0))
        self.assertEqual(len(batches), -(-active // 2))
        self.assertEqual(
            V2PlanDocument.objects.filter(organization__is_active=True).count(), active
        )

    def test_settling_retries_until_the_other_runner_is_gone(self) -> None:
        self.redis.set(rollout.LOCK_KEY, "dead-runner")
        waits: list[float] = []

        def sleep(seconds: float) -> None:
            waits.append(seconds)
            self.redis.delete(rollout.LOCK_KEY)  # its lock expired

        note = rollout.reconcile_until_settled(
            attempts=3, wait_s=7.0, client=self.redis, sleep=sleep
        )
        self.assertEqual(note["status"], "swept")
        self.assertEqual(waits, [7.0])

    def test_settling_survives_an_exception_and_reports_it(self) -> None:
        with mock.patch.object(rollout, "sweep", side_effect=RuntimeError("db not migrated")):
            note = rollout.reconcile_until_settled(
                attempts=2, wait_s=0.0, client=self.redis, sleep=lambda _s: None
            )
        self.assertEqual(note["status"], "error")
        self.assertIsNone(self.redis.get(rollout.LOCK_KEY))

    def test_the_command_runs_the_rollout(self) -> None:
        out = StringIO()
        with mock.patch.object(rollout, "_redis", return_value=self.redis):
            call_command("sync_v2_plan_documents", "--if-changed", stdout=out)
            call_command("sync_v2_plan_documents", "--if-changed", stdout=out)
        lines = out.getvalue().splitlines()
        self.assertIn('"status": "swept"', lines[0])
        self.assertIn('"status": "current"', lines[1])
        with self.assertRaises(CommandError):
            call_command("sync_v2_plan_documents", "--if-changed", "--org-slug", "f4-stale")


class FingerprintTests(TestCase):
    def test_it_is_stable_and_follows_every_publish_setting(self) -> None:
        with override_settings(V2_PLAN_NEW_RULES_MODE="monitor"):
            monitor = rollout.fingerprint()
            self.assertEqual(rollout.fingerprint(), monitor)
            with mock.patch.dict(os.environ, {"AMF_V2_CANARY_ORGS": "acme"}):
                canary = rollout.fingerprint()
        with override_settings(V2_PLAN_NEW_RULES_MODE="enforce"):
            enforce = rollout.fingerprint()
        with override_settings(V2_PLAN_NEW_RULES_MODE="off"):
            off = rollout.fingerprint()
        self.assertEqual(len({monitor, canary, enforce, off}), 4)

    def test_it_follows_the_publisher_code(self) -> None:
        with override_settings(V2_PLAN_NEW_RULES_MODE="enforce"):
            before = rollout.fingerprint()
            with mock.patch("core.firewall_v2_publish.surface_drafts", return_value=[]):
                reverted = rollout.fingerprint()
        self.assertNotEqual(before, reverted)


class EntrypointTests(TestCase):
    """The rollout runs once per container start, in the background, after the dry-run exit."""

    def test_the_server_entrypoint_starts_the_rollout_in_the_background(self) -> None:
        text = (Path(__file__).resolve().parents[3] / "server-entrypoint.sh").read_text()
        launch = text.index("sync_v2_plan_documents --if-changed")
        self.assertGreater(launch, text.index('CONTROL_ENTRYPOINT_DRYRUN:-0}" = "1"'))
        self.assertLess(launch, text.rindex('exec "$@"'))
        block = text[launch : text.index("fi", launch)]
        self.assertTrue(block.rstrip().endswith("&"), block)
        self.assertIn('"${V2_PLAN_ROLLOUT_ON_START:-1}" != "0"', text)
