"""Phase 0a C-4b: cap concurrent analytics scans (semaphore)."""

from __future__ import annotations

import threading
import time

from django.http import JsonResponse
from django.test import RequestFactory, SimpleTestCase


class AnalyticsSemaphoreTests(SimpleTestCase):
    def test_default_max_inflight_is_two(self):
        from policy.analytics_concurrency import ANALYTICS_MAX_INFLIGHT

        self.assertEqual(ANALYTICS_MAX_INFLIGHT, 2)

    def test_third_waiter_gets_503_when_two_held(self):
        from policy.analytics_concurrency import (
            AnalyticsConcurrencyMixin,
            reset_analytics_semaphore_for_tests,
        )

        reset_analytics_semaphore_for_tests(max_inflight=2, wait_s=0.05)

        class _Inner:
            def dispatch(self, request, *args, **kwargs):
                time.sleep(0.3)
                return JsonResponse({"ok": True})

        class _View(AnalyticsConcurrencyMixin, _Inner):
            pass

        factory = RequestFactory()
        view = _View()
        results = []

        def run():
            resp = view.dispatch(factory.get("/api/security/soc-kpis/"))
            results.append(resp.status_code)

        threads = [threading.Thread(target=run) for _ in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=2)

        self.assertEqual(len(results), 3)
        self.assertEqual(results.count(503), 1)
        self.assertEqual(results.count(200), 2)
