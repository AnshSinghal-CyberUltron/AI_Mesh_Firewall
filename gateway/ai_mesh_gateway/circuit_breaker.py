"""
Automatic Circuit Breaker for LLM Models.

Tracks per-model error rates in Redis using fixed-window counters.
When a model's error rate exceeds the configured threshold within
a window, automatically triggers a temporary block.

States:
- CLOSED: normal operation
- OPEN: model is blocked, requests rejected/rerouted
- HALF_OPEN: after cooldown, allow a probe request to test recovery

Redis key structure:
- circuit:errors:{model}:{window} -- error count
- circuit:total:{model}:{window} -- total request count
- circuit:state:{model} -- current state (closed/open/half_open)
- circuit:open_at:{model} -- timestamp when circuit opened
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import redis.asyncio as aioredis

LOG = logging.getLogger("gateway.circuit_breaker")

WINDOW_SECONDS = 60
DEFAULT_ERROR_THRESHOLD = 0.5
DEFAULT_MIN_REQUESTS = 10
DEFAULT_COOLDOWN_SECONDS = 120
DEFAULT_PROBE_SUCCESS_COUNT = 3


class CircuitState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class CircuitStatus:
    """Current circuit breaker status for a model."""

    state: CircuitState = CircuitState.CLOSED
    error_rate: float = 0.0
    total_requests: int = 0
    error_count: int = 0
    opened_at: float = 0.0
    should_block: bool = False
    fallback_model: str = ""


class CircuitBreaker:
    """Redis-backed circuit breaker for LLM model error tracking."""

    def __init__(
        self,
        redis_client: aioredis.Redis,
        error_threshold: float = DEFAULT_ERROR_THRESHOLD,
        min_requests: int = DEFAULT_MIN_REQUESTS,
        cooldown_seconds: int = DEFAULT_COOLDOWN_SECONDS,
        probe_success_count: int = DEFAULT_PROBE_SUCCESS_COUNT,
    ):
        self._redis = redis_client
        self._error_threshold = error_threshold
        self._min_requests = min_requests
        self._cooldown_seconds = cooldown_seconds
        self._probe_success_count = probe_success_count

    def _window_key(self, prefix: str, model: str) -> str:
        window = int(time.time()) // WINDOW_SECONDS
        return f"circuit:{prefix}:{model}:{window}"

    def _kill_switch_key(self, org_slug: str, model: str) -> str:
        slug = (org_slug or "default").strip() or "default"
        return f"kill_switch:{slug}:model:{model}"

    async def _activate_kill_switch_trip(self, org_slug: str, model: str) -> None:
        """Mirror circuit OPEN to org-scoped kill_switch Redis key with TTL."""
        key = self._kill_switch_key(org_slug, model)
        payload = json.dumps(
            {
                "is_active": True,
                "action": "disable",
                "fallback_model": "",
                "reason": "circuit_breaker_open",
                "org_slug": (org_slug or "default").strip() or "default",
                "trigger_source": "circuit_breaker",
            }
        )
        ttl = self._cooldown_seconds * 2
        await self._redis.set(key, payload, ex=ttl)
        LOG.warning(
            "Circuit breaker tripped kill-switch key %s (ttl=%ds)",
            key,
            ttl,
        )

    async def _clear_kill_switch_trip(self, org_slug: str, model: str) -> None:
        """Remove circuit-breaker kill-switch key if we own it."""
        key = self._kill_switch_key(org_slug, model)
        try:
            raw = await self._redis.get(key)
            if not raw:
                return
            payload = json.loads(raw if isinstance(raw, str) else raw.decode())
            if payload.get("trigger_source") == "circuit_breaker":
                await self._redis.delete(key)
                LOG.info("Circuit breaker cleared kill-switch key %s", key)
        except Exception as exc:
            LOG.debug("Circuit breaker kill-switch clear failed: %s", exc)

    async def record_success(self, model: str, org_slug: str = "default") -> None:
        """Record a successful LLM response."""
        try:
            total_key = self._window_key("total", model)
            pipe = self._redis.pipeline(transaction=False)
            pipe.incr(total_key)
            pipe.expire(total_key, WINDOW_SECONDS * 3)
            await pipe.execute()

            state = await self._get_state(model)
            if state == CircuitState.HALF_OPEN:
                probe_key = f"circuit:probes:{model}"
                count = await self._redis.incr(probe_key)
                await self._redis.expire(probe_key, self._cooldown_seconds)
                if count >= self._probe_success_count:
                    await self._set_state(model, CircuitState.CLOSED)
                    await self._redis.delete(probe_key)
                    await self._clear_kill_switch_trip(org_slug, model)
                    LOG.info(
                        "Circuit CLOSED for model '%s' after %d successful probes",
                        model,
                        count,
                    )
        except Exception as exc:
            LOG.debug("Circuit breaker record_success failed: %s", exc)

    async def record_error(self, model: str, error_type: str = "", org_slug: str = "default") -> None:
        """Record a failed LLM response and evaluate threshold."""
        try:
            total_key = self._window_key("total", model)
            error_key = self._window_key("errors", model)
            pipe = self._redis.pipeline(transaction=False)
            pipe.incr(total_key)
            pipe.expire(total_key, WINDOW_SECONDS * 3)
            pipe.incr(error_key)
            pipe.expire(error_key, WINDOW_SECONDS * 3)
            results = await pipe.execute()

            total = int(results[0])
            errors = int(results[2])

            if total >= self._min_requests:
                error_rate = errors / total
                if error_rate >= self._error_threshold:
                    current_state = await self._get_state(model)
                    if current_state == CircuitState.CLOSED:
                        await self._set_state(model, CircuitState.OPEN)
                        await self._redis.set(
                            f"circuit:open_at:{model}",
                            str(time.time()),
                            ex=self._cooldown_seconds * 2,
                        )
                        await self._activate_kill_switch_trip(org_slug, model)
                        LOG.warning(
                            "Circuit OPEN for model '%s': error_rate=%.2f (%d/%d), threshold=%.2f",
                            model,
                            error_rate,
                            errors,
                            total,
                            self._error_threshold,
                        )

            state = await self._get_state(model)
            if state == CircuitState.HALF_OPEN:
                await self._set_state(model, CircuitState.OPEN)
                await self._redis.set(
                    f"circuit:open_at:{model}",
                    str(time.time()),
                    ex=self._cooldown_seconds * 2,
                )
                await self._activate_kill_switch_trip(org_slug, model)
                await self._redis.delete(f"circuit:probes:{model}")
                LOG.warning(
                    "Circuit re-OPENED for model '%s' after probe failure", model
                )
        except Exception as exc:
            LOG.debug("Circuit breaker record_error failed: %s", exc)

    async def check(self, model: str) -> CircuitStatus:
        """Check current circuit state for a model."""
        try:
            state = await self._get_state(model)

            if state == CircuitState.CLOSED:
                return CircuitStatus(state=CircuitState.CLOSED)

            if state == CircuitState.OPEN:
                opened_at_raw = await self._redis.get(f"circuit:open_at:{model}")
                opened_at = float(opened_at_raw) if opened_at_raw else 0.0
                elapsed = time.time() - opened_at

                if elapsed >= self._cooldown_seconds:
                    await self._set_state(model, CircuitState.HALF_OPEN)
                    LOG.info(
                        "Circuit HALF_OPEN for model '%s' (cooldown elapsed)", model
                    )
                    return CircuitStatus(
                        state=CircuitState.HALF_OPEN,
                        opened_at=opened_at,
                        should_block=False,
                    )

                return CircuitStatus(
                    state=CircuitState.OPEN,
                    opened_at=opened_at,
                    should_block=True,
                )

            return CircuitStatus(state=CircuitState.HALF_OPEN, should_block=False)

        except Exception as exc:
            # INVARIANT 6: Circuit breaker fails closed — block on errors
            LOG.error("Circuit breaker check failed (fail-CLOSED): %s", exc)
            return CircuitStatus(state=CircuitState.OPEN, should_block=True)

    async def _get_state(self, model: str) -> CircuitState:
        raw = await self._redis.get(f"circuit:state:{model}")
        if raw:
            try:
                return CircuitState(
                    raw.decode() if isinstance(raw, bytes) else raw
                )
            except ValueError:
                pass
        return CircuitState.CLOSED

    async def _set_state(self, model: str, state: CircuitState) -> None:
        await self._redis.set(
            f"circuit:state:{model}",
            state.value,
            ex=self._cooldown_seconds * 3,
        )
