"""Admission bounds derivation (R2-07 / R2-08, card GW19).

The card reserves ``admit/quota.py`` for "Local GCRA + shared lease"; the admission-control design
narrows its responsibility to **bounds derivation**. This module answers one question: given the
signed :class:`~gateway_v2.runtime.resources.ResourceContract` and the injected, measured guard
rate ``q_safe`` (from GW20, *not* the deployment-derived ``offered_service_rate()``), what is the
declared maximum depth of each :class:`Bounded_Queue` and the concurrency cap?

Every number here is a :class:`ResourceContract` call — the contract is the only module permitted
to hold a capacity-position literal, enforced by the AST capacity gate. ``quota.py`` holds **no**
numeric capacity literal: an uncomputable bound simply propagates the contract's
:class:`CapacityUnavailable` (refuse-to-admit / refuse-to-start, Req 13.3), and an unset ``q_safe``
raises :class:`CapacityUnset` before anything else runs (refuse-to-start, Req 4.5).

The queue → contract-method mapping (design "Bounds derivation", Req 7.5):

======== =================================================================================
Queue    Bound source
======== =================================================================================
request  ``contract.queue_depth(q_safe)`` — the measured guard rate is the service rate
guard    ``contract.queue_depth(q_safe)`` — guard-owner share, same rule as every owner (1.7)
dispatch ``contract.queue_depth(q_safe)``
egress   ``contract.queue_depth(q_safe)`` for *slot* admission; the memory byte bound comes
         from ``contract.stream_buffer_bytes(...)`` and is owned by backpressure (task 12)
audit    ``contract.audit_queue_depth(drain_rate, bytes_per_record)`` — deliberately NOT
         ``queue_depth`` (an audit record is a few KB; sizing it by per-worker RSS would drop
         audit under any load — see the contract docstring), Req 7.5
======== =================================================================================

The module is pure, has no module-level mutable state, and depends only on ``runtime`` — within the
``admit`` layer's import-linter contract.
"""

from __future__ import annotations

from dataclasses import dataclass

from gateway_v2.runtime.errors import CapacityUnset
from gateway_v2.runtime.resources import ResourceContract

__all__ = (
    "AdmissionBounds",
    "derive_bounds",
)


@dataclass(frozen=True, slots=True)
class AdmissionBounds:
    """Declared maximum depths for admission, each derived from the ResourceContract.

    ``concurrency`` is the in-flight admission cap (Req 3.3, 4.1); the ``*_depth`` fields are the
    declared maximum depth of each :class:`Bounded_Queue` (Req 7.1, 7.5). ``egress_depth`` is the
    *slot* admission depth only — the streaming memory byte bound lives in the backpressure path and
    is not stored here.
    """

    concurrency: int
    request_depth: int
    guard_depth: int
    dispatch_depth: int
    egress_depth: int
    audit_depth: int


def derive_bounds(
    contract: ResourceContract,
    *,
    q_safe: float | None,
    audit_drain_rate_per_s: float,
    audit_bytes_per_record: int,
) -> AdmissionBounds:
    """Derive every admission bound from ``contract`` and the injected ``q_safe``.

    ``q_safe`` is the measured guard service rate (GW20), injected at construction and never
    hardcoded. If it is unset or non-positive the component refuses to start (Req 4.5, 13 start):
    a missing measurement is explicitly not guessed. Each depth is a :class:`ResourceContract`
    call per the queue → method mapping above; an uncomputable bound propagates
    :class:`~gateway_v2.runtime.errors.CapacityUnavailable` from the contract unchanged, which the
    caller treats as refuse-to-admit / refuse-to-start (Req 13.3).

    :param contract: the signed capacity contract — the only holder of capacity literals.
    :param q_safe: the measured guard service rate; ``None`` or ``<= 0`` ⇒ refuse to start.
    :param audit_drain_rate_per_s: the audit sink's measured drain rate (``AuditSink.calibrate``).
    :param audit_bytes_per_record: the measured bytes a single audit record costs.
    :raises CapacityUnset: if ``q_safe`` is unset or non-positive.
    :raises CapacityUnavailable: if any bound is below the minimum required to serve.
    """
    if q_safe is None or q_safe <= 0:
        raise CapacityUnset("q_safe is unset; refuse to start")

    # The measured guard rate is the service rate for the request/guard/dispatch share and for
    # egress slot admission; the guard owner is just another owner on the same rule (Req 1.7).
    slot_depth = contract.queue_depth(q_safe)

    # Audit is NOT sized by queue_depth: a record is a few KB, so a per-worker-RSS slot count would
    # drop audit under any load (Req 7.5, see ResourceContract.audit_queue_depth docstring).
    audit_depth = contract.audit_queue_depth(audit_drain_rate_per_s, audit_bytes_per_record)

    return AdmissionBounds(
        concurrency=slot_depth,
        request_depth=slot_depth,
        guard_depth=slot_depth,
        dispatch_depth=slot_depth,
        egress_depth=slot_depth,
        audit_depth=audit_depth,
    )
