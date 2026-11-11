"""Bounds-derivation tests for ``admit/quota.py`` (R2-07 / GW19), task 5.2.

Covers the pure :func:`gateway_v2.admit.quota.derive_bounds` only (no I/O, no event loop):

* each queue's depth equals the mapped :class:`ResourceContract` method's output for a given
  injected ``q_safe`` (Req 3.3, 4.1, 7.5);
* ``q_safe`` unset (``None``) or non-positive ⇒ :class:`CapacityUnset` refuse-to-start (Req 4.5);
* an uncomputable bound ⇒ :class:`CapacityUnavailable` (Req 13.3), driven by pushing the contract
  below its minimum-to-serve regime;
* ``q_safe`` arrives by injection as a keyword argument, never a hardcoded literal (Req 4.2/4.3).

House idiom: a seeded ``random.Random`` loop of >= 10,000 iterations with the seed logged in the
assertion message asserts the per-queue mapping holds across a wide ``q_safe`` sweep. Test files are
not under the import-linter layer contract.
"""

from __future__ import annotations

import random

import pytest

from gateway_v2.admit.quota import AdmissionBounds, derive_bounds
from gateway_v2.runtime.errors import CapacityUnavailable, CapacityUnset
from gateway_v2.runtime.resources import ResourceContract

_ITERATIONS = 10_000

# A deployment-realistic audit calibration (mirrors tests/audit/test_lgw14c_sink.py).
_AUDIT_DRAIN_RATE = 100_000.0
_AUDIT_BYTES_PER_RECORD = 3_000


def _contract(
    *,
    memory_limit: int = 2 * 1024 * 1024 * 1024,
    target_p99_ms: float = 20.0,
    utilization_cap: float = 0.75,
    per_worker_rss: int = 400 * 1024 * 1024,
) -> ResourceContract:
    """A valid contract fixture with known capacity signals (serve-able by default)."""
    return ResourceContract(
        cpu_quota=4.0,
        memory_limit=memory_limit,
        fd_limit=65_536,
        guard_capacity=None,
        target_p99_ms=target_p99_ms,
        utilization_cap=utilization_cap,
        per_worker_rss=per_worker_rss,
        worker_override=None,
        cpu_source="test",
        mem_source="test",
        fd_source="test",
    )


# --------------------------------------------------------------------------- #
# Each queue → contract-method mapping (Req 3.3, 4.1, 7.5)
# --------------------------------------------------------------------------- #


def test_each_queue_depth_matches_its_mapped_contract_method() -> None:
    """request/guard/dispatch/egress → queue_depth(q_safe); audit → audit_queue_depth."""
    contract = _contract()
    q_safe = 2_000.0

    bounds = derive_bounds(
        contract,
        q_safe=q_safe,
        audit_drain_rate_per_s=_AUDIT_DRAIN_RATE,
        audit_bytes_per_record=_AUDIT_BYTES_PER_RECORD,
    )

    slot_depth = contract.queue_depth(q_safe)
    audit_depth = contract.audit_queue_depth(_AUDIT_DRAIN_RATE, _AUDIT_BYTES_PER_RECORD)

    assert bounds.concurrency == slot_depth
    assert bounds.request_depth == slot_depth
    assert bounds.guard_depth == slot_depth
    assert bounds.dispatch_depth == slot_depth
    assert bounds.egress_depth == slot_depth
    assert bounds.audit_depth == audit_depth
    # audit is deliberately NOT queue_depth(q_safe) at this calibration (Req 7.5).
    assert bounds.audit_depth != slot_depth


def test_audit_depth_is_not_queue_depth() -> None:
    """Req 7.5: audit uses audit_queue_depth, never the per-worker-RSS queue_depth."""
    contract = _contract()
    q_safe = 1_500.0

    bounds = derive_bounds(
        contract,
        q_safe=q_safe,
        audit_drain_rate_per_s=_AUDIT_DRAIN_RATE,
        audit_bytes_per_record=_AUDIT_BYTES_PER_RECORD,
    )

    assert bounds.audit_depth == contract.audit_queue_depth(
        _AUDIT_DRAIN_RATE, _AUDIT_BYTES_PER_RECORD
    )
    assert bounds.audit_depth != contract.queue_depth(q_safe)


def test_mapping_holds_across_a_q_safe_sweep() -> None:
    """The per-queue mapping holds for every injected q_safe (seeded, >= 10,000 iters)."""
    seed = 0x19_05_02
    rng = random.Random(seed)
    contract = _contract()
    msg = f"seed={seed:#x}"

    for _ in range(_ITERATIONS):
        q_safe = rng.uniform(1e-6, 50_000.0)
        bounds = derive_bounds(
            contract,
            q_safe=q_safe,
            audit_drain_rate_per_s=_AUDIT_DRAIN_RATE,
            audit_bytes_per_record=_AUDIT_BYTES_PER_RECORD,
        )
        slot_depth = contract.queue_depth(q_safe)
        assert bounds.concurrency == slot_depth, msg
        assert bounds.request_depth == slot_depth, msg
        assert bounds.guard_depth == slot_depth, msg
        assert bounds.dispatch_depth == slot_depth, msg
        assert bounds.egress_depth == slot_depth, msg
        assert bounds.audit_depth == contract.audit_queue_depth(
            _AUDIT_DRAIN_RATE, _AUDIT_BYTES_PER_RECORD
        ), msg


