"""Login-specific rate throttles to stop brute-force / credential-stuffing.

Two independent scopes are applied to the login endpoint:

* ``login_ip``   — caps attempts per client IP (blunts distributed guessing
  against many accounts from one source).
* ``login_user`` — caps attempts per target email (blunts focused guessing
  against a single account, even across rotating IPs).

Rates are configured in ``REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]`` and backed
by the shared Redis cache. Exceeding either returns HTTP 429 with ``Retry-After``.
"""

from __future__ import annotations

import logging

from rest_framework.throttling import SimpleRateThrottle

logger = logging.getLogger(__name__)


class _AtomicWindowThrottle(SimpleRateThrottle):
    """Brute-force throttle with an *atomic* fixed-window counter.

    DRF's ``SimpleRateThrottle.allow_request`` does a non-atomic
    read-modify-write of a per-key history list (``cache.get`` then
    ``cache.set``). Under concurrency that races: several login attempts read
    the same stale count before any writes back, so the per-email / per-IP
    brute-force cap can be exceeded (a handful of extra guesses slip through).

    This override keeps the same rate (``num_requests`` / ``duration``) and the
    subclass ``get_cache_key`` scoping, but counts with the cache backend's
    atomic ``incr`` against a fixed window. ``cache.add`` seeds the counter with
    the window TTL only when absent; ``cache.incr`` is a single atomic op
    (Redis ``INCRBY`` / LocMem lock-guarded), so concurrent attempts cannot
    bypass the cap. Fails CLOSED: if the cache backend errors the request is
    denied rather than allowed.
    """

    def _window_cache_key(self, base_key):
        # Fixed window: a per-``duration`` bucket so the counter auto-resets via
        # TTL with no read-modify-write. Distinct buckets across windows avoid a
        # stale TTL pinning an old count.
        self.now = self.timer()
        window = int(self.now // self.duration)
        return "%s_w%d" % (base_key, window)

    def allow_request(self, request, view):
        if self.rate is None:
            return True

        base_key = self.get_cache_key(request, view)
        if base_key is None:
            # Not scoped (e.g. no email on this request); other throttles apply.
            return True

        self.key = self._window_cache_key(base_key)
        # ``self.history`` / ``self.now`` are read by the base ``wait()`` for the
        # ``Retry-After`` header; seed an empty history so wait() never crashes.
        self.history = []

        try:
            # Seed the counter (with TTL) only if it does not already exist, then
            # atomically increment. ``incr`` on a key seeded by ``add`` preserves
            # the original TTL, so the window expires ``duration`` seconds after
            # the first attempt in the window.
            self.cache.add(self.key, 0, self.duration)
            count = self.cache.incr(self.key)
        except Exception:  # noqa: BLE001 — fail CLOSED on any backend error.
            # Never leak backend internals to the client; log server-side only.
            logger.warning(
                "login throttle backend error for scope=%s; failing closed",
                self.scope,
                exc_info=True,
            )
            return False

        if count > self.num_requests:
            return False
        return True

    def wait(self):
        # Recommend waiting until the current fixed window rolls over. Avoids the
        # base implementation's reliance on a populated history list.
        if getattr(self, "duration", None) is None or getattr(self, "now", None) is None:
            return None
        return self.duration - (self.now % self.duration)


class LoginIPThrottle(_AtomicWindowThrottle):
    """Throttle login attempts per originating IP address."""

    scope = "login_ip"

    def get_cache_key(self, request, view):
        return self.cache_format % {
            "scope": self.scope,
            "ident": self.get_ident(request),
        }


class LoginEmailThrottle(_AtomicWindowThrottle):
    """Throttle login attempts per target email address."""

    scope = "login_user"

    def get_cache_key(self, request, view):
        data = request.data
        if not isinstance(data, dict):
            # Malformed body (e.g. a JSON array/string/number instead of an
            # object). `request.data.get` would raise AttributeError here and
            # this runs PRE-auth during check_throttles, so an unguarded crash
            # is an unauthenticated 500. Scope nothing on email; the IP throttle
            # still applies and the view's serializer rejects the body with 400.
            return None
        email_raw = data.get("email")
        # A non-string email (dict/int/list/bool) would crash `.strip()` with an
        # AttributeError here — and this runs PRE-auth in check_throttles, so an
        # unguarded crash is an UNAUTHENTICATED 500. Treat any non-string as
        # absent (the IP throttle still applies; the serializer 400s the body).
        email = (email_raw if isinstance(email_raw, str) else "").strip().lower()
        if not email:
            # No email supplied -> nothing to scope on; the IP throttle still applies.
            return None
        return self.cache_format % {"scope": self.scope, "ident": email}
