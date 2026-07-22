"""#33 regression: policy_check rate limiter admits exactly `limit` requests/minute.

Contract under test (policy.rate_limit.check_rate_limit):
  * The old `count = cache.get(); ...; cache.set(count+1)` was a read-modify-write
    RACE — concurrent requests all read the same count and all wrote count+1, so
    MORE than `limit` requests/minute slipped through (rate-limit bypass under load).
  * The atomic `cache.add` + `cache.incr` form admits EXACTLY `limit` and rejects the
    (limit+1)-th with HTTP 429. Race-safety (atomic incr) is additionally demonstrated
    by a threaded simulation: scratchpad rate-limit sim (old admits >limit, new == limit).
"""

from __future__ import annotations

from types import SimpleNamespace

from django.core.cache import cache
from django.test import SimpleTestCase, override_settings

from policy.rate_limit import check_rate_limit


def _req(ip: str = "1.2.3.4", user_id=None):
    return SimpleNamespace(user=None, data={"user_id": user_id} if user_id else {}, META={"REMOTE_ADDR": ip})


@override_settings(RATELIMIT_POLICY_CHECK_PER_MINUTE=5)
class RateLimitAtomicTests(SimpleTestCase):
    def setUp(self):
        cache.clear()

    def test_exactly_limit_allowed_then_rejected(self):
        req = _req()
        for i in range(5):
            self.assertIsNone(check_rate_limit(req), f"request {i + 1} should be allowed")
        resp = check_rate_limit(req)
        self.assertIsNotNone(resp, "6th request must be rate-limited")
        self.assertEqual(resp.status_code, 429)

    def test_distinct_keys_have_independent_buckets(self):
        a, b = _req(ip="10.0.0.1"), _req(ip="10.0.0.2")
        for _ in range(5):
            self.assertIsNone(check_rate_limit(a))
        # a is now exhausted; b has its own bucket and is unaffected.
        self.assertIsNotNone(check_rate_limit(a))
        self.assertIsNone(check_rate_limit(b))

    def test_user_scoped_key_independent_of_ip(self):
        ip_req = _req(ip="10.0.0.9")
        user_req = _req(ip="10.0.0.9", user_id="u123")
        for _ in range(5):
            self.assertIsNone(check_rate_limit(user_req))
        self.assertIsNotNone(check_rate_limit(user_req))
        # same IP but IP-scoped bucket is separate from the user bucket.
        self.assertIsNone(check_rate_limit(ip_req))
