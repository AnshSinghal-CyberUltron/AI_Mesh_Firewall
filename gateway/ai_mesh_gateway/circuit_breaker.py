"""
Automatic Circuit Breaker for LLM Models.

Tracks per-model error rates in Redis using fixed-window counters.
When a model's error rate exceeds the configured threshold within
a window, automatically triggers a temporary block.

States:
- CLOSED: normal operation
- OPEN: model is blocked, requests rejected/rerouted
- HALF_OPEN: after cooldown, allow a bounded set of probe requests

Redis key structure:
- circuit:errors:{model}:{window} -- error count
- circuit:total:{model}:{window} -- total request count
- circuit:state:{model} -- current state (closed/open/half_open)
- circuit:open_at:{model} -- timestamp when circuit opened
- circuit:half_open_gate:{model} -- SET NX gate so exactly one worker
  performs the OPEN -> HALF_OPEN transition per cooldown cycle
- circuit:epoch:{model} -- monotonically increasing half-open cycle id;
  scopes the probe counters so stale counts from a previous cycle can
  never leak into a new one
- circuit:probes:{model}:{epoch} -- successful probe count for the cycle
- circuit:probe_admit:{model}:{epoch} -- probes admitted this cycle
  (caps concurrent probes at probe_success_count; TTL self-heals if an
  admitted probe never reports an outcome)

Concurrency model (no Lua so the breaker stays fakeredis-testable):
- OPEN -> HALF_OPEN: SET NX gate; only the winner resets counters
  (epoch bump + delete) and flips the state. Losers re-read the state
  and either join probe admission or stay blocked.
- HALF_OPEN probe admission: epoch-scoped INCR with a hard cap.
- Probe success counting and HALF_OPEN -> OPEN re-open both run as
  WATCH/MULTI transactions on the state key, so a success racing a
  concurrent re-open raises WatchError and is discarded instead of
  closing a circuit that just re-opened.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

from redis.exceptions import WatchError

if TYPE_CHECKING:
    import redis.asyncio as aioredis

LOG = logging.getLogger("gateway.circuit_breaker")

WINDOW_SECONDS = 60
DEFAULT_ERROR_THRESHOLD = 0.5
DEFAULT_MIN_REQUESTS = 10
DEFAULT_COOLDOWN_SECONDS = 120
DEFAULT_PROBE_SUCCESS_COUNT = 3

# Bounded optimistic-lock retries. Contention is capped by the probe
# admission limit, so a handful of retries is always enough; on
# exhaustion we drop the probe count (safe: circuit merely stays
# HALF_OPEN until the next probe).
WATCH_RETRY_ATTEMPTS = 8


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

    # ── Key helpers ───────────────────────────────────────────────────

    def _window_key(self, prefix: str, model: str) -> str:
        window = int(time.time()) // WINDOW_SECONDS
        return f"circuit:{prefix}:{model}:{window}"

    def _state_key(self, model: str) -> str:
        return f"circuit:state:{model}"

    def _open_at_key(self, model: str) -> str:
        return f"circuit:open_at:{model}"

    def _gate_key(self, model: str) -> str:
        return f"circuit:half_open_gate:{model}"

    def _epoch_key(self, model: str) -> str:
        return f"circuit:epoch:{model}"

    def _probes_key(self, model: str, epoch: int) -> str:
        return f"circuit:probes:{model}:{epoch}"

    def _admit_key(self, model: str, epoch: int) -> str:
        return f"circuit:probe_admit:{model}:{epoch}"

    def _legacy_probes_key(self, model: str) -> str:
        # Pre-epoch unscoped probe counter; deleted defensively so old
        # deployments' stale counts cannot leak into a new cycle.
        return f"circuit:probes:{model}"

    def _ttl(self, multiplier: int = 1) -> int:
        return max(1, self._cooldown_seconds * multiplier)

    def _kill_switch_key(self, org_slug: str, model: str) -> str:
        slug = (org_slug or "default").strip() or "default"
        return f"kill_switch:{slug}:model:{model}"

    # ── Kill-switch mirroring ─────────────────────────────────────────

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
        # TTL == cooldown (NOT 2x). The kill-switch mirror is checked on the
        # request path BEFORE CircuitBreaker.check, so a mirror that outlives the
        # cooldown keeps blocking the model past the point the breaker is eligible
        # to HALF_OPEN — the blocked request never reaches CB.check to probe, so the
        # breaker can never self-heal (deadlock until TTL). Expiring the mirror at
        # exactly the cooldown lets the first post-cooldown request flow to CB.check
        # → HALF_OPEN probe; a failed probe re-trips and re-sets the mirror.
        ttl = self._ttl()
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

    async def _clear_kill_switch_for_model(self, model: str) -> None:
        """Clear any circuit-breaker-owned kill-switch key for this model.

        The org slug is not known at the OPEN -> HALF_OPEN transition (``check``
        is not org-scoped), so we scan the org-scoped kill-switch keyspace for
        this model and drop only the keys we own. Without this, a stale
        ``circuit_breaker``-tripped kill-switch keeps a recovered model 503'd
        while the circuit is already probing in HALF_OPEN.
        """
        pattern = f"kill_switch:*:model:{model}"
        try:
            async for key in self._redis.scan_iter(match=pattern):
                try:
                    raw = await self._redis.get(key)
                    if not raw:
                        continue
                    payload = json.loads(raw if isinstance(raw, str) else raw.decode())
                    if payload.get("trigger_source") == "circuit_breaker":
                        await self._redis.delete(key)
                        LOG.info(
                            "Circuit breaker cleared stale kill-switch key %s on HALF_OPEN",
                            key,
                        )
                except Exception as exc:
                    LOG.debug("Circuit breaker kill-switch scan-clear failed: %s", exc)
        except Exception as exc:
            LOG.debug("Circuit breaker kill-switch scan failed: %s", exc)

    # ── Recording ─────────────────────────────────────────────────────

    async def record_success(self, model: str, org_slug: str = "default") -> None:
        """Record a successful LLM response."""
        try:
            total_key = self._window_key("total", model)
            # CHG-0086: MULTI/EXEC so INCR + EXPIRE commit atomically — a mid-pipeline
            # failure (connection drop) between them would otherwise orphan the counter
            # with NO TTL (same class as CHG-0062/0084). execute() still returns results.
            pipe = self._redis.pipeline(transaction=True)
            pipe.incr(total_key)
            pipe.expire(total_key, WINDOW_SECONDS * 3)
            await pipe.execute()

            state = await self._get_state(model)
            if state == CircuitState.HALF_OPEN:
                await self._count_probe_success(model, org_slug)
        except Exception as exc:
            LOG.debug("Circuit breaker record_success failed: %s", exc)

    async def record_error(self, model: str, error_type: str = "", org_slug: str = "default") -> None:
        """Record a failed LLM response and evaluate threshold."""
        try:
            total_key = self._window_key("total", model)
            error_key = self._window_key("errors", model)
            pipe = self._redis.pipeline(transaction=True)  # CHG-0086: atomic INCR+EXPIRE
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
                            self._open_at_key(model),
                            str(time.time()),
                            ex=self._ttl(2),
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
                reopened = await self._reopen_from_half_open(model)
                if reopened:
                    await self._activate_kill_switch_trip(org_slug, model)
                    LOG.warning(
                        "Circuit re-OPENED for model '%s' after probe failure", model
                    )
        except Exception as exc:
            LOG.debug("Circuit breaker record_error failed: %s", exc)

    # ── Gating ────────────────────────────────────────────────────────

    async def check(self, model: str) -> CircuitStatus:
        """Check current circuit state for a model."""
        try:
            state = await self._get_state(model)

            if state == CircuitState.CLOSED:
                return CircuitStatus(state=CircuitState.CLOSED)

            opened_at = 0.0
            if state == CircuitState.OPEN:
                opened_at_raw = await self._redis.get(self._open_at_key(model))
                opened_at = float(opened_at_raw) if opened_at_raw else 0.0
                elapsed = time.time() - opened_at

                if elapsed < self._cooldown_seconds:
                    return CircuitStatus(
                        state=CircuitState.OPEN,
                        opened_at=opened_at,
                        should_block=True,
                    )

                # Cooldown elapsed: exactly ONE caller may flip the
                # circuit to HALF_OPEN (SET NX gate). The winner resets
                # the probe counters before the state flip so stale
                # counts can never instantly re-close the circuit.
                won = await self._redis.set(
                    self._gate_key(model), "1", nx=True, ex=self._ttl()
                )
                if won:
                    epoch = await self._begin_half_open(model)
                    # The transition winner is the first admitted probe.
                    await self._admit_probe(model, epoch)
                    LOG.info(
                        "Circuit HALF_OPEN for model '%s' (cooldown elapsed)", model
                    )
                    return CircuitStatus(
                        state=CircuitState.HALF_OPEN,
                        opened_at=opened_at,
                        should_block=False,
                    )

                # Lost the gate: another worker owns the transition.
                # Block unless the HALF_OPEN flip has already landed.
                state = await self._get_state(model)
                if state != CircuitState.HALF_OPEN:
                    return CircuitStatus(
                        state=CircuitState.OPEN,
                        opened_at=opened_at,
                        should_block=True,
                    )

            # HALF_OPEN: bounded probe admission for the current cycle.
            epoch = await self._get_epoch(model)
            admitted = await self._admit_probe(model, epoch)
            if admitted <= self._probe_success_count:
                return CircuitStatus(
                    state=CircuitState.HALF_OPEN,
                    opened_at=opened_at,
                    should_block=False,
                )
            return CircuitStatus(
                state=CircuitState.HALF_OPEN,
                opened_at=opened_at,
                should_block=True,
            )

        except Exception as exc:
            # INVARIANT 6: Circuit breaker fails closed — block on errors
            LOG.error("Circuit breaker check failed (fail-CLOSED): %s", exc)
            return CircuitStatus(state=CircuitState.OPEN, should_block=True)

    # ── Atomic transition helpers ─────────────────────────────────────

    async def _begin_half_open(self, model: str) -> int:
        """Start a fresh half-open cycle (gate already won by caller).

        Bumps the epoch and wipes every probe counter BEFORE flipping the
        state, so concurrent record_success calls can never observe
        HALF_OPEN alongside a stale probe count.
        """
        epoch_key = self._epoch_key(model)
        pipe = self._redis.pipeline(transaction=True)  # CHG-0086: atomic INCR+EXPIRE(+DELETE)
        pipe.incr(epoch_key)
        pipe.expire(epoch_key, self._ttl(3))
        pipe.delete(self._legacy_probes_key(model))
        results = await pipe.execute()
        epoch = int(results[0])
        # Defensive: clear counters for this epoch id (covers epoch-key
        # expiry causing id reuse).
        await self._redis.delete(
            self._probes_key(model, epoch), self._admit_key(model, epoch)
        )
        await self._set_state(model, CircuitState.HALF_OPEN)
        # Drop any stale circuit-breaker kill-switch so a recovered model can
        # serve probe traffic instead of staying 503'd in HALF_OPEN.
        await self._clear_kill_switch_for_model(model)
        return epoch

    async def _admit_probe(self, model: str, epoch: int) -> int:
        """Atomically claim a probe slot; returns the admission number."""
        admit_key = self._admit_key(model, epoch)
        # CHG-0086: MULTI/EXEC — this method's docstring promises an ATOMIC probe-slot
        # claim, but INCR + EXPIRE were not wrapped, so a mid-pipeline failure could
        # orphan the admit counter with no TTL.
        pipe = self._redis.pipeline(transaction=True)
        pipe.incr(admit_key)
        pipe.expire(admit_key, self._ttl())
        results = await pipe.execute()
        return int(results[0])

    async def _count_probe_success(self, model: str, org_slug: str) -> None:
        """Count a successful probe; close the circuit at the threshold.

        Runs as a WATCH/MULTI transaction on the state + epoch + probe
        keys so a concurrent failure re-opening the circuit invalidates
        this count (WatchError) instead of letting it close a circuit
        that just re-opened.
        """
        state_key = self._state_key(model)
        epoch_key = self._epoch_key(model)
        for _ in range(WATCH_RETRY_ATTEMPTS):
            closing = False
            count = 0
            try:
                async with self._redis.pipeline(transaction=True) as pipe:
                    await pipe.watch(state_key, epoch_key)
                    raw_state = await pipe.get(state_key)
                    if self._parse_state(raw_state) != CircuitState.HALF_OPEN:
                        await pipe.unwatch()
                        return
                    raw_epoch = await pipe.get(epoch_key)
                    epoch = int(raw_epoch) if raw_epoch is not None else 0
                    probes_key = self._probes_key(model, epoch)
                    await pipe.watch(probes_key)
                    raw_count = await pipe.get(probes_key)
                    count = (int(raw_count) if raw_count is not None else 0) + 1
                    closing = count >= self._probe_success_count
                    pipe.multi()
                    if closing:
                        pipe.set(
                            state_key,
                            CircuitState.CLOSED.value,
                            ex=self._ttl(3),
                        )
                        pipe.delete(
                            probes_key,
                            self._admit_key(model, epoch),
                            self._gate_key(model),
                        )
                    else:
                        pipe.set(probes_key, count, ex=self._ttl())
                    await pipe.execute()
            except WatchError:
                continue
            if closing:
                await self._clear_kill_switch_trip(org_slug, model)
                LOG.info(
                    "Circuit CLOSED for model '%s' after %d successful probes",
                    model,
                    count,
                )
            return
        LOG.debug(
            "Circuit breaker dropped probe success for '%s' (watch contention)",
            model,
        )

    async def _reopen_from_half_open(self, model: str) -> bool:
        """Atomically transition HALF_OPEN -> OPEN after a probe failure.

        Returns True iff this caller performed the transition. WATCHes
        the state key so it serializes against _count_probe_success.
        """
        state_key = self._state_key(model)
        for _ in range(WATCH_RETRY_ATTEMPTS):
            try:
                async with self._redis.pipeline(transaction=True) as pipe:
                    await pipe.watch(state_key)
                    raw_state = await pipe.get(state_key)
                    if self._parse_state(raw_state) != CircuitState.HALF_OPEN:
                        await pipe.unwatch()
                        return False
                    raw_epoch = await pipe.get(self._epoch_key(model))
                    epoch = int(raw_epoch) if raw_epoch is not None else 0
                    pipe.multi()
                    pipe.set(state_key, CircuitState.OPEN.value, ex=self._ttl(3))
                    pipe.set(
                        self._open_at_key(model), str(time.time()), ex=self._ttl(2)
                    )
                    pipe.delete(
                        self._probes_key(model, epoch),
                        self._admit_key(model, epoch),
                        self._gate_key(model),
                        self._legacy_probes_key(model),
                    )
                    await pipe.execute()
                    return True
            except WatchError:
                continue
        return False

    # ── State primitives ──────────────────────────────────────────────

    @staticmethod
    def _parse_state(raw) -> CircuitState:
        if raw:
            try:
                return CircuitState(raw.decode() if isinstance(raw, bytes) else raw)
            except ValueError:
                pass
        return CircuitState.CLOSED

    async def _get_state(self, model: str) -> CircuitState:
        raw = await self._redis.get(self._state_key(model))
        return self._parse_state(raw)

    async def _get_epoch(self, model: str) -> int:
        raw = await self._redis.get(self._epoch_key(model))
        try:
            return int(raw) if raw is not None else 0
        except (TypeError, ValueError):
            return 0

    async def _set_state(self, model: str, state: CircuitState) -> None:
        await self._redis.set(
            self._state_key(model),
            state.value,
            ex=self._ttl(3),
        )
