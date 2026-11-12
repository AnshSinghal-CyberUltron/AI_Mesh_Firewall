# Harness used for bench_rollout.out.txt: run as `manage.py test bench_rollout` with the harness directory on PYTHONPATH.
"""F4 author (PR #115), harness only: time the v2 plan rollout sweep per org. Not committed."""
import statistics
import time

from django.test import TransactionTestCase, override_settings

from auth.models import Organization
from core import v2_plan_rollout as rollout
from core.models import V2PlanDocument


class FakeRedis:
    def __init__(self):
        self.data = {}

    def get(self, k):
        return self.data.get(k)

    def set(self, k, v, nx=False, ex=None):
        if nx and k in self.data:
            return None
        self.data[k] = v.encode()
        return True

    def expire(self, k, t):
        return True

    def delete(self, *ks):
        return sum(1 for k in ks if self.data.pop(k, None) is not None)


@override_settings(V2_PLAN_NEW_RULES_MODE="monitor")
class Bench(TransactionTestCase):
    N = 400

    def test_bench(self):
        Organization.objects.bulk_create(
            [Organization(name=f"B{i}", slug=f"bench-{i}") for i in range(self.N)]
        )  # bulk_create sends no post_save: no policies seeded, no document (worst case: create)
        n = Organization.objects.filter(is_active=True).count()
        r = FakeRedis()
        out = []
        for label, mode in (("first sweep (create)", "monitor"), ("flip to enforce (update)", "enforce"),
                            ("same settings, forced (no change)", "enforce")):
            with override_settings(V2_PLAN_NEW_RULES_MODE=mode):
                t0, c0 = time.perf_counter(), time.process_time()
                note = rollout.reconcile(client=r, force=label.endswith("(no change)"))
                wall, cpu = time.perf_counter() - t0, time.process_time() - c0
            out.append(f"{label}: orgs={n} status={note['status']} published={note.get('published')} "
                       f"wall={wall:.2f}s ({wall / n * 1000:.2f} ms/org) process_cpu={cpu:.2f}s "
                       f"({cpu / n * 1000:.2f} ms/org)")
        samples = []
        for _ in range(200):
            t0 = time.perf_counter()
            with override_settings(V2_PLAN_NEW_RULES_MODE="enforce"):
                assert rollout.reconcile(client=r)["status"] == "current"
            samples.append((time.perf_counter() - t0) * 1000)
        out.append(f"no-op reconcile (marker current): median={statistics.median(samples):.2f} ms "
                   f"p99={sorted(samples)[197]:.2f} ms (n=200)")
        fp = []
        for _ in range(200):
            t0 = time.perf_counter()
            rollout.fingerprint()
            fp.append((time.perf_counter() - t0) * 1000)
        out.append(f"fingerprint(): median={statistics.median(fp):.2f} ms (n=200)")
        size = max(len(d.body) for d in V2PlanDocument.objects.all()[:50])
        out.append(f"document bytes (enforce, default org): {size}")
        print("\nBENCH " + "\nBENCH ".join(out))
