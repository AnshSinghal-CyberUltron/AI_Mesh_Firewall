"""
Per-(org_slug, scanning_model_id) circuit breaker for the Tier-2 Bedrock
scanner (Phase 0 D-G3-v3).

Why per-(org, model)?
    The Bedrock scanning model (e.g. ``global.anthropic.claude-haiku-4-5-20251001-v1:0``) is the
    *platform* guard model. Orgs each run different *inference* models, but
    they all share the platform scanner. We key on ``(org_slug, model_id)``
    so a misbehaving platform-scan model for one org (rate-limit, region
    outage, throttle) does not deny Tier-2 to other orgs. ``model_id`` is
    included so swapping the scanning model (e.g. Claude Haiku) starts with
    a fresh window.

States
------
CLOSED      - Normal. All requests pass through to Bedrock. Failures are
              recorded in a 12-bucket * 5s sliding window (60s total).
              When ``failures/total >= FAILURE_THRESHOLD`` and
              ``total >= MIN_CALLS``, transition to OPEN.

OPEN        - All Bedrock calls are short-circuited for ``COOLDOWN_SECONDS``.
              Callers consult ``tier2_strict`` to decide pass-through vs
              hard-fail (HTTP 451 ``tier2_unavailable_strict``).
              After cooldown elapses, transition to HALF_OPEN on next probe.

HALF_OPEN   - Single probe call allowed. Success -> CLOSED (window reset).
              Failure -> OPEN (cooldown restarts).

Memory bound
------------
At most ``MAX_KEYS = 1000`` per-(org, model) entries. When the cap is
reached, the oldest CLOSED entry is evicted (LRU over ``last_touch_ts``).
OPEN / HALF_OPEN entries are never evicted while in their transitional
state — they age out only after they return to CLOSED.

Concurrency
-----------
State is mutated from the asyncio coroutine that owns the request
(``scan_prompt_with_tier2`` and friends). The Bedrock sync call itself
runs in a thread-pool executor, but the breaker's ``record_success`` /
``record_failure`` are invoked back on the event loop after the executor
future resolves. No lock is required as long as callers respect this
invariant. ``emit_operational_event`` on state change is dispatched via
``asyncio.create_task`` (fire-and-forget) per the Perf-Hawk constraint.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from collections import OrderedDict, deque
from dataclasses import dataclass, field
from typing import Deque, Optional, Tuple

LOG = logging.getLogger("gateway.tier2_breaker")

# --- Env-tunable thresholds -------------------------------------------------

MIN_CALLS = max(1, int(os.getenv("TIER2_BREAKER_MIN_CALLS", "5")))
FAILURE_THRESHOLD = float(os.getenv("TIER2_BREAKER_FAILURE_THRESHOLD", "0.5"))
WINDOW_SECONDS = max(5, int(os.getenv("TIER2_BREAKER_WINDOW_SECONDS", "60")))
COOLDOWN_SECONDS = max(1, int(os.getenv("TIER2_BREAKER_COOLDOWN_SECONDS", "30")))
MAX_KEYS = max(1, int(os.getenv("TIER2_BREAKER_MAX_KEYS", "1000")))

# Sliding window: 12 buckets * (WINDOW_SECONDS / 12) seconds per bucket.
_BUCKET_COUNT = 12
_BUCKET_SECONDS = max(1.0, WINDOW_SECONDS / _BUCKET_COUNT)

# --- States -----------------------------------------------------------------

STATE_CLOSED = "closed"
STATE_OPEN = "open"
STATE_HALF_OPEN = "half_open"


class Tier2UnavailableStrict(Exception):
    """
    Raised when the breaker is OPEN, the cooldown has not elapsed, and the
    caller has ``tier2_strict=True``. The gateway HTTP layer catches this
    and returns HTTP 451 with the degraded-envelope body.
    """

    def __init__(self, org_slug: str, model_id: str, retry_after_seconds: int):
        self.org_slug = org_slug
        self.model_id = model_id
        self.retry_after_seconds = max(1, int(retry_after_seconds))
        super().__init__(
            f"Tier-2 scanner OPEN for org={org_slug!r} model={model_id!r}; "
            f"strict mode, retry_after={self.retry_after_seconds}s"
        )


# --- Per-key state ----------------------------------------------------------


@dataclass
class _KeyState:
    state: str = STATE_CLOSED
    opened_at: float = 0.0          # epoch seconds when state became OPEN
    last_touch_ts: float = field(default_factory=time.time)
    # Sliding-window buckets: each entry is (bucket_start_ts, total, failures)
    buckets: Deque[Tuple[float, int, int]] = field(default_factory=lambda: deque(maxlen=_BUCKET_COUNT))

    def _evict_stale_buckets(self, now: float) -> None:
        horizon = now - WINDOW_SECONDS
        while self.buckets and self.buckets[0][0] < horizon:
            self.buckets.popleft()

    def _current_bucket(self, now: float) -> Tuple[float, int, int]:
        bucket_start = now - (now % _BUCKET_SECONDS)
        if self.buckets and self.buckets[-1][0] == bucket_start:
            return self.buckets[-1]
        entry = (bucket_start, 0, 0)
        self.buckets.append(entry)
        return entry

    def record(self, now: float, *, failure: bool) -> Tuple[int, int]:
        """Add one observation; return (total, failures) across the window."""
        self._evict_stale_buckets(now)
        self._current_bucket(now)
        bucket_start, total, failures = self.buckets[-1]
        total += 1
        if failure:
            failures += 1
        self.buckets[-1] = (bucket_start, total, failures)
        self.last_touch_ts = now
        # Aggregate over window
        agg_total = sum(b[1] for b in self.buckets)
        agg_failures = sum(b[2] for b in self.buckets)
        return agg_total, agg_failures

    def reset_window(self, now: float) -> None:
        self.buckets.clear()
        self.last_touch_ts = now


# --- Breaker ----------------------------------------------------------------


class BedrockTier2Breaker:
    """
    Process-local circuit breaker for Bedrock Tier-2 calls.

    Use ``allow(org_slug, model_id, strict=...)`` BEFORE dispatching to
    Bedrock. It returns ``True`` if the call should proceed, ``False`` if
    the breaker is OPEN and ``strict=False`` (caller should pass-through
    with ``EVENT_CLASS_TIER2_DEGRADED_PASS``), and raises
    ``Tier2UnavailableStrict`` if the breaker is OPEN and ``strict=True``.

    After the Bedrock call (or its exception path) completes, call
    ``record_result(org_slug, model_id, failure=<bool>)``.
    """

    def __init__(self) -> None:
        # LRU dict keyed by (org_slug, model_id); ordering is mutation order.
        self._states: "OrderedDict[Tuple[str, str], _KeyState]" = OrderedDict()

    # -- Public API --------------------------------------------------------

    def state_of(self, org_slug: str, model_id: str) -> str:
        key = (org_slug or "", model_id or "")
        st = self._states.get(key)
        return st.state if st else STATE_CLOSED

    def allow(self, org_slug: str, model_id: str, *, strict: bool) -> bool:
        """
        Return True if the call should proceed to Bedrock.

        - CLOSED          -> True
        - HALF_OPEN       -> True (probe; caller must record_result)
        - OPEN, cooled    -> transition to HALF_OPEN, return True
        - OPEN, not cooled, strict=False -> False (caller passes through)
        - OPEN, not cooled, strict=True  -> raises ``Tier2UnavailableStrict``
        """
        now = time.time()
        key = (org_slug or "", model_id or "")
        st = self._states.get(key)
        if st is None:
            st = self._allocate(key, now)
            return True

        # Move to MRU end on access (LRU bookkeeping).
        self._states.move_to_end(key)
        st.last_touch_ts = now

        if st.state == STATE_CLOSED or st.state == STATE_HALF_OPEN:
            return True

        # STATE_OPEN
        elapsed = now - st.opened_at
        if elapsed >= COOLDOWN_SECONDS:
            self._transition(key, st, STATE_HALF_OPEN, now)
            return True
        remaining = int(COOLDOWN_SECONDS - elapsed) or 1
        if strict:
            raise Tier2UnavailableStrict(
                org_slug=org_slug or "",
                model_id=model_id or "",
                retry_after_seconds=remaining,
            )
        return False

    def record_result(self, org_slug: str, model_id: str, *, failure: bool) -> None:
        """Record one observation, advancing breaker state as needed."""
        now = time.time()
        key = (org_slug or "", model_id or "")
        st = self._states.get(key)
        if st is None:
            st = self._allocate(key, now)
        else:
            self._states.move_to_end(key)

        if st.state == STATE_HALF_OPEN:
            if failure:
                self._transition(key, st, STATE_OPEN, now)
            else:
                self._transition(key, st, STATE_CLOSED, now)
            return

        total, failures = st.record(now, failure=failure)
        if (
            st.state == STATE_CLOSED
            and total >= MIN_CALLS
            and (failures / total) >= FAILURE_THRESHOLD
        ):
            self._transition(key, st, STATE_OPEN, now)

    def retry_after(self, org_slug: str, model_id: str) -> int:
        """Seconds remaining in OPEN cooldown; 0 if not OPEN."""
        key = (org_slug or "", model_id or "")
        st = self._states.get(key)
        if not st or st.state != STATE_OPEN:
            return 0
        remaining = COOLDOWN_SECONDS - (time.time() - st.opened_at)
        return max(1, int(remaining)) if remaining > 0 else 0

    # -- Internal ----------------------------------------------------------

    def _allocate(self, key: Tuple[str, str], now: float) -> _KeyState:
        # Evict oldest CLOSED entry if we're at the cap.
        if len(self._states) >= MAX_KEYS:
            victim_key: Optional[Tuple[str, str]] = None
            for k, st in self._states.items():
                if st.state == STATE_CLOSED:
                    victim_key = k
                    break
            if victim_key is not None:
                self._states.pop(victim_key, None)
            else:
                # All entries are OPEN/HALF_OPEN; evict the absolute oldest.
                k, _ = self._states.popitem(last=False)
                LOG.warning("tier2_breaker LRU cap reached; evicted non-CLOSED key=%r", k)
        st = _KeyState()
        st.last_touch_ts = now
        self._states[key] = st
        return st

    def _transition(
        self,
        key: Tuple[str, str],
        st: _KeyState,
        new_state: str,
        now: float,
    ) -> None:
        old_state = st.state
        if old_state == new_state:
            return
        st.state = new_state
        if new_state == STATE_OPEN:
            st.opened_at = now
        elif new_state == STATE_CLOSED:
            st.opened_at = 0.0
            st.reset_window(now)
        # HALF_OPEN keeps opened_at and window as-is.

        org_slug, model_id = key
        LOG.warning(
            "tier2_breaker state change org=%s model=%s %s -> %s",
            org_slug, model_id, old_state, new_state,
        )
        # Fire-and-forget telemetry. Lazy import to keep this module
        # importable from tooling without dragging in the full gateway.
        try:
            try:
                from .telemetry_ops import (
                    EVENT_CLASS_TIER2_BREAKER_STATE_CHANGE,
                    emit_operational_event,
                )
            except ImportError:  # absolute import fallback (sys.path layout)
                from telemetry_ops import (  # type: ignore[no-redef]
                    EVENT_CLASS_TIER2_BREAKER_STATE_CHANGE,
                    emit_operational_event,
                )
            loop = asyncio.get_event_loop()
            if loop.is_running():
                loop.create_task(
                    emit_operational_event(
                        EVENT_CLASS_TIER2_BREAKER_STATE_CHANGE,
                        org_slug=org_slug or None,
                        severity="warning",
                        metadata={
                            "model_id": model_id,
                            "from_state": old_state,
                            "to_state": new_state,
                            "cooldown_seconds": COOLDOWN_SECONDS if new_state == STATE_OPEN else 0,
                        },
                    )
                )
        except Exception:  # never let telemetry break the breaker
            LOG.debug("tier2_breaker state-change telemetry emit failed", exc_info=True)


# Process-local singleton. Import this from scanner.py.
BREAKER = BedrockTier2Breaker()
