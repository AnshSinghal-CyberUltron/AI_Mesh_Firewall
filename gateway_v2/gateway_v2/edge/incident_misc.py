"""GW29 incident gating for the misc and MCP surfaces (design 6.4b), out of ``edge/openai_misc.py``.

Embeddings, moderations and MCP do not go through ``run_lifecycle``, so each wires the gate by name
(an unwired route refuses: ``fail_closed_*``). The org id is ``identity.org_id`` from the resolved
Principal only (AC7). Every refusal is ONE 503 with ``Retry-After`` + ``x-should-retry: false``, one
audit record, and charges nothing (the gate runs BEFORE admission; a recheck sits above
``holds.mark_dispatched()`` so ``release`` refunds).

* embeddings never reroute (vector dimensions differ between models): a verdict carrying a
  reroute is converted to ``REROUTE_REFUSED`` with cause ``reroute_unsupported``;
* moderations and MCP name no routable model, so only the org-wide gate (``decide_org``) applies.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import TYPE_CHECKING

from gateway_v2.audit.record import AuditRecord
from gateway_v2.domain import posture
from gateway_v2.domain.incident import GateVerdict
from gateway_v2.edge.asgi_sse import ASGISend
from gateway_v2.edge.audit_emit import emit_chat_audit
from gateway_v2.edge.incident_recheck import IncidentRecheckRefused
from gateway_v2.edge.outcome_metrics import OutcomeObserver, observe_refusal
from gateway_v2.edge.request_holds import RequestHolds
from gateway_v2.edge.route_emit import emit_error_code

if TYPE_CHECKING:
    from gateway_v2.edge.openai_misc import MiscRoute

__all__ = (
    "embeddings_gate",
    "moderations_gate",
    "refuse_incident",
    "refuse_recheck",
    "unsupported_reroute",
    "verdict_or_refusal",
)

_EMBEDDINGS = "embeddings"
_MODERATIONS = "moderations"
_ID_HEX_WIDTH = 24
_REROUTE_UNSUPPORTED = "reroute_unsupported"
_RETRY_FLOOR_S = 1.0


def unsupported_reroute(verdict: GateVerdict) -> GateVerdict:
    """Turn a reroute verdict into the embeddings refusal; any other verdict passes through."""
    if verdict.reroute is None:
        return verdict
    return GateVerdict.refuse(posture.REROUTE_REFUSED, _REROUTE_UNSUPPORTED, _RETRY_FLOOR_S)


def verdict_or_refusal(verdict: GateVerdict) -> tuple[str, str, float] | None:
    """``(code, cause, retry_after_s)`` for a refusing verdict, else ``None``."""
    if verdict.refuse_code is None:
        return None
    cause = verdict.cause or "gate_error"
    return verdict.refuse_code, cause, verdict.retry_after_s or _RETRY_FLOOR_S


async def refuse_incident(
    send: ASGISend,
    holds: RequestHolds,
    verdict: GateVerdict,
    *,
    route: str,
    audit: Callable[[AuditRecord], bool] | None,
    observer: OutcomeObserver | None,
) -> None:
    """Render the 503 for a refusing ``verdict`` and write the one incident audit record."""
    refusal = verdict_or_refusal(verdict)
    code, cause, retry = refusal or (posture.KILL_SWITCH_UNAVAILABLE, "gate_error", _RETRY_FLOOR_S)
    holds.note_incident_cause(cause, retry_after_s=retry)
    observe_refusal(observer, code, decided=False)
    await emit_error_code(send, code, holds.request_id or None, retry_after_s=retry)
    emit_chat_audit(audit, holds, code, route=route, observer=None)


async def refuse_recheck(
    send: ASGISend,
    holds: RequestHolds,
    refused: IncidentRecheckRefused,
    *,
    route: str,
    audit: Callable[[AuditRecord], bool] | None,
    observer: OutcomeObserver | None,
) -> None:
    """Render a recheck refusal (cause ``recheck``, underlying code) with one audit record."""
    verdict = refused.verdict
    code = verdict.refuse_code or posture.KILL_SWITCH_UNAVAILABLE
    retry = verdict.retry_after_s or _RETRY_FLOOR_S
    holds.note_incident_cause("recheck", underlying=code, retry_after_s=retry)
    observe_refusal(observer, code, decided=False)
    await emit_error_code(send, code, holds.request_id or None, retry_after_s=retry)
    emit_chat_audit(audit, holds, code, route=route, observer=None)


async def embeddings_gate(
    deps: MiscRoute,
    send: ASGISend,
    holds: RequestHolds,
    *,
    org_id: str,
    model: str,
    request_id: str,
) -> str | None:
    """Phase 4b for embeddings: the canonical routed model, or ``None`` after the 503 was sent."""
    verdict = unsupported_reroute(
        deps.incident_gate.decide(org_id=org_id, requested_model=model, request_id=request_id)
    )
    if verdict.refuse_code is not None or verdict.routed_model is None:
        await refuse_incident(
            send, holds, verdict, route=_EMBEDDINGS, audit=deps.audit, observer=deps.observer
        )
        return None
    return verdict.routed_model


async def moderations_gate(deps: MiscRoute, send: ASGISend, *, org_id: str) -> bool:
    """Org-wide gate for moderations (no provider, no routable model); ``False`` = refused."""
    request_id = f"zs-{org_id}-{uuid.uuid4().hex[:_ID_HEX_WIDTH]}"
    verdict = deps.incident_org_gate(org_id=org_id, request_id=request_id)
    if verdict.refuse_code is None:
        return True
    holds = RequestHolds()
    holds.note_tenant(org_id)
    holds.note_request(request_id)
    await refuse_incident(
        send, holds, verdict, route=_MODERATIONS, audit=deps.audit, observer=deps.observer
    )
    return False
