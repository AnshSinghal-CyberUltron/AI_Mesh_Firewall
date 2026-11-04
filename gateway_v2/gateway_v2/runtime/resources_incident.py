"""GW29 incident-response capacity accessors, split out of `runtime/resources.py`.

`ResourceContract` mixes `IncidentBoundsMixin` in (GW30 `VectorBoundsMixin` precedent) so
`runtime/resources.py` stays under its 800-line size gate. The LITERALS (`_RISK_WINDOW_S`,
`_RISK_THRESHOLD`, ...) stay in `runtime/resources.py`, the one module `check_capacity_literals`
exempts; this module holds only name references and is read through function-local imports to
break the otherwise-circular `resources` <-> `resources_incident` import.

Every accessor fails closed: `CapacityUnavailable` on a non-positive or non-finite derived bound,
never a guessed default. The working numbers are OWNER-TO-SIGN (design D8); the names are final.
"""

from __future__ import annotations

import math
from types import MappingProxyType

from gateway_v2.domain.incident import ModelOutcome
from gateway_v2.runtime.errors import CapacityUnavailable


def check_incident_ordering() -> None:
    """Refuse to start unless the risk constants are ordered (design 8, `_check_incident_ordering`).

    `0 < threshold < 1`, `probation_min_samples <= min_samples`, `bucket_s * buckets == window_s`,
    `auto_isolation_duration_s >= risk_window_s`, and every weight in `[0, 1]`.
    """
    from gateway_v2.runtime.resources import (
        _AUTO_ISOLATE_WINDOWS,
        _RISK_BUCKETS,
        _RISK_MIN_SAMPLES,
        _RISK_PROBATION_MIN,
        _RISK_THRESHOLD,
        _RISK_WEIGHT_HARD,
        _RISK_WEIGHT_SOFT,
        _RISK_WINDOW_S,
    )

    if not 0.0 < _RISK_THRESHOLD < 1.0:
        raise CapacityUnavailable(f"risk threshold {_RISK_THRESHOLD} outside (0, 1)")
    if not 1 <= _RISK_PROBATION_MIN <= _RISK_MIN_SAMPLES:
        raise CapacityUnavailable("risk probation samples must be in [1, min samples]")
    if _RISK_BUCKETS < 1 or not math.isclose(
        (_RISK_WINDOW_S / _RISK_BUCKETS) * _RISK_BUCKETS, _RISK_WINDOW_S,
    ):
        raise CapacityUnavailable("risk bucket width x buckets must equal the risk window")
    if _AUTO_ISOLATE_WINDOWS < 1:
        raise CapacityUnavailable("auto-isolation duration must cover at least one risk window")
    if not all(0.0 <= w <= 1.0 for w in (_RISK_WEIGHT_HARD, _RISK_WEIGHT_SOFT)):
        raise CapacityUnavailable("risk weights must be in [0, 1]")


