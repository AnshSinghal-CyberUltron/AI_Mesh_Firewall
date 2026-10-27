"""Request-path counters for ``/metrics``: requests per tenant, rejections, and postures.

The Publisher owns the counters; this module decides what each terminal outcome counts as.
A refusal before any input or output decision is an admission rejection, ``shed`` for
``overload_shed`` and ``rejected`` otherwise. That is the rule the audit admission record
uses, so ``/metrics`` and the audit stream agree. A refusal whose code is a fail-closed
posture also bumps that posture's own counter. A policy BLOCK is neither.

Every function here never raises: a broken observer cannot turn a served request into a 500.
"""

from __future__ import annotations

from typing import Protocol

from gateway_v2.audit.record import REJECTED, SHED
from gateway_v2.domain.posture import OVERLOAD_SHED
from gateway_v2.edge import errors
from gateway_v2.edge.counters import POSTURE_CODES
from gateway_v2.edge.request_holds import RequestHolds

__all__ = ("OutcomeObserver", "observe_outcome", "observe_refusal", "observe_tenant")


class OutcomeObserver(Protocol):
    """What the request path calls. :class:`~gateway_v2.edge.publisher.Publisher` is one."""

    def count_tenant(self, tenant_key: str) -> None: ...

    def observe_rejection(self, status: int, reason: str) -> None: ...

    def observe_posture(self, code: str) -> None: ...


def observe_tenant(observer: OutcomeObserver | None, org_id: str) -> None:
    """Count one request for the org once its identity resolved."""
    if observer is None or not org_id:
        return
    try:
        observer.count_tenant(org_id)
    except Exception:  # noqa: BLE001 — never raise into the serving request.
        return


def observe_refusal(observer: OutcomeObserver | None, code: str | None, *, decided: bool) -> None:
    """Count one refusal. ``decided`` is True once an input or output decision exists."""
    if observer is None or not code:
        return
    try:
        if code in POSTURE_CODES:
            observer.observe_posture(code)
        if decided:
            return
        status = errors.render_http(code).status
        observer.observe_rejection(status, SHED if code == OVERLOAD_SHED else REJECTED)
    except Exception:  # noqa: BLE001 — never raise into the serving request.
        return


def observe_outcome(
    observer: OutcomeObserver | None, holds: RequestHolds, refuse_code: str | None
) -> None:
    """Count one finished chat, responses or MCP request from its hold ledger."""
    observe_tenant(observer, holds.org_id)
    decided = holds.input_decision is not None or holds.output_decision is not None
    observe_refusal(observer, refuse_code, decided=decided)
