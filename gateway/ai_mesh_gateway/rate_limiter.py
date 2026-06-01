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

        # Lua script: 
        # 1. Get current usage
        # 2. If current + estimated > limit, return 0 (blocked) and current
        # 3. Else, INCRBY estimated, set TTL if new, return 1 (allowed) and new total
        lua_script = """
        local current = tonumber(redis.call("GET", KEYS[1]) or "0")
        local limit = tonumber(ARGV[1])
        local estimated = tonumber(ARGV[2])
        local ttl = tonumber(ARGV[3])
        
        if current + estimated > limit then
            return {0, current}
        end
        
        local new_val = redis.call("INCRBY", KEYS[1], estimated)
        if current == 0 then
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
            # INVARIANT 6: Rate limiter fails closed — reject when Redis is down
            LOG.error("Rate limit check failed (fail-CLOSED): %s", exc)
            return False, 0

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
        try:
            client = self._client()
            current = await client.incr(redis_key)
            if current == 1:
                await client.expire(redis_key, KEY_TTL_SECONDS)
            if current > max_rpm:
                return False, int(current)
            return True, int(current)
        except Exception as exc:
            # INVARIANT 6: Model rate limit fails closed
            LOG.error("Model rate limit check failed (fail-CLOSED): %s", exc)
            return False, 0

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
        Fail-CLOSED on Redis errors (matches `check_rate_limit`).
        """
        if not org_slug or org_tpm_limit <= 0:
            return True, 0

        lua_script = """
        local current = tonumber(redis.call("GET", KEYS[1]) or "0")
        local limit = tonumber(ARGV[1])
        local estimated = tonumber(ARGV[2])
        local ttl = tonumber(ARGV[3])

        if current + estimated > limit then
            return {0, current}
        end

        local new_val = redis.call("INCRBY", KEYS[1], estimated)
        if current == 0 then
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
            LOG.error("Org rate limit check failed (fail-CLOSED): %s", exc)
            return False, 0

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
        1. check_rate_limit (the security gate) is FAIL-CLOSED
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
            await client.incrby(redis_key, delta)
            LOG.debug(
                "RateLimit record_usage: %s delta=%+d (actual=%d, est=%d)",
                key_hash[:12], delta, actual_tokens, estimated_tokens,
            )
        except Exception as exc:
            # Not a security issue (gate is fail-closed), but worth monitoring
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
            await client.incrby(redis_key, delta)
            LOG.debug(
                "Org TPM record_usage: org=%s delta=%+d (actual=%d, est=%d)",
                org_slug,
                delta,
                actual_tokens,
                estimated_tokens,
            )
        except Exception as exc:
            LOG.error("Org rate limit record_usage failed (non-critical): %s", exc)
