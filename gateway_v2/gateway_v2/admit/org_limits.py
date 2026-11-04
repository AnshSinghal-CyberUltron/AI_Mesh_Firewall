"""GW27: per-org request rate, burst and token (TPM) limits, enforced locally per worker.

The decision is a pure function of the injected clock and the org's published ``OrgControls``:
no store I/O, no ``await``, no HTTP object. A refusal is the existing ``BudgetVerdict`` with
``posture.RATE_LIMITED`` (``edge`` renders the 429). Scope is **per gateway replica**: each worker
holds ``1/workers()`` of the rate and a spread-adjusted share of the burst, and every replica holds
its own copy, so the fleet-wide ceiling is up to R times the configured value (design D-4).

State is per instance. The table never evicts a live org: a controlled org arriving at a full
table is refused (fail-closed 429) and counted, so a limiter can never be reset by pressure. A
republish carries the outstanding debt forward (``_Bucket.retarget``) and can only clamp it.
"""
from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from gateway_v2.admit.grant import BudgetVerdict, rate_limited_verdict
from gateway_v2.domain.plan import OrgControls
from gateway_v2.runtime.resources import ResourceContract
from gateway_v2.runtime.resources_org import OrgBounds, derive_org_bounds

__all__ = ("OrgLimitParams", "OrgLimits", "OrgLimitsMetrics", "BucketShape")

LOG = logging.getLogger(__name__)

# Absorbs float accumulation error when k * T is compared with burst * T (seconds).
_EPSILON_S = 1e-9


class OrgLimitsMetrics(Protocol):
    """The two label-free hooks ``AdmissionMetrics`` provides."""

    def observe_org_limits_table_full(self) -> None: ...

    def set_org_slots_used(self, used: int) -> None: ...


@dataclass(frozen=True, slots=True)
class BucketShape:
    """GCRA emission interval ``T`` and tolerance ``tau``, in seconds."""

    interval_s: float
    tolerance_s: float


@dataclass(frozen=True, slots=True)
class OrgLimitParams:
    """What an org's controls ask of this worker; ``None`` means that bucket does not exist."""

    request: BucketShape | None
    tokens: BucketShape | None


def _controlled(controls: OrgControls) -> bool:
    """The one predicate: fast path, slot allocation and bucket construction all read it."""
    rate = controls.rate
    return (rate is not None and rate.enabled) or controls.org_tpm_limit > 0


def _params(bounds: OrgBounds, controls: OrgControls) -> OrgLimitParams:
    rate = controls.rate
    request: BucketShape | None = None
    if rate is not None and rate.enabled:
        interval = bounds.request_interval_s(rate.requests_per_minute)
        request = BucketShape(interval, bounds.burst_per_worker(rate.burst) * interval)
    tokens: BucketShape | None = None
    if controls.org_tpm_limit > 0:
        tokens = BucketShape(
            bounds.token_interval_s(controls.org_tpm_limit),
            bounds.token_tolerance_s(controls.org_tpm_limit),
        )
    return OrgLimitParams(request, tokens)


class _Bucket:
    """One GCRA carrier. The TAT advances only on admit."""

    __slots__ = ("interval", "tat", "tau")

    def __init__(self, shape: BucketShape, now: float) -> None:
        self.interval = shape.interval_s
        self.tau = shape.tolerance_s
        self.tat = now

    def take(self, now: float, units: int) -> bool:
        proposed = max(self.tat, now) + units * self.interval
        if proposed <= now + self.tau + _EPSILON_S:
            self.tat = proposed
            return True
        return False

    def retarget(self, shape: BucketShape, now: float) -> None:
        """Adopt new parameters keeping the outstanding debt, clamped to the new tolerance."""
        debt = min(max(self.tat - now, 0.0), shape.tolerance_s)
        self.interval = shape.interval_s
        self.tau = shape.tolerance_s
        self.tat = now + debt

    def refund(self, now: float, units: int) -> None:
        self.tat = max(now, self.tat - units * self.interval)


class _OrgState:
    __slots__ = ("controls", "request", "tokens")

    def __init__(self, controls: OrgControls) -> None:
        self.controls = controls
        self.request: _Bucket | None = None
        self.tokens: _Bucket | None = None


class OrgLimits:
    """Per-instance org limit table. One per worker."""

    def __init__(
        self,
        contract: ResourceContract,
        *,
        clock: Callable[[], float] = time.monotonic,
        metrics: OrgLimitsMetrics | None = None,
    ) -> None:
        self._bounds = derive_org_bounds(contract)  # unusable contract = boot failure
        self._clock = clock
        self._metrics = metrics
        self._orgs: dict[str, _OrgState] = {}
        self._last_full_log = float("-inf")

    @property
    def slots_used(self) -> int:
        return len(self._orgs)

    def check(
        self, org: str, controls: OrgControls, cost: int, *, request_id: str
    ) -> BudgetVerdict | None:
        """``None`` admits; a verdict refuses (429). Any internal fault refuses."""
        if not _controlled(controls):
            if org in self._orgs:
                self.invalidate(org)
            return None
        try:
            if self._admit(org, controls, max(cost, 1)):
                return None
        except Exception:
            LOG.exception("org_limits_check_failed request_id=%s: refusing", request_id)
        return rate_limited_verdict(request_id)

    def refund_tokens(self, org: str, cost: int) -> None:
        """Give back an estimate charged for a request that never reached the provider."""
        try:
            state = self._orgs.get(org)
            if state is not None and state.tokens is not None:
                state.tokens.refund(self._clock(), max(cost, 1))
        except Exception:
            LOG.exception("org_limits_refund_failed")

    def invalidate(self, org: str) -> None:
        """Offboarding, or controls turned off: free the slot."""
        self._orgs.pop(org, None)
        self._publish_slots()

    def _admit(self, org: str, controls: OrgControls, cost: int) -> bool:
        now = self._clock()
        state = self._orgs.get(org)
        if state is None:
            if len(self._orgs) >= self._bounds.slots:
                self._note_table_full(now)
                return False
            state = _OrgState(controls)
            self._adopt(state, controls, now)
            self._orgs[org] = state
            self._publish_slots()
        elif state.controls != controls:
            self._adopt(state, controls, now)
        if state.request is not None and not state.request.take(now, 1):
            return False
        return state.tokens is None or state.tokens.take(now, cost)

    def _adopt(self, state: _OrgState, controls: OrgControls, now: float) -> None:
        params = _params(self._bounds, controls)
        state.request = self._rebuild(state.request, params.request, now)
        state.tokens = self._rebuild(state.tokens, params.tokens, now)
        state.controls = controls

    @staticmethod
    def _rebuild(bucket: _Bucket | None, shape: BucketShape | None, now: float) -> _Bucket | None:
        if shape is None:
            return None
        if bucket is None:
            return _Bucket(shape, now)
        bucket.retarget(shape, now)
        return bucket

    def _note_table_full(self, now: float) -> None:
        if self._metrics is not None:
            self._metrics.observe_org_limits_table_full()
        if now - self._last_full_log >= self._bounds.tpm_window_s:
            self._last_full_log = now
            LOG.error(
                "org_limits_table_full slots=%d: refusing controlled orgs (429); raise the "
                "metrics directory capacity or invalidate offboarded orgs",
                self._bounds.slots,
            )

    def _publish_slots(self) -> None:
        if self._metrics is not None:
            self._metrics.set_org_slots_used(len(self._orgs))
