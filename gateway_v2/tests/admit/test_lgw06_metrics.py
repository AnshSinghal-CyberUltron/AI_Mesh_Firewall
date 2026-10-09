"""Budget-lease quota producer tests (R2-09 / GW06), task 10.1.

Covers the ``QuotaMetrics`` producer added to ``gateway_v2.admit.metrics`` (producer side only; the
GW14d publisher is separate). It mirrors the ``AdmissionMetrics`` producer discipline:

* **Three fixed, label-free series present + zero from the FIRST snapshot** (R12.1, R12.3, R12.4):
  ``amf_quota_lease_overshoot`` (declared aggregate overshoot, Req 7.4),
  ``amf_quota_async_refill_total`` and ``amf_quota_budget_unavailable_total`` all read ``0`` before
  any observation — "no refills / no refusals / overshoot 0" is distinct from "series absent".
* **The two event hooks increment the right counters** (R12.4): ``observe_async_refill`` bumps the
  refill count and nothing else; ``observe_budget_unavailable`` bumps the refusal count and nothing
  else.
* **The overshoot gauge reports what it was set to** (R7.4): seeded ``0``; ``set_overshoot(n)``
  reports ``n``; the latest value wins; a negative value is rejected.
* **No tenant-derived label on any series** (R2-10 / R12.2): structural — no producer method
  exposes an ``owner`` / ``org`` / ``tenant`` parameter, so no per-tenant series can be created.
* **The reading is frozen** and does not drift under later observations.
* **Structural Protocol satisfaction**: a ``QuotaMetrics`` instance satisfies
  ``lease.QuotaMetricsLike`` (``observe_async_refill``) and the façade's duck-typed
  ``observe_budget_unavailable`` hook that ``quota.QuotaComponent._refuse`` calls.

Validates: Requirements 7.4, 12.1, 12.2, 12.3, 12.4

Test files are not under the import-linter layer contract.
"""

from __future__ import annotations

import inspect

from gateway_v2.admit import metrics as metrics_module
from gateway_v2.admit.lease import QuotaMetricsLike
from gateway_v2.admit.metrics import (
    QUOTA_PREFIX,
    QuotaMetrics,
    QuotaReading,
)

_TENANT_PARAMS = {"owner", "owner_id", "org", "org_id", "tenant", "tenant_id"}


# --------------------------------------------------------------------------- #
# The three fixed series exist and are zero from the first snapshot
# Validates: Requirements 12.1, 12.3, 12.4
# --------------------------------------------------------------------------- #


def test_quota_prefix_is_fixed() -> None:
    """The quota series prefix is ``amf_quota`` (fixed, label-free family)."""
    assert QUOTA_PREFIX == "amf_quota"


def test_three_series_present_and_zero_from_first_snapshot() -> None:
    """All three series read a real ``0`` before any observation (R12.3)."""
    reading = QuotaMetrics().snapshot()
    assert reading.lease_overshoot == 0
    assert reading.async_refill_total == 0
    assert reading.budget_unavailable_total == 0


def test_reading_carries_exactly_the_three_fixed_series() -> None:
    """``QuotaReading`` exposes exactly the three declared fields (Req 12.4)."""
    fields = set(QuotaReading.__dataclass_fields__)
    assert fields == {"lease_overshoot", "async_refill_total", "budget_unavailable_total"}


# --------------------------------------------------------------------------- #
# The two event hooks increment the right counters
# Validates: Requirement 12.4
# --------------------------------------------------------------------------- #


def test_observe_async_refill_increments_only_refill_total() -> None:
    """``observe_async_refill`` bumps the refill count and nothing else."""
    producer = QuotaMetrics()
    producer.observe_async_refill()
    producer.observe_async_refill()
    producer.observe_async_refill()
    reading = producer.snapshot()
    assert reading.async_refill_total == 3
    assert reading.budget_unavailable_total == 0
    assert reading.lease_overshoot == 0


def test_observe_budget_unavailable_increments_only_refusal_total() -> None:
    """``observe_budget_unavailable`` bumps the refusal count and nothing else."""
    producer = QuotaMetrics()
    producer.observe_budget_unavailable()
    producer.observe_budget_unavailable()
    reading = producer.snapshot()
    assert reading.budget_unavailable_total == 2
    assert reading.async_refill_total == 0
    assert reading.lease_overshoot == 0


def test_both_counters_are_independent() -> None:
    """The two counters accumulate independently across interleaved observations."""
    producer = QuotaMetrics()
    producer.observe_async_refill()
    producer.observe_budget_unavailable()
    producer.observe_async_refill()
    producer.observe_budget_unavailable()
    producer.observe_budget_unavailable()
    reading = producer.snapshot()
    assert reading.async_refill_total == 2
    assert reading.budget_unavailable_total == 3


# --------------------------------------------------------------------------- #
# The overshoot gauge reports what it was set to (seeded 0)
# Validates: Requirement 7.4
# --------------------------------------------------------------------------- #


