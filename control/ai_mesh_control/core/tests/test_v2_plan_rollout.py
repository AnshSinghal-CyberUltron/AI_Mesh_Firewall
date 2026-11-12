"""F4 (PR #115): every org's v2 plan document follows the publisher.

Before: ``publish_for_org`` ran only on an org or FirewallConfig save, so after a release or a
``V2_PLAN_NEW_RULES_MODE`` change (including the rollback to ``off``) an org that did not save
kept its old document. ``core.v2_plan_rollout`` stamps every document with the fingerprint of the
publisher that built it and republishes the active orgs whose stamp is not the running one.

Round 3 of the reviews added: the fingerprint covers the publisher's code, migrations and model
defaults (A-D1, B-D1); a failing org costs retries of itself only, and settles as ``partial``
(A-D2, B-D2); the lock is owner-checked (B-D5); an old replica's save is repaired by the next
start (B-D4); progress lines and ``--status`` (B-D8); the sweep skips the console note (B-D7);
the server entrypoint's launch shape (A-D3, B-D3). These tests use the real Redis (CI and the
local test host run one) for the lock, under a key of their own.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import uuid
from collections import Counter
from io import StringIO
from pathlib import Path
from typing import Any
from unittest import mock

from auth.models import Organization
from core import v2_plan_rollout as rollout
from core.apps import CoreConfig
from core.models import FirewallConfig, V2PlanDocument
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, TestCase, override_settings
from policy.apps import PolicyConfig

STALE = '{"org_id":"x","rules":[],"streaming_mode":"incremental"}'
EXT = "fw-prompt_injection-input-ext"
ENTRYPOINT = Path(__file__).resolve().parents[3] / "server-entrypoint.sh"


def _active() -> int:
    """Active orgs, including the ones migrations seed (auth 0005 backfills a default org)."""
    return Organization.objects.filter(is_active=True).count()


def _rules(org: Organization) -> dict[str, dict[str, Any]]:
    body = json.loads(V2PlanDocument.objects.get(organization=org).body)
    return {row["rule_id"]: row for row in body["rules"]}


def _ext(org: Organization) -> str:
    row = _rules(org).get(EXT)
    return "absent" if row is None else f"{row['action']}/{row['mode']}"


def _stamp(org: Organization) -> str:
    return V2PlanDocument.objects.get(organization=org).publisher


class _Counting:
    """Wraps ``publish_for_org`` and records the org ids it was called with."""

    def __init__(self, fail: set[int] | None = None) -> None:
        from core import firewall_v2_publish

        self.real = firewall_v2_publish.publish_for_org
        self.fail = fail or set()
        self.calls: list[int] = []

    def __call__(self, org: Any, **kwargs: Any) -> dict[str, object]:
        self.calls.append(org.pk)
        if org.pk in self.fail:
            return {"status": "unavailable", "org_id": org.slug}
        return self.real(org, **kwargs)

    def patch(self) -> Any:
        return mock.patch("core.firewall_v2_publish.publish_for_org", side_effect=self)


class _RedisLockMixin:
    """A lock key of the test's own on the real Redis, removed afterwards."""

    def _own_lock_key(self: Any) -> None:
        self.redis = rollout._redis()
        self.lock_key = f"test:v2plan:rollout:lock:{uuid.uuid4().hex}"
        patcher = mock.patch.object(rollout, "LOCK_KEY", self.lock_key)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.redis.delete, self.lock_key)


