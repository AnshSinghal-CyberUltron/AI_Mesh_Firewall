"""Rate limiting for policy evaluation API (per IP or user)."""

import time

from django.conf import settings
from django.core.cache import cache
from rest_framework import status
from rest_framework.response import Response


def get_rate_limit_key(request):
    """Return key for rate limit: IP or user_id from body, scoped by org."""
    org = getattr(getattr(getattr(request, "user", None), "profile", None), "organization", None)
    org_prefix = f"org:{org.id}" if org else "org:0"
    user_id = (request.data or {}).get("user_id")
    if user_id is not None:
        return f"{org_prefix}:user:{user_id}"
    return f"{org_prefix}:ip:{request.META.get('REMOTE_ADDR', 'unknown')}"


def check_rate_limit(request):
    """
    Check rate limit for policy check API. Returns None if allowed,
    or Response(429) if limit exceeded.
    """
    limit = getattr(settings, "RATELIMIT_POLICY_CHECK_PER_MINUTE", 120)
    key = get_rate_limit_key(request)
    minute_bucket = int(time.time() // 60)
    cache_key = f"ratelimit:policy_check:{key}:{minute_bucket}"

    # #33: ATOMIC increment. The old `count = cache.get(); ...; cache.set(count+1)`
    # was a read-modify-write RACE — N concurrent requests all read the same count
    # and all wrote count+1, so the bucket under-counted and MORE than `limit`
    # requests/minute slipped through (rate-limit bypass under load). `cache.add`
    # (create-if-absent) + `cache.incr` (atomic; Redis INCR / locmem lock) makes the
    # increment race-free. `current` is the value AFTER this request, so rejecting on
    # `current > limit` admits exactly `limit` requests — identical to the old
    # `count >= limit` allowance, just without the race.
    cache.add(cache_key, 0, timeout=120)
    try:
        current = cache.incr(cache_key)
    except ValueError:
        # Bucket expired between add and incr (extremely rare) — reseed.
        cache.set(cache_key, 1, timeout=120)
        current = 1
    if current > limit:
        return Response(
            {"detail": "Rate limit exceeded. Try again later."},
            status=status.HTTP_429_TOO_MANY_REQUESTS,
            headers={"Retry-After": "60"},
        )
    return None