class IncidentBoundsMixin:
    """GW29 risk, isolation, revocation, event-queue and retry bounds for `ResourceContract`.

    A behaviour-only mixin: no field, no `__slots__`. The host contract supplies the
    annotation-only members below.
    """

    target_p99_ms: float

    def workers(self) -> int:  # pragma: no cover - supplied by ResourceContract
        raise NotImplementedError

    def connection_budget(self) -> int:  # pragma: no cover - supplied by ResourceContract
        raise NotImplementedError

    def metrics_directory_slots(self) -> int:  # pragma: no cover - supplied by ResourceContract
        raise NotImplementedError

    def metrics_window_s(self) -> float:  # pragma: no cover - supplied by ResourceContract
        raise NotImplementedError

    def event_log_rate(self) -> int:  # pragma: no cover - supplied by ResourceContract
        raise NotImplementedError

    def risk_window_s(self) -> float:
        """Sliding risk window in seconds (the declared auto-isolation detection window)."""
        from gateway_v2.runtime.resources import _RISK_WINDOW_S

        return _positive(_RISK_WINDOW_S, "risk window")

    def risk_buckets(self) -> int:
        from gateway_v2.runtime.resources import _RISK_BUCKETS

        return _at_least_one(_RISK_BUCKETS, "risk buckets")

    def risk_bucket_s(self) -> float:
        """Bucket width: `risk_window_s() / risk_buckets()` (5 s = the console gauge cadence)."""
        return self.risk_window_s() / self.risk_buckets()

    def risk_min_samples(self) -> int:
        from gateway_v2.runtime.resources import _RISK_MIN_SAMPLES

        return _at_least_one(_RISK_MIN_SAMPLES, "risk min samples")

    def risk_probation_min_samples(self) -> int:
        from gateway_v2.runtime.resources import _RISK_PROBATION_MIN

        return _at_least_one(_RISK_PROBATION_MIN, "risk probation samples")

    def risk_threshold(self) -> float:
        from gateway_v2.runtime.resources import _RISK_THRESHOLD

        if not 0.0 < _RISK_THRESHOLD < 1.0:
            raise CapacityUnavailable("risk threshold outside (0, 1)")
        return _RISK_THRESHOLD

    def risk_weights(self) -> MappingProxyType[ModelOutcome, float]:
        """Per-outcome weight in `[0, 1]`: hard failures 1.0, 429 and guard hits lower, OK 0."""
        from gateway_v2.runtime.resources import (
            _RISK_WEIGHT_HARD,
            _RISK_WEIGHT_NONE,
            _RISK_WEIGHT_SOFT,
        )

        return MappingProxyType({
            ModelOutcome.OK: _RISK_WEIGHT_NONE,
            ModelOutcome.TIMEOUT: _RISK_WEIGHT_HARD,
            ModelOutcome.SERVER_ERROR: _RISK_WEIGHT_HARD,
            ModelOutcome.MALFORMED: _RISK_WEIGHT_HARD,
            ModelOutcome.RATE_LIMITED: _RISK_WEIGHT_SOFT,
            ModelOutcome.GUARD_HIT: _RISK_WEIGHT_SOFT,
        })

    def risk_max_models(self) -> int:
        """Tracker bound: `metrics_directory_slots() // _RISK_MODELS_DIV`."""
        from gateway_v2.runtime.resources import _RISK_MODELS_DIV

        return _at_least_one(self.metrics_directory_slots() // _RISK_MODELS_DIV, "risk max models")

    def auto_isolation_duration_s(self) -> float:
        """Default `expires_at` span: `risk_window_s() x _AUTO_ISOLATE_WINDOWS`."""
        from gateway_v2.runtime.resources import _AUTO_ISOLATE_WINDOWS

        return self.risk_window_s() * _at_least_one(_AUTO_ISOLATE_WINDOWS, "auto-isolation windows")

    def max_isolation_hold_s(self) -> float:
        """Local monotonic cap on an auto isolation: duration x `_HOLD_CAP_MULT`."""
        from gateway_v2.runtime.resources import _HOLD_CAP_MULT

        return self.auto_isolation_duration_s() * _at_least_one(_HOLD_CAP_MULT, "hold cap")

    def risk_min_distinct_orgs(self) -> int:
        from gateway_v2.runtime.resources import _RISK_MIN_ORGS

        return _at_least_one(_RISK_MIN_ORGS, "risk min distinct orgs")

    def local_isolation_slots(self) -> int:
        return self.risk_max_models()

    def revocation_ledger_slots(self) -> int:
        """`connection_budget() x _REVOKE_SLOTS_MULT`."""
        from gateway_v2.runtime.resources import _REVOKE_SLOTS_MULT

        return _at_least_one(self.connection_budget() * _REVOKE_SLOTS_MULT, "revocation slots")

    def incident_event_queue_slots(self) -> int:
        """Pump queue depth: `event_log_rate() x _EVENT_QUEUE_SLOTS_MULT`."""
        from gateway_v2.runtime.resources import _EVENT_QUEUE_SLOTS_MULT

        return _at_least_one(self.event_log_rate() * _EVENT_QUEUE_SLOTS_MULT, "event queue slots")

    def incident_event_window_s(self) -> float:
        return _positive(self.metrics_window_s(), "incident event window")

    def notifier_timeout_s(self) -> float:
        """`max(p99_s x _NOTIFIER_TIMEOUT_MULT, p99_s)`: the pump's per-call bound."""
        from gateway_v2.runtime.resources import _MS_PER_S, _NOTIFIER_TIMEOUT_MULT

        p99_s = _positive(self.target_p99_ms / _MS_PER_S, "notifier timeout")
        return max(p99_s * _NOTIFIER_TIMEOUT_MULT, p99_s)

    def incident_retry_jitter_s(self) -> float:
        """`MIN_RETRY_AFTER_S x _RETRY_JITTER_FRACTION`: the jitter added to Retry-After."""
        from gateway_v2.domain.posture import MIN_RETRY_AFTER_S
        from gateway_v2.runtime.resources import _RETRY_JITTER_FRACTION

        return _positive(MIN_RETRY_AFTER_S * _RETRY_JITTER_FRACTION, "retry jitter")

    def incident_snapshot(self) -> dict[str, object]:
        """The GW29 keys for `resources.snapshot()`."""
        return {
            "risk_window_s": self.risk_window_s(),
            "risk_bucket_s": self.risk_bucket_s(),
            "risk_buckets": self.risk_buckets(),
            "risk_min_samples": self.risk_min_samples(),
            "risk_probation_min_samples": self.risk_probation_min_samples(),
            "risk_threshold": self.risk_threshold(),
            "risk_max_models": self.risk_max_models(),
            "risk_min_distinct_orgs": self.risk_min_distinct_orgs(),
            "auto_isolation_duration_s": self.auto_isolation_duration_s(),
            "max_isolation_hold_s": self.max_isolation_hold_s(),
            "local_isolation_slots": self.local_isolation_slots(),
            "revocation_ledger_slots": self.revocation_ledger_slots(),
            "incident_event_queue_slots": self.incident_event_queue_slots(),
            "incident_event_window_s": self.incident_event_window_s(),
            "notifier_timeout_s": self.notifier_timeout_s(),
            "incident_retry_jitter_s": self.incident_retry_jitter_s(),
        }


def _positive(value: float, what: str) -> float:
    if math.isnan(value) or math.isinf(value) or value <= 0:
        raise CapacityUnavailable(f"{what} below minimum to serve")
    return value


def _at_least_one(value: int, what: str) -> int:
    if value < 1:
        raise CapacityUnavailable(f"{what} below minimum to serve")
    return value