@override_settings(V2_PLAN_NEW_RULES_MODE="monitor")
class ReconcileTests(_RedisLockMixin, TestCase):
    def setUp(self) -> None:
        self._own_lock_key()
        self.stale = Organization.objects.create(name="Stale", slug="f4-stale")
        self.fresh = Organization.objects.create(name="Never saved", slug="f4-never-saved")
        self.inactive = Organization.objects.create(name="Gone", slug="f4-gone", is_active=False)
        # The signal publish runs on commit, which a TestCase never reaches: no document yet.
        V2PlanDocument.objects.create(organization=self.stale, body=STALE, revision=1)
        V2PlanDocument.objects.create(organization=self.inactive, body=STALE, revision=1)

    def test_a_changed_publisher_republishes_every_active_org_once(self) -> None:
        note = rollout.reconcile(client=self.redis)
        self.assertEqual(note["status"], "swept")
        self.assertEqual((note["published"], note["failed"], note["remaining"]), (_active(), 0, 0))
        self.assertEqual(V2PlanDocument.objects.get(organization=self.stale).revision, 2)
        self.assertIn("fw-pii-input-ext", _rules(self.stale))
        self.assertIn("fw-pii-input-ext", _rules(self.fresh))
        self.assertEqual(V2PlanDocument.objects.get(organization=self.inactive).body, STALE)
        current = rollout.fingerprint()
        self.assertEqual(
            set(V2PlanDocument.objects.filter(organization__is_active=True)
                .values_list("publisher", flat=True)),
            {current},
        )
        self.assertIsNone(self.redis.get(self.lock_key))

    def test_nothing_stale_is_one_query_and_no_redis(self) -> None:
        rollout.reconcile(client=self.redis)
        revisions = list(V2PlanDocument.objects.order_by("pk").values_list("revision", flat=True))
        counting = _Counting()
        with counting.patch(), mock.patch.object(
            rollout, "_redis", side_effect=AssertionError("Redis touched")
        ):
            note = rollout.reconcile()
        self.assertEqual(note["status"], "current")
        self.assertEqual(counting.calls, [])
        self.assertEqual(
            list(V2PlanDocument.objects.order_by("pk").values_list("revision", flat=True)),
            revisions,
        )

    def test_a_forced_sweep_of_unchanged_documents_raises_no_revision(self) -> None:
        rollout.reconcile(client=self.redis)
        note = rollout.reconcile(client=self.redis, force=True)
        self.assertEqual((note["status"], note["published"]), ("swept", _active()))
        self.assertEqual(V2PlanDocument.objects.get(organization=self.stale).revision, 2)

    def test_the_gate_flip_and_the_rollback_reach_an_org_that_never_saves(self) -> None:
        rollout.reconcile(client=self.redis)
        self.assertEqual(_ext(self.stale), "flag/monitor")
        with override_settings(V2_PLAN_NEW_RULES_MODE="enforce"):
            self.assertEqual(rollout.reconcile(client=self.redis)["status"], "swept")
        self.assertEqual(_ext(self.stale), "block/enforce")
        with override_settings(V2_PLAN_NEW_RULES_MODE="off"):
            self.assertEqual(rollout.reconcile(client=self.redis)["status"], "swept")
        self.assertFalse([rid for rid in _rules(self.stale) if rid.endswith("-ext")])
        self.assertFalse([rid for rid in _rules(self.fresh) if rid.endswith("-ext")])

    def test_an_old_replica_save_after_the_sweep_is_repaired_by_the_next_start(self) -> None:
        """B-D4: a replica still on the old settings publishes after the new sweep. Its document
        carries the old stamp, so the next start republishes exactly that org."""
        from core.firewall_v2_publish import publish_for_org

        for new_mode, old_mode, repaired in (
            ("enforce", "monitor", "block/enforce"),
            ("off", "enforce", "absent"),
        ):
            with override_settings(V2_PLAN_NEW_RULES_MODE=new_mode):
                self.assertEqual(rollout.reconcile(client=self.redis)["status"], "swept")
            with override_settings(V2_PLAN_NEW_RULES_MODE=old_mode):
                publish_for_org(self.stale)  # the old replica's console save
            self.assertNotEqual(_ext(self.stale), repaired)
            counting = _Counting()
            with override_settings(V2_PLAN_NEW_RULES_MODE=new_mode), counting.patch():
                self.assertEqual(rollout.status()["stale_orgs"], 1)
                note = rollout.reconcile(client=self.redis)
            self.assertEqual((note["status"], counting.calls), ("swept", [self.stale.pk]))
            self.assertEqual(_ext(self.stale), repaired)

    def test_a_rollback_image_that_changes_or_inserts_documents_leaves_them_stale(self) -> None:
        """An older control image does not know the stamp columns. Its body change raises the
        revision past the stamp, and its insert (raw SQL without the new columns, which needs
        the database defaults) carries an empty stamp: the next start of this release repairs
        both."""
        from django.db import connection
        from django.db.models import F

        rollout.reconcile(client=self.redis)
        V2PlanDocument.objects.filter(organization=self.stale).update(
            body=STALE, revision=F("revision") + 1
        )
        V2PlanDocument.objects.filter(organization=self.fresh).delete()
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO core_v2_plan_document (organization_id, body, revision, updated_at) "
                "VALUES (%s, %s, 1, now())",
                [self.fresh.pk, STALE],
            )
        self.assertEqual(rollout.status()["stale_orgs"], 2)
        counting = _Counting()
        with counting.patch():
            note = rollout.reconcile(client=self.redis)
        self.assertEqual(note["status"], "swept")
        self.assertEqual(sorted(counting.calls), sorted([self.stale.pk, self.fresh.pk]))
        self.assertIn("fw-pii-input-ext", _rules(self.stale))
        self.assertIn("fw-pii-input-ext", _rules(self.fresh))
        self.assertEqual(rollout.status()["stale_orgs"], 0)

    def test_a_console_publish_stamps_the_running_publisher(self) -> None:
        from core.firewall_v2_publish import publish_for_org

        current = rollout.fingerprint()
        publish_for_org(self.fresh)
        document = V2PlanDocument.objects.get(organization=self.fresh)
        self.assertEqual((document.publisher, document.publisher_revision), (current, 1))
        V2PlanDocument.objects.filter(pk=document.pk).update(publisher="an-older-release")
        publish_for_org(self.fresh)
        again = V2PlanDocument.objects.get(organization=self.fresh)
        self.assertEqual((again.revision, again.body), (document.revision, document.body))
        self.assertEqual((again.publisher, again.publisher_revision), (current, 1))
        publish_for_org(self.stale)  # body changes: revision 2, stamped at 2
        changed = V2PlanDocument.objects.get(organization=self.stale)
        self.assertEqual(
            (changed.revision, changed.publisher, changed.publisher_revision), (2, current, 2)
        )

    def test_another_runner_holding_the_lock_means_no_work_here(self) -> None:
        self.redis.set(self.lock_key, "elsewhere")
        note = rollout.reconcile(client=self.redis)
        self.assertEqual(note["status"], "locked")
        self.assertEqual(V2PlanDocument.objects.get(organization=self.stale).body, STALE)
        self.assertEqual(self.redis.get(self.lock_key), b"elsewhere")

    def test_pending_migrations_hold_the_sweep(self) -> None:
        self.assertEqual(rollout.unapplied_migrations(), [])
        counting = _Counting()
        with counting.patch(), mock.patch.object(
            rollout, "unapplied_migrations", return_value=["core.9999_data_fix"]
        ):
            note = rollout.reconcile(client=self.redis)
        self.assertEqual((note["status"], note["pending"]), ("waiting_for_migrations", 1))
        self.assertEqual(counting.calls, [])
        self.assertIsNone(self.redis.get(self.lock_key))

    def test_a_failed_org_stays_stale_and_only_it_is_retried(self) -> None:
        """A-D2/B-D2: the sweep is resumable; a retry republishes only what is still stale."""
        counting = _Counting(fail={self.fresh.pk})
        with counting.patch():
            note = rollout.reconcile(client=self.redis)
        self.assertEqual(
            (note["status"], note["published"], note["failed_orgs"], note["remaining"]),
            ("incomplete", _active() - 1, [self.fresh.pk], 1),
        )
        self.assertIsNone(self.redis.get(self.lock_key))
        retry = _Counting()
        with retry.patch():
            self.assertEqual(rollout.reconcile(client=self.redis)["status"], "swept")
        self.assertEqual(retry.calls, [self.fresh.pk])
        self.assertIn("fw-pii-input-ext", _rules(self.fresh))

    def test_a_persistently_failing_org_settles_as_partial(self) -> None:
        """A-D2 (21 orgs, 3 attempts: 63 publishes) and B-D2 (41 orgs, 5 attempts: 205 per
        start). Now: the fleet once, plus ORG_MAX_TRIES - 1 retries of the failing org; the
        next start retries that org only."""
        for n in range(20):
            Organization.objects.create(name=f"Fleet {n}", slug=f"f4-fleet-{n}")
        poison = Organization.objects.get(slug="f4-fleet-3")
        active = _active()
        waits: list[float] = []
        starts: list[tuple[str, int]] = []
        for _start in range(2):
            counting = _Counting(fail={poison.pk})
            with counting.patch():
                note = rollout.reconcile_until_settled(
                    attempts=20, wait_s=30.0, client=self.redis, sleep=waits.append
                )
            self.assertEqual(note["failed_orgs"], [poison.pk])
            self.assertEqual(Counter(counting.calls)[poison.pk], rollout.ORG_MAX_TRIES)
            starts.append((str(note["status"]), len(counting.calls)))
        self.assertEqual(
            starts,
            [("partial", active + rollout.ORG_MAX_TRIES - 1), ("partial", rollout.ORG_MAX_TRIES)],
        )
        self.assertEqual(len(waits), 2 * rollout.ORG_MAX_TRIES)
        self.assertEqual(
            list(rollout.stale_orgs(rollout.fingerprint()).values_list("pk", flat=True)),
            [poison.pk],
        )

    def test_a_failed_org_does_not_hold_back_the_orgs_after_it(self) -> None:
        for n in range(4):
            Organization.objects.create(name=f"Paged {n}", slug=f"f4-paged-{n}")
        first = Organization.objects.filter(is_active=True).order_by("pk").first()
        assert first is not None
        counting = _Counting(fail={first.pk})
        with mock.patch.object(rollout, "BATCH", 2), counting.patch():
            done = rollout.sweep(rollout.fingerprint())
        self.assertEqual((done["published"], done["failed_orgs"]), (_active() - 1, [first.pk]))
        self.assertEqual(sorted(counting.calls), sorted(set(counting.calls)))

    def test_a_forced_sweep_pages_by_primary_key_and_publishes_each_org_once(self) -> None:
        for n in range(3):
            Organization.objects.create(name=f"Paged {n}", slug=f"f4-paged-{n}")
        rollout.reconcile(client=self.redis)
        counting = _Counting()
        with mock.patch.object(rollout, "BATCH", 2), counting.patch():
            done = rollout.sweep(rollout.fingerprint(), force=True)
        active = list(
            Organization.objects.filter(is_active=True).order_by("pk").values_list("pk", flat=True)
        )
        self.assertEqual(counting.calls, active)
        self.assertEqual(done["published"], len(active))

    def test_a_systemic_failure_ends_the_sweep_early(self) -> None:
        for n in range(4):
            Organization.objects.create(name=f"Down {n}", slug=f"f4-down-{n}")
        everyone = {org.pk for org in Organization.objects.all()}
        counting = _Counting(fail=everyone)
        with mock.patch.object(rollout, "MAX_CONSECUTIVE_FAILURES", 3), counting.patch():
            note = rollout.reconcile(client=self.redis)
        self.assertEqual((note["status"], note["published"], note["failed"]), ("incomplete", 0, 3))
        self.assertEqual(len(counting.calls), 3)

    def test_settling_retries_until_the_other_runner_is_gone(self) -> None:
        self.redis.set(self.lock_key, "dead-runner")
        waits: list[float] = []

        def sleep(seconds: float) -> None:
            waits.append(seconds)
            self.redis.delete(self.lock_key)  # its lock expired

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
        self.assertIsNone(self.redis.get(self.lock_key))

    def test_a_sweep_that_loses_its_lock_stops_and_leaves_the_new_owner_alone(self) -> None:
        """B-D5: the refresh is owner-checked; a runner whose lock was taken over stops."""
        from core import firewall_v2_publish

        real = firewall_v2_publish.publish_for_org
        calls: list[int] = []

        def taken_over(org: Any, **kwargs: Any) -> dict[str, object]:
            calls.append(org.pk)
            self.redis.set(self.lock_key, "runner-b")  # A's TTL lapsed and B took the lock
            return real(org, **kwargs)

        with mock.patch.object(rollout, "LOCK_REFRESH_S", 0.0), mock.patch(
            "core.firewall_v2_publish.publish_for_org", side_effect=taken_over
        ):
            note = rollout.reconcile(client=self.redis)
        self.assertEqual((note["status"], note["published"], len(calls)), ("lost_lock", 1, 1))
        self.assertEqual(self.redis.get(self.lock_key), b"runner-b")

    def test_release_never_deletes_another_runners_lock(self) -> None:
        """B-D5: the release is compare-and-delete (was GET then DEL)."""

        def sweep(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
            self.redis.set(self.lock_key, "runner-b")
            return {"published": 0, "failed_orgs": [], "stopped": ""}

        with mock.patch.object(rollout, "sweep", side_effect=sweep):
            rollout.reconcile(client=self.redis)
        self.assertEqual(self.redis.get(self.lock_key), b"runner-b")

    def test_the_sweep_reports_progress(self) -> None:
        with mock.patch.object(rollout, "PROGRESS_EVERY_S", 0.0), self.assertLogs(
            "core.v2_plan_rollout", "INFO"
        ) as logs:
            rollout.reconcile(client=self.redis)
        text = "\n".join(logs.output)
        self.assertIn(f"stale_orgs={_active()}", text)
        self.assertIn("progress: published=", text)
        self.assertIn("'remaining': 0", text)

    def test_the_sweep_skips_the_console_note(self) -> None:
        """B-D7: the sweep discarded the note (a second config query and compliance checks)."""
        from core import firewall_v2_publish

        with mock.patch.object(firewall_v2_publish, "_notes", return_value={}) as notes:
            self.assertEqual(rollout.reconcile(client=self.redis)["status"], "swept")
            notes.assert_not_called()
            firewall_v2_publish.publish_for_org(self.stale)
            notes.assert_called_once()

    def test_the_dev_copy_is_written_inside_the_row_lock(self) -> None:
        """B-D6: written after the commit, a delayed sweep's dev copy could land after a newer
        console save's. Inside the transaction, the row lock orders the two writes."""
        from core import firewall_v2_publish
        from django.db import connection

        outside = len(connection.atomic_blocks)
        depths: list[int] = []
        with mock.patch.dict(os.environ, {"AMF_V2_AUDIT_ORG_MAP": "f4-alias=f4-stale"}), \
                mock.patch.object(
                    firewall_v2_publish, "_write",
                    side_effect=lambda *_a: depths.append(len(connection.atomic_blocks)),
                ):
            note = firewall_v2_publish.publish_for_org(self.stale)
        self.assertEqual(note["dev_copy"], "written")
        self.assertEqual(len(depths), 1)
        self.assertGreater(depths[0], outside)

    def test_the_command_runs_the_rollout_and_reports_status(self) -> None:
        out = StringIO()
        call_command("sync_v2_plan_documents", "--status", stdout=out)
        self.assertEqual(json.loads(out.getvalue())["stale_orgs"], _active())
        out = StringIO()
        with mock.patch.object(rollout, "_redis", return_value=self.redis):
            call_command("sync_v2_plan_documents", "--if-changed", stdout=out)
            call_command("sync_v2_plan_documents", "--if-changed", stdout=out)
        lines = out.getvalue().splitlines()
        self.assertIn('"status": "swept"', lines[0])
        self.assertIn('"status": "current"', lines[1])
        out = StringIO()
        call_command("sync_v2_plan_documents", "--status", stdout=out)
        self.assertEqual(json.loads(out.getvalue())["stale_orgs"], 0)
        with self.assertRaises(CommandError):
            call_command("sync_v2_plan_documents", "--if-changed", "--org-slug", "f4-stale")

    def test_a_partial_rollout_fails_the_command(self) -> None:
        with mock.patch.object(rollout, "_redis", return_value=self.redis), _Counting(
            fail={self.fresh.pk}
        ).patch(), self.assertRaisesRegex(CommandError, "did not settle: partial"):
            call_command(
                "sync_v2_plan_documents", "--if-changed", "--attempts", "5", "--wait", "0",
                stdout=StringIO(),
            )


@override_settings(V2_PLAN_NEW_RULES_MODE="monitor")
class FingerprintTests(TestCase):
    """A-D1/B-D1: the fingerprint follows the publisher, not one probe document."""

    def setUp(self) -> None:
        self.addCleanup(rollout.code_digest.cache_clear)
        self.addCleanup(rollout.migrations_digest.cache_clear)
        self.base = rollout.fingerprint()

    def test_it_is_stable_and_follows_the_gate(self) -> None:
        self.assertEqual(rollout.fingerprint(), self.base)
        seen = {self.base}
        for mode in ("enforce", "off"):
            with override_settings(V2_PLAN_NEW_RULES_MODE=mode):
                seen.add(rollout.fingerprint())
        self.assertEqual(len(seen), 3)

    def test_it_follows_every_publish_setting(self) -> None:
        for name in rollout.PUBLISH_SETTINGS:
            with self.subTest(setting=name), mock.patch.dict(os.environ, {name: "f4-changed"}):
                self.assertNotEqual(rollout.fingerprint(), self.base)
        for name in ("V2_PLAN_CONTROLS_ENABLED", "V2_PLAN_CONTROLS_ORGS"):
            with self.subTest(django_setting=name), override_settings(**{name: "acme"}):
                self.assertNotEqual(rollout.fingerprint(), self.base)

    def test_it_follows_any_change_to_any_publisher_module(self) -> None:
        """A release edits a file: B's three examples (customer default priority, customer rows
        ``fail_open``, monitor-posture rows) and A's canary row are all edits to these files."""
        real = rollout._source
        for name in rollout.PUBLISHER_MODULES:

            def edited(module: str, _name: str = name) -> bytes:
                return real(module) + (b"\n# release\n" if module == _name else b"")

            rollout.code_digest.cache_clear()
            with self.subTest(module=name), mock.patch.object(rollout, "_source", edited):
                self.assertNotEqual(rollout.fingerprint(), self.base)
        rollout.code_digest.cache_clear()
        self.assertEqual(rollout.fingerprint(), self.base)

    def test_it_follows_a_new_migration_of_the_publisher_inputs(self) -> None:
        real = rollout._migration_names
        for label in rollout.MIGRATION_APPS:

            def added(app: str, _label: str = label) -> list[str]:
                return real(app) + ([f"{app}.9999_data_fix"] if app == _label else [])

            rollout.migrations_digest.cache_clear()
            with self.subTest(app=label), mock.patch.object(rollout, "_migration_names", added):
                self.assertNotEqual(rollout.fingerprint(), self.base)

    def test_it_follows_a_model_default_of_a_never_saved_org(self) -> None:
        """A-D1 row 2: ``tier2_fail_closed_enabled`` is absent from the every-built-in probe."""
        field = FirewallConfig._meta.get_field("tier2_fail_closed_enabled")
        self.assertIs(field.get_default(), True)
        with mock.patch.object(field, "_get_default", lambda: False):
            self.assertNotEqual(rollout.fingerprint(), self.base)

    def test_the_migration_names_are_real(self) -> None:
        names = [n for label in rollout.MIGRATION_APPS for n in rollout._migration_names(label)]
        self.assertIn("core.0043_v2plandocument_publisher", names)
        self.assertIn("core.0040_rewrite_to_redact", names)
        self.assertTrue(any(n.startswith("auth_api.") for n in names))
        self.assertTrue(any(n.startswith("policy.") for n in names))


_STUB_PYTHON = r'''
import json, os, sys
if sys.argv[1:2] == ["-m"]:
    sys.exit(1)  # ai_mesh_shared.resource_budget: the entrypoint falls back
with open(os.environ["STUB_CALLS"], "a") as out:
    out.write(json.dumps({"argv": sys.argv[1:], "cwd": os.getcwd()}) + "\n")
print("rollout says hello")
sys.exit(int(os.environ["STUB_EXIT"]))
'''
# Stands in for gunicorn: like its arbiter, it reaps every child with waitpid(-1).
_STUB_REAPER = r'''
import json, os, sys
reaped = []
while True:
    try:
        _pid, status = os.waitpid(-1, 0)
    except ChildProcessError:
        break
    reaped.append(os.waitstatus_to_exitcode(status))
with open(os.environ["STUB_REAPED"], "w") as out:
    json.dump({"argv": sys.argv[1:2], "reaped": reaped}, out)
'''


class EntrypointTests(SimpleTestCase):
    """A-D3/B-D3: the rollout runs once per container start, in the background, by its relative
    ``manage.py`` name, and gunicorn (the exec'd parent of every background child) only ever
    reaps an exit status of 0 from it."""

    def _run(
        self, rollout_exit: int, sed_exit: int | None = None, **env: str
    ) -> tuple[list[int], list[dict[str, Any]]]:
        tmp = Path(tempfile.mkdtemp(prefix="f4-entrypoint-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        stubs = tmp / "bin"
        stubs.mkdir()
        files = {"python": _STUB_PYTHON, "gunicorn": _STUB_REAPER}
        for name, body in files.items():
            (stubs / name).write_text(f"#!{sys.executable}\n{body}")
        if sed_exit is not None:
            (stubs / "sed").write_text(f"#!/bin/sh\ncat >/dev/null\nexit {sed_exit}\n")
        for path in stubs.iterdir():
            path.chmod(path.stat().st_mode | stat.S_IXUSR)
        calls, reaped = tmp / "calls.jsonl", tmp / "reaped.json"
        run = subprocess.run(
            ["sh", str(ENTRYPOINT)],
            env={
                "PATH": f"{stubs}{os.pathsep}{os.environ.get('PATH', '/usr/bin:/bin')}",
                "STUB_CALLS": str(calls),
                "STUB_REAPED": str(reaped),
                "STUB_EXIT": str(rollout_exit),
                "CONTROL_WEB_CONCURRENCY": "1",
                **env,
            },
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        self.assertEqual(run.returncode, 0, run.stderr)
        got = json.loads(reaped.read_text())
        self.assertEqual(got["argv"], ["main_app.asgi:application"])
        lines = calls.read_text().splitlines() if calls.exists() else []
        return got["reaped"], [json.loads(line) for line in lines]

    def test_gunicorn_only_ever_reaps_a_zero_exit_from_the_rollout(self) -> None:
        for rollout_exit, sed_exit in ((0, None), (1, None), (3, None), (4, None), (1, 4)):
            with self.subTest(rollout_exit=rollout_exit, sed_exit=sed_exit):
                reaped, calls = self._run(rollout_exit, sed_exit)
                self.assertEqual(reaped, [0])
                self.assertEqual(len(calls), 1)

    def test_the_rollout_runs_as_a_management_command(self) -> None:
        _reaped, calls = self._run(0)
        call = calls[0]
        self.assertEqual(call["argv"][:3], ["manage.py", "sync_v2_plan_documents", "--if-changed"])
        self.assertEqual(Path(call["cwd"]).resolve(), ENTRYPOINT.parent.resolve())
        self.assertTrue((Path(call["cwd"]) / "manage.py").is_file())
        with mock.patch.object(sys, "argv", call["argv"]), mock.patch.dict(sys.modules):
            sys.modules.pop("pytest", None)
            self.assertTrue(CoreConfig._is_management_or_test())
            self.assertTrue(PolicyConfig._is_management_or_test())

    def test_gap_r29_an_absolute_manage_py_path_starts_the_server_jobs(self) -> None:
        """Register R29 (pre-existing): the start-up guard keys on the bare name, so a command run
        as ``/app/control/manage.py`` (``deploy-ec2.sh`` migrate) starts the server's jobs. The
        entrypoint therefore runs the rollout by its relative name."""
        argv = ["/app/control/manage.py", "sync_v2_plan_documents", "--if-changed"]
        with mock.patch.object(sys, "argv", argv), mock.patch.dict(sys.modules):
            sys.modules.pop("pytest", None)
            self.assertFalse(CoreConfig._is_management_or_test())

    def test_the_rollout_can_be_switched_off(self) -> None:
        reaped, calls = self._run(0, V2_PLAN_ROLLOUT_ON_START="0")
        self.assertEqual((reaped, calls), ([], []))

    def test_the_launch_follows_the_dry_run_exit_and_precedes_exec(self) -> None:
        text = ENTRYPOINT.read_text()
        launch = text.index("manage.py sync_v2_plan_documents --if-changed")
        self.assertGreater(launch, text.index('CONTROL_ENTRYPOINT_DRYRUN:-0}" = "1"'))
        self.assertLess(launch, text.rindex('exec "$@"'))
