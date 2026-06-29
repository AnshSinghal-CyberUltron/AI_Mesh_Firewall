"""
Token-Per-Minute (TPM) rate limiter using Redis fixed-window counters.

Redis key structure:
    ratelimit:tpm:{key_hash}:{minute_bucket}

Each key has a TTL of 120 seconds (covers the current window plus overlap).

Design decisions:
    - Fixed-window for simplicity and sub-millisecond overhead on the hot path.
    - Pre-request check: reject if current bucket usage >= limit.
    - Post-response record: INCRBY actual total_tokens from LLM usage.
    - Fail-open on Redis errors: rate limiting is best-effort.
      (Authentication is fail-closed in AuthMiddleware; rate limiting
       should not block traffic if Redis is temporarily unreachable.)
"""

import logging
import time
from typing import Optional

import redis.asyncio as aioredis

LOG = logging.getLogger("gateway.rate_limiter")

WINDOW_SECONDS = 60
KEY_TTL_SECONDS = 120
KEY_PREFIX = "ratelimit:tpm"


class RateLimiter:
    """Async Redis-backed TPM rate limiter for the Gateway Data Plane."""

    def __init__(self, redis_url: str) -> None:
        self._redis_url: str = redis_url
        self._pool: Optional[aioredis.ConnectionPool] = None

    def _get_pool(self) -> aioredis.ConnectionPool:
        """Lazy-init the async connection pool."""
        if self._pool is None:
            self._pool = aioredis.ConnectionPool.from_url(
                self._redis_url,
                decode_responses=True,
                max_connections=100,
                socket_timeout=1.0,
                socket_connect_timeout=1.0,
                retry_on_timeout=True,
            )
        return self._pool

    def _client(self) -> aioredis.Redis:
        return aioredis.Redis(connection_pool=self._get_pool())

    @staticmethod
    def _bucket_key(key_hash: str) -> str:
        bucket = int(time.time()) // WINDOW_SECONDS
        return f"{KEY_PREFIX}:{key_hash}:{bucket}"

    async def check_rate_limit(
        self,
        key_hash: str,
        rate_limit_tpm: int,
        estimated_tokens: int = 20,
    ) -> tuple[bool, int]:
        """
        Check and increment the TPM budget atomically using a Lua script.

        Returns:
            (allowed, current_usage) — allowed=True if under limit.
        """
        if rate_limit_tpm <= 0:
            return True, 0

        # Lua script (single atomic Redis execution):
        # 1. Get current usage
        # 2. If current + estimated > limit, return 0 (blocked) and current
        # 3. Else, INCRBY estimated, ensure TTL, return 1 (allowed) and new total
        # The EXPIRE is set on first increment (current == 0) and is also
        # (re)asserted whenever the key has no TTL, so a counter key can never
        # persist without a TTL (e.g. if record_usage recreated the bucket via
        # INCRBY after the original TTL elapsed). This prevents a wedged bucket
        # whose counts never reset.
        lua_script = """
        local current = tonumber(redis.call("GET", KEYS[1]) or "0")
        local limit = tonumber(ARGV[1])
        local estimated = tonumber(ARGV[2])
        local ttl = tonumber(ARGV[3])

        if current + estimated > limit then
            return {0, current}
        end

        local new_val = redis.call("INCRBY", KEYS[1], estimated)
        if current == 0 or redis.call("TTL", KEYS[1]) < 0 then
            redis.call("EXPIRE", KEYS[1], ttl)
        end
        return {1, new_val}
        """

        try:
            client = self._client()
            redis_key = self._bucket_key(key_hash)
            
            # Execute Lua script
            result = await client.eval(
                lua_script, 
                1, redis_key, 
                rate_limit_tpm, estimated_tokens, KEY_TTL_SECONDS
            )
            
            LOG.info("RateLimit check: %s est=%d limit=%d result=%s", key_hash[:12], estimated_tokens, rate_limit_tpm, result)
            allowed = bool(result[0] == 1)
            current_usage = int(result[1])
            return allowed, current_usage
            
        except Exception as exc:
            # L6: fail-OPEN — match the module docstring ("rate limiting is
            # best-effort; should not block traffic if Redis is unreachable"). A
            # Redis outage/slowness must not mass-block tenants (security scanning
            # still runs). Was inconsistently fail-CLOSED here.
            LOG.error("Rate limit check failed (fail-OPEN, allowing): %s", exc)
            return True, 0

    async def check_model_rate_limit(
        self,
        model_name: str,
        max_rpm: int,
        org_slug: str = "",
    ) -> tuple[bool, int]:
        """
        Check per-model RPM limit using a fixed-window counter.
        Returns (allowed, current_count).

        The counter is scoped per ``org_slug`` so one tenant's traffic on a
        shared model identifier cannot exhaust another tenant's per-model
        budget. ``org_slug`` defaults to the literal ``"default"`` for
        backwards compatibility with callers that have no tenant context.
        """
        if max_rpm <= 0:
            return True, 0

        scope = org_slug or "default"
        bucket = int(time.time()) // WINDOW_SECONDS
        redis_key = f"ratelimit:model:{scope}:{model_name}:{bucket}"

        # Atomic INCR + EXPIRE: a separate INCR-then-EXPIRE pair can leave the
        # counter key with NO TTL if the process dies between the two calls,
        # wedging the bucket forever (counts never reset). A Lua script runs
        # both inside a single atomic Redis execution. EXPIRE is set on the
        # first increment (current == 1) and is idempotently (re)asserted on
        # every increment so the bucket can never persist without a TTL.
        lua_script = """
        local current = redis.call("INCR", KEYS[1])
        if current == 1 then
            redis.call("EXPIRE", KEYS[1], ARGV[1])
        elseif redis.call("TTL", KEYS[1]) < 0 then
            redis.call("EXPIRE", KEYS[1], ARGV[1])
        end
        return current
        """
        try:
            client = self._client()
            current = int(await client.eval(
                lua_script, 1, redis_key, KEY_TTL_SECONDS
            ))
            if current > max_rpm:
                return False, current
            return True, current
        except Exception as exc:
            # L6: fail-OPEN (consistent with the org/key TPM paths + the module
            # docstring) — a limiter-backend error must not block traffic.
            LOG.error("Model rate limit check failed (fail-OPEN, allowing): %s", exc)
            return True, 0

    async def check_org_rate_limit(
        self,
        org_slug: str,
        org_tpm_limit: int,
        estimated_tokens: int = 20,
    ) -> tuple[bool, int]:
        """
        Organization-wide TPM ceiling using a fixed-window Lua counter.

        This is the FIRST tier of the two-tier check: a single org cannot
        exceed its plan cap no matter how many API keys it issues. The
        per-key check (`check_rate_limit`) runs AFTER this one and applies
        a tighter per-key ceiling.

        Redis key: `ratelimit:org:tpm:{org_slug}:{bucket}` with 120s TTL.
        Fail-OPEN on Redis errors (L6 — matches `check_rate_limit` and the module
        docstring: rate limiting is best-effort and must not mass-block tenants
        if Redis is unreachable; security scanning still runs).
        """
        if not org_slug or org_tpm_limit <= 0:
            return True, 0

        # Single atomic Redis execution. EXPIRE is set on the first increment
        # and (re)asserted whenever the key has no TTL, so the org bucket can
        # never persist without a TTL and wedge the limiter for that org.
        lua_script = """
        local current = tonumber(redis.call("GET", KEYS[1]) or "0")
        local limit = tonumber(ARGV[1])
        local estimated = tonumber(ARGV[2])
        local ttl = tonumber(ARGV[3])

        if current + estimated > limit then
            return {0, current}
        end

        local new_val = redis.call("INCRBY", KEYS[1], estimated)
        if current == 0 or redis.call("TTL", KEYS[1]) < 0 then
            redis.call("EXPIRE", KEYS[1], ttl)
        end
        return {1, new_val}
        """

        bucket = int(time.time()) // WINDOW_SECONDS
        redis_key = f"ratelimit:org:tpm:{org_slug}:{bucket}"

        try:
            client = self._client()
            result = await client.eval(
                lua_script,
                1, redis_key,
                org_tpm_limit, estimated_tokens, KEY_TTL_SECONDS,
            )
            allowed = bool(result[0] == 1)
            current_usage = int(result[1])
            return allowed, current_usage
        except Exception as exc:
            # L6: fail-OPEN (matches the module docstring + the enforcement layer).
            LOG.error("Org rate limit check failed (fail-OPEN, allowing): %s", exc)
            return True, 0

    async def record_usage(
        self,
        key_hash: str,
        actual_tokens: int,
        estimated_tokens: int = 20,
    ) -> None:
        """
        Adjust the TPM counter after an LLM response.

        ``check_rate_limit`` pre-charges ``estimated_tokens`` *before* the
        request is forwarded. Once the real token count is known, this
        method corrects the bucket by adding the delta
        (actual - estimated). A negative delta (over-estimate) is fine --
        INCRBY accepts negative values.

        SEC-08 NOTE: Fail-open by design here is acceptable because:
        1. rate limiting is best-effort and FAIL-OPEN everywhere (L6) — a Redis
           outage must not mass-block tenants; security scanning still runs
        2. record_usage is a post-request adjustment, not a security gate
        3. Any variance self-corrects when the window expires (60s)
        4. Worst case: minor quota variance until window reset
        """
        delta = actual_tokens - estimated_tokens
        if delta == 0:
            return

        try:
            client = self._client()
            redis_key = self._bucket_key(key_hash)
            _new_val = await client.incrby(redis_key, delta)
            if _new_val is not None and _new_val < 0:
                # Floor-clamp (#10): a large over-estimate correction must never
                # drive the TPM counter NEGATIVE — a negative count would
                # under-charge (effectively disable the limit) until it climbs
                # back to 0. Reset to 0, preserving the window TTL where supported.
                try:
                    await client.set(redis_key, 0, keepttl=True)
                except TypeError:
                    await client.set(redis_key, 0)
            LOG.debug(
                "RateLimit record_usage: %s delta=%+d (actual=%d, est=%d)",
                key_hash[:12], delta, actual_tokens, estimated_tokens,
            )
        except Exception as exc:
            # Not a security issue (the limiter is fail-OPEN, best-effort), but
            # worth monitoring.
            LOG.error("Rate limit record_usage failed (non-critical): %s", exc)

    async def record_org_usage(
        self,
        org_slug: str,
        actual_tokens: int,
        estimated_tokens: int = 20,
    ) -> None:
        """
        Reconcile org TPM bucket after stream/non-stream completion.

        ``check_org_rate_limit`` pre-charges ``estimated_tokens``; this applies
        the delta once actual usage is known.
        """
        if not org_slug:
            return
        delta = actual_tokens - estimated_tokens
        if delta == 0:
            return

        bucket = int(time.time()) // WINDOW_SECONDS
        redis_key = f"ratelimit:org:tpm:{org_slug}:{bucket}"
        try:
            client = self._client()
            _new_val = await client.incrby(redis_key, delta)
            if _new_val is not None and _new_val < 0:
                # Floor-clamp (#10): a large over-estimate correction must never
                # drive the TPM counter NEGATIVE — a negative count would
                # under-charge (effectively disable the limit) until it climbs
                # back to 0. Reset to 0, preserving the window TTL where supported.
                try:
                    await client.set(redis_key, 0, keepttl=True)
                except TypeError:
                    await client.set(redis_key, 0)
            LOG.debug(
                "Org TPM record_usage: org=%s delta=%+d (actual=%d, est=%d)",
                org_slug,
                delta,
                actual_tokens,
                estimated_tokens,
            )
        except Exception as exc:
            LOG.error("Org rate limit record_usage failed (non-critical): %s", exc)