def test_derive_bounds_returns_a_frozen_admissionbounds() -> None:
    """The return type is the frozen, slotted value object (Req 7)."""
    contract = _contract()
    bounds = derive_bounds(
        contract,
        q_safe=1_000.0,
        audit_drain_rate_per_s=_AUDIT_DRAIN_RATE,
        audit_bytes_per_record=_AUDIT_BYTES_PER_RECORD,
    )
    assert isinstance(bounds, AdmissionBounds)
    with pytest.raises((AttributeError, TypeError)):
        bounds.concurrency = 1  # type: ignore[misc]


# --------------------------------------------------------------------------- #
# Refuse-to-start when q_safe is unset or non-positive (Req 4.5, 13 start)
# --------------------------------------------------------------------------- #


def test_q_safe_none_refuses_to_start() -> None:
    """q_safe unset ⇒ CapacityUnset: a missing measurement is not guessed (Req 4.5)."""
    contract = _contract()
    with pytest.raises(CapacityUnset, match="q_safe is unset"):
        derive_bounds(
            contract,
            q_safe=None,
            audit_drain_rate_per_s=_AUDIT_DRAIN_RATE,
            audit_bytes_per_record=_AUDIT_BYTES_PER_RECORD,
        )


@pytest.mark.parametrize("bad", [0.0, -1.0, -1e-9, -50_000.0])
def test_q_safe_non_positive_refuses_to_start(bad: float) -> None:
    """q_safe <= 0 ⇒ CapacityUnset refuse-to-start (Req 4.5)."""
    contract = _contract()
    with pytest.raises(CapacityUnset, match="q_safe is unset"):
        derive_bounds(
            contract,
            q_safe=bad,
            audit_drain_rate_per_s=_AUDIT_DRAIN_RATE,
            audit_bytes_per_record=_AUDIT_BYTES_PER_RECORD,
        )


def test_q_safe_unset_is_checked_before_any_contract_call() -> None:
    """The refuse-to-start guard fires even when the contract itself cannot serve."""
    # memory_limit tiny → every contract bound would raise CapacityUnavailable; the q_safe guard
    # must win first (CapacityUnset), proving the refuse-to-start check precedes derivation.
    unservable = _contract(memory_limit=1)
    with pytest.raises(CapacityUnset):
        derive_bounds(
            unservable,
            q_safe=None,
            audit_drain_rate_per_s=_AUDIT_DRAIN_RATE,
            audit_bytes_per_record=_AUDIT_BYTES_PER_RECORD,
        )


# --------------------------------------------------------------------------- #
# Uncomputable bound ⇒ CapacityUnavailable (Req 13.3)
# --------------------------------------------------------------------------- #


def test_below_minimum_slot_bound_propagates_capacity_unavailable() -> None:
    """A below-minimum queue_depth bound propagates CapacityUnavailable (Req 13.3).

    memory_limit below one per_worker_rss slot drives queue_depth below the minimum to serve.
    """
    per_worker = 400 * 1024 * 1024
    unservable = _contract(memory_limit=per_worker, per_worker_rss=per_worker)
    # ram_slots = floor(memory_limit * 0.75 / per_worker_rss) = floor(0.75) = 0 → below minimum.
    with pytest.raises(CapacityUnavailable):
        derive_bounds(
            unservable,
            q_safe=1_000.0,
            audit_drain_rate_per_s=_AUDIT_DRAIN_RATE,
            audit_bytes_per_record=_AUDIT_BYTES_PER_RECORD,
        )


def test_below_minimum_audit_bound_propagates_capacity_unavailable() -> None:
    """An uncomputable audit bound propagates CapacityUnavailable (Req 13.3).

    A record larger than the whole audit memory share makes by_memory < 1 → below minimum.
    """
    contract = _contract(memory_limit=64 * 1024 * 1024)
    # audit memory share = 64MiB * 0.75 * 0.02 ≈ 1.0MiB; a 4MiB record cannot fit even one.
    with pytest.raises(CapacityUnavailable):
        derive_bounds(
            contract,
            q_safe=1_000.0,
            audit_drain_rate_per_s=_AUDIT_DRAIN_RATE,
            audit_bytes_per_record=4 * 1024 * 1024,
        )


def test_non_positive_audit_drain_rate_propagates_capacity_unavailable() -> None:
    """A non-positive audit drain rate is an uncomputable bound (Req 13.3)."""
    contract = _contract()
    with pytest.raises(CapacityUnavailable):
        derive_bounds(
            contract,
            q_safe=1_000.0,
            audit_drain_rate_per_s=0.0,
            audit_bytes_per_record=_AUDIT_BYTES_PER_RECORD,
        )
