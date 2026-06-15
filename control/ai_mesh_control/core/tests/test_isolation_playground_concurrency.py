"""M-25b: concurrent ensure_isolation_playground_for_org must not mint duplicates.

The get-or-create critical section now runs inside transaction.atomic() and
locks the organization row with select_for_update (key rows alone cannot be
locked when none exist yet), so two concurrent provisioning calls serialize:
the second blocks until the first commits, then returns the SAME key.

Uses TransactionTestCase so each thread gets a real committed transaction
(select_for_update blocking requires actual cross-connection locking, which
TestCase's single wrapping transaction cannot exercise).
"""

from __future__ import annotations

import threading

from django.contrib.auth import get_user_model
from django.db import connections
from django.test import TransactionTestCase

User = get_user_model()


class IsolationPlaygroundConcurrencyTests(TransactionTestCase):
    def setUp(self):
        from auth.models import Organization, UserProfile

        self.org = Organization.objects.create(name="Conc Org", slug="conc-org")
        self.user = User.objects.create_user(username="conc_user", password="pw")
        profile, _ = UserProfile.objects.get_or_create(user=self.user)
        profile.organization = self.org
        profile.save(update_fields=["organization"])

    def test_concurrent_ensure_returns_single_key(self):
        from core.models import GatewayAPIKey

        n_threads = 3
        barrier = threading.Barrier(n_threads, timeout=15)
        results: list = []
        errors: list = []
        lock = threading.Lock()

        def worker():
            try:
                # Line all threads up so they enter the critical section together.
                barrier.wait()
                inst, raw = GatewayAPIKey.ensure_isolation_playground_for_org(
                    self.org, self.user
                )
                with lock:
                    results.append((inst.pk, raw))
            except Exception as exc:  # pragma: no cover - failure diagnostics
                with lock:
                    errors.append(exc)
            finally:
                # Each thread opened its own DB connection; release it.
                connections.close_all()

        threads = [threading.Thread(target=worker) for _ in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        self.assertEqual(errors, [], f"workers raised: {errors!r}")
        self.assertEqual(len(results), n_threads)

        # Every caller got the SAME key (no duplicate mint under concurrency).
        pks = {pk for pk, _raw in results}
        self.assertEqual(len(pks), 1, f"expected one key, got {pks}")

        # And the recoverable plaintext handed out is identical for everyone.
        raws = {raw for _pk, raw in results}
        self.assertEqual(len(raws), 1)
        self.assertIsNotNone(next(iter(raws)))

        # Exactly ONE active playground key exists for the org.
        active = GatewayAPIKey.objects.filter(
            organization=self.org,
            project_id=f"isolation-playground-{self.org.slug}",
            is_active=True,
        )
        self.assertEqual(active.count(), 1)

    def test_sequential_ensure_still_idempotent_outside_test_atomics(self):
        """Sanity: the atomic-wrapped path stays idempotent under autocommit."""
        from core.models import GatewayAPIKey

        first, raw1 = GatewayAPIKey.ensure_isolation_playground_for_org(self.org, self.user)
        second, raw2 = GatewayAPIKey.ensure_isolation_playground_for_org(self.org, self.user)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(raw1, raw2)
        self.assertEqual(
            GatewayAPIKey.objects.filter(
                organization=self.org,
                project_id=f"isolation-playground-{self.org.slug}",
                is_active=True,
            ).count(),
            1,
        )
