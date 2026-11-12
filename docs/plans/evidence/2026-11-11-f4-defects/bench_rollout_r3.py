# Harness for bench_rollout_r3.out.txt (PR #115 round 3): run as `manage.py test bench_rollout_r3`
# from control/ with this directory and the local pgvector settings shim on PYTHONPATH (the shim is
# not committed). It creates its rows in its own test database.
"""F4 round 3: the rollout's start-up cost when nothing is stale, at 100k orgs, and its sweep."""

import statistics
import time
import uuid
from unittest import mock

from auth.models import Organization
from core import firewall_v2_publish as pub
from core import v2_plan_rollout as rollout
from core.models import V2PlanDocument
from django.test import TransactionTestCase, override_settings


def _ms(fn, n):
    samples = []
    for _ in range(n):
        t0 = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - t0) * 1000)
    samples.sort()
    return f"median={statistics.median(samples):.2f} ms p95={samples[int(n * 0.95) - 1]:.2f} ms (n={n})"


@override_settings(V2_PLAN_NEW_RULES_MODE="monitor")
class Bench(TransactionTestCase):
    FLEET = 100_000
    SWEEP = 1_000

    def test_bench(self):
        with mock.patch.object(rollout, "LOCK_KEY", f"bench:v2plan:lock:{uuid.uuid4().hex}"):
            self._bench()

    def _bench(self):
        out = []
        current = rollout.fingerprint()
        out.append(f"fingerprint(): {_ms(rollout.fingerprint, 200)}")
        out.append(f"unapplied_migrations(): {_ms(rollout.unapplied_migrations, 5)}")

        # The sweep: SWEEP orgs without a document (bulk_create sends no signal).
        Organization.objects.bulk_create(
            [Organization(name=f"S{i}", slug=f"bench-s-{i}") for i in range(self.SWEEP)]
        )
        n = rollout.stale_orgs(current).count()
        t0, c0 = time.perf_counter(), time.process_time()
        note = rollout.reconcile()
        wall, cpu = time.perf_counter() - t0, time.process_time() - c0
        out.append(
            f"sweep (create): orgs={n} status={note['status']} published={note['published']} "
            f"wall={wall:.2f}s ({wall / n * 1000:.2f} ms/org) process_cpu={cpu:.2f}s "
            f"({cpu / n * 1000:.2f} ms/org)"
        )
        orgs = list(Organization.objects.filter(slug__startswith="bench-s-")[:200])
        for notes in (True, False):
            t0 = time.perf_counter()
            for org in orgs:
                pub.publish_for_org(org, publisher=current, notes=notes)
            out.append(
                f"publish_for_org notes={notes}: {(time.perf_counter() - t0) / len(orgs) * 1000:.2f} "
                f"ms/org (n={len(orgs)}, body unchanged)"
            )

        # The no-op start at fleet scale: FLEET more orgs whose document is current.
        start = Organization.objects.order_by("-pk").values_list("pk", flat=True).first()
        for chunk in range(0, self.FLEET, 10_000):
            made = Organization.objects.bulk_create(
                [Organization(name=f"F{i}", slug=f"bench-f-{i}")
                 for i in range(chunk, chunk + 10_000)]
            )
            V2PlanDocument.objects.bulk_create(
                [V2PlanDocument(organization=o, body="{}", revision=1, publisher=current,
                                publisher_revision=1)
                 for o in made]
            )
        total = Organization.objects.filter(is_active=True).count()
        out.append(f"fleet: active orgs={total}, first fleet pk > {start}")
        from django.db import connection

        with connection.cursor() as cursor:
            cursor.execute("ANALYZE")
        out.append(f"reconcile() with nothing stale: {_ms(rollout.reconcile, 20)}")
        out.append(f"stale_orgs(...).exists() with nothing stale: "
                   f"{_ms(lambda: rollout.stale_orgs(current).exists(), 20)}")
        out.append(f"status() (two counts): {_ms(rollout.status, 10)}")
        last = Organization.objects.order_by("-pk").first()
        V2PlanDocument.objects.filter(organization=last).update(publisher="old")
        out.append(f"stale_orgs(...).exists() with only the highest pk stale: "
                   f"{_ms(lambda: rollout.stale_orgs(current).exists(), 20)}")
        t0 = time.perf_counter()
        note = rollout.reconcile()
        out.append(f"reconcile() republishing that one org: status={note['status']} "
                   f"published={note['published']} {(time.perf_counter() - t0) * 1000:.0f} ms")
        sql = str(rollout.stale_orgs(current).query)
        out.append(f"stale query: {sql[sql.index('FROM'):]}")
        print("\nBENCH " + "\nBENCH ".join(out))