def test_overshoot_seeded_zero_and_reports_set_value() -> None:
    """The overshoot gauge is seeded ``0`` and reports what ``set_overshoot`` was told (Req 7.4)."""
    producer = QuotaMetrics()
    assert producer.snapshot().lease_overshoot == 0
    producer.set_overshoot(12)
    assert producer.snapshot().lease_overshoot == 12


def test_overshoot_is_a_gauge_latest_wins() -> None:
    """The overshoot is a gauge, not a counter: the latest ``set_overshoot`` wins."""
    producer = QuotaMetrics()
    producer.set_overshoot(5)
    producer.set_overshoot(9)
    assert producer.snapshot().lease_overshoot == 9


def test_overshoot_rejects_negative() -> None:
    """A negative overshoot is rejected — it is a non-negative declared count."""
    producer = QuotaMetrics()
    try:
        producer.set_overshoot(-1)
    except ValueError:
        pass
    else:
        raise AssertionError("set_overshoot must reject a negative overshoot")


# --------------------------------------------------------------------------- #
# No tenant-derived label on any series (structural)
# Validates: Requirements 12.2
# --------------------------------------------------------------------------- #


def test_no_producer_method_accepts_a_tenant_label() -> None:
    """No producer method takes an owner / org / tenant parameter (R2-10 / R12.2)."""
    for name in ("set_overshoot", "observe_async_refill", "observe_budget_unavailable", "snapshot"):
        params = set(inspect.signature(getattr(QuotaMetrics, name)).parameters)
        assert not (params & _TENANT_PARAMS), (
            f"{name} must not accept a tenant-derived label, got {params}"
        )


def test_reading_fields_carry_no_tenant_dimension() -> None:
    """The reading's series are scalars — no per-tenant mapping dimension exists (R12.2)."""
    reading = QuotaMetrics().snapshot()
    assert isinstance(reading.lease_overshoot, int)
    assert isinstance(reading.async_refill_total, int)
    assert isinstance(reading.budget_unavailable_total, int)


# --------------------------------------------------------------------------- #
# The reading is frozen and does not drift
# --------------------------------------------------------------------------- #


def test_reading_is_frozen() -> None:
    """``QuotaReading`` is frozen; its fields cannot be reassigned."""
    reading = QuotaMetrics().snapshot()
    try:
        reading.async_refill_total = 99  # type: ignore[misc]
    except AttributeError:
        pass
    else:
        raise AssertionError("QuotaReading must be frozen")


def test_snapshot_does_not_drift_under_later_observations() -> None:
    """A snapshot is a stable copy by value: later observations do not mutate an earlier reading."""
    producer = QuotaMetrics()
    producer.observe_async_refill()
    producer.set_overshoot(4)
    first = producer.snapshot()
    producer.observe_async_refill()
    producer.observe_budget_unavailable()
    producer.set_overshoot(8)
    assert first.async_refill_total == 1
    assert first.budget_unavailable_total == 0
    assert first.lease_overshoot == 4


# --------------------------------------------------------------------------- #
# Structural: QuotaMetrics satisfies the two Protocols the written code calls
# --------------------------------------------------------------------------- #


def test_satisfies_lease_quota_metrics_like() -> None:
    """A ``QuotaMetrics`` structurally satisfies ``lease.QuotaMetricsLike`` (Async_Refill hook).

    ``QuotaMetricsLike`` is not ``@runtime_checkable``, so the structural match is proved by method
    presence + a compatible (self-only) signature — exactly what ``lease._refill`` duck-types
    against when it calls ``self._metrics.observe_async_refill()``.
    """
    producer = QuotaMetrics()
    # The one method the Protocol declares — present on QuotaMetrics and declared on the Protocol.
    assert "observe_async_refill" in dir(QuotaMetricsLike)
    assert callable(getattr(producer, "observe_async_refill", None)), (
        "QuotaMetrics is missing the lease.QuotaMetricsLike hook observe_async_refill"
    )
    # The hook lease._refill calls takes no extra parameters (self-only), matching the Protocol.
    sig = inspect.signature(QuotaMetrics.observe_async_refill)
    assert list(sig.parameters) == ["self"]


def test_satisfies_facade_budget_unavailable_hook() -> None:
    """A ``QuotaMetrics`` exposes the ``observe_budget_unavailable`` hook ``quota._refuse`` uses."""
    producer = QuotaMetrics()
    observe = getattr(producer, "observe_budget_unavailable", None)
    assert callable(observe)
    observe()
    assert producer.snapshot().budget_unavailable_total == 1


def test_quota_types_are_the_public_surface() -> None:
    """``QuotaMetrics`` and ``QuotaReading`` are exported by the admit metrics module."""
    assert QuotaMetrics.__module__ == metrics_module.__name__
    assert QuotaReading.__module__ == metrics_module.__name__
    assert "QuotaMetrics" in metrics_module.__all__
    assert "QuotaReading" in metrics_module.__all__
