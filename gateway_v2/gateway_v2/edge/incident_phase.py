"""GW29 phase 4b of the chat lifecycle: the incident gate, split out of ``edge/openai_chat.py``.

Phase 4b runs after the plan phase and ``holds.note_request``, BEFORE admission and budget, so a
refusal holds no slot and charges nothing (P9). It is the one place the verdict and the resolved
identity are both in scope, so it also builds the :class:`StreamBinding` an in-flight stream is cut
against and the reroute provenance note. The org id is ``identity.org_id`` from the resolved
Principal only; nothing from headers or body reaches the gate (AC7).

``edge`` only: it renders nothing itself, it returns a refused :class:`LifecycleOutcome` (value
code + ``retry_after_s`` + ``cause``) for ``ChatRoute`` to render through ``edge/errors``.
"""

from __future__ import annotations

from gateway_v2.domain.incident import (
    GateVerdict,
    IncidentDecider,
    RerouteNote,
    StreamBinding,
)
from gateway_v2.edge.chat_lifecycle import LifecycleOutcome, _refused
from gateway_v2.edge.request_holds import RequestHolds

__all__ = ("incident_binding", "incident_phase", "note_reroute", "proceed_binding")


def incident_phase(
    gate: IncidentDecider,
    *,
    org_id: str,
    model: str,
    request_id: str,
    holds: RequestHolds,
) -> tuple[GateVerdict, LifecycleOutcome | None]:
    """Decide phase 4b; return ``(verdict, refused_outcome_or_None)``.

    On a refusal the cause is noted on ``holds`` HERE so every caller of ``run_lifecycle`` (chat,
    completions, responses) gets the single incident audit record and no generic admission one.
    """
    verdict = gate.decide(org_id=org_id, requested_model=model, request_id=request_id)
    if verdict.refuse_code is None:
        return verdict, None
    holds.note_incident_cause(
        verdict.cause or "gate_error", retry_after_s=verdict.retry_after_s
    )
    return verdict, _refused(
        verdict.refuse_code,
        request_id=request_id,
        retry_after_s=verdict.retry_after_s,
        cause=verdict.cause,
    )


def incident_binding(
    verdict: GateVerdict, *, org_id: str, key_hash: str, feed_seq: int
) -> StreamBinding:
    """The binding an in-flight stream is cut against: org, the ROUTED model, key, feed position."""
    return StreamBinding(
        org_id=org_id,
        model=verdict.routed_model or "",
        key_hash=key_hash,
        key_feed_seq=feed_seq,
    )


def note_reroute(holds: RequestHolds, verdict: GateVerdict, requested: str) -> None:
    """Write the reroute provenance onto ``holds`` before any provider contact (both paths)."""
    decision = verdict.reroute
    if decision is None or verdict.routed_model is None:
        return
    canonical = decision.isolated_model or requested
    holds.note_reroute(
        RerouteNote(
            requested=canonical,
            routed=verdict.routed_model,
            isolated_model=canonical,
            decision_source=decision.decision_source,
            reason=decision.reason,
        )
    )


def proceed_binding(
    holds: RequestHolds,
    verdict: GateVerdict,
    *,
    requested: str,
    org_id: str,
    key_hash: str,
    feed_seq: int,
) -> StreamBinding:
    """Note the reroute (if any) and build the stream binding."""
    note_reroute(holds, verdict, requested)
    return incident_binding(verdict, org_id=org_id, key_hash=key_hash, feed_seq=feed_seq)
