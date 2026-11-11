"""GW29 provider-attempt accounting: the ONE place an attempt becomes a risk sample (design 5.5).

``outcome_for`` maps a closed set of provider facts and never looks at request content, so a tenant
cannot trip a model isolation with malformed requests or its own bad key. ``attempt_outcomes``
derives those facts from a finished request's hold ledger and its refusal code, and
``feed_attempt`` hands them to the sink (the incident audit tap) from ``emit_chat_audit``, the
single per-request outcome point. Nothing here raises: the tracker only tightens, so a failure is
swallowed and never changes a request outcome.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from gateway_v2.domain.incident import ModelOutcome
from gateway_v2.domain.posture import STREAM_MALFORMED_UPSTREAM
from gateway_v2.edge.request_holds import RequestHolds

__all__ = ("AttemptSink", "attempt_outcomes", "feed_attempt", "outcome_for")

#: Provider status classes (protocol facts, not capacity): 429 and the 5xx range.
_RATE_LIMITED = 429
_SERVER_ERRORS = range(500, 600)




def outcome_for(
    *,
    status: int | None = None,
    timed_out: bool = False,
    malformed: bool = False,
    guard_hit: bool = False,
    completed: bool = False,
) -> ModelOutcome | None:
    """The closed mapping of design 5.5. ``None`` means: this attempt counts for nothing.

    Precedence is fixed: timeout, malformed upstream, output-guard hit, provider status, then a
    completed stream. Provider 400/401/403/404/413/422 and every unmapped status are
    client- or credential-attributable and never counted. Gateway-side refusals, client
    disconnects and drain are not provider attempts and are never passed here.
    """
    if timed_out:
        return ModelOutcome.TIMEOUT
    if malformed:
        return ModelOutcome.MALFORMED
    if guard_hit:
        return ModelOutcome.GUARD_HIT
    if status is not None:
        if status == _RATE_LIMITED:
            return ModelOutcome.RATE_LIMITED
        if status in _SERVER_ERRORS:
            return ModelOutcome.SERVER_ERROR
        return None
    return ModelOutcome.OK if completed else None


#: Declared gateway codes (``dispatch/openai_upstream.py``) for provider facts and the guard.
_TIMEOUT = "upstream_timeout"
_RATE_LIMITED_CODE = "upstream_rate_limited"
_SERVER_CODES = frozenset({"upstream_error", "upstream_unavailable"})
_GUARD_CODE = "output_blocked"
_SERVER_STATUS = 500


@runtime_checkable
class AttemptSink(Protocol):
    """What ``emit_chat_audit`` feeds (``IncidentAuditTap`` in ``incident_wiring``)."""

    def record_attempt(self, *, org_id: str, model: str, outcome: ModelOutcome | None) -> None: ...


def attempt_outcomes(holds: RequestHolds, refuse_code: str | None) -> tuple[ModelOutcome, ...]:
    """The provider-attempt samples of one finished request (none when it never reached a model).

    Only a request that was dispatched to a named model counts. Gateway-side refusals, recheck
    and kill cuts never reach here as provider facts: ``incident_cause`` set means the gateway
    ended the request.
    """
    if not holds.dispatched or not holds.attempt_model or holds.incident_cause is not None:
        return ()
    refuse_code = refuse_code or holds.attempt_code
    if refuse_code is None:
        found = outcome_for(completed=True)
        return () if found is None else (found,)
    if refuse_code == _GUARD_CODE:
        # Design 5.5: the guard hit is a SEPARATE terminal sample beside the first-byte OK.
        ok = outcome_for(completed=True)
        hit = outcome_for(guard_hit=True)
        return tuple(o for o in (ok, hit) if o is not None)
    found = outcome_for(
        status=(
            429 if refuse_code == _RATE_LIMITED_CODE
            else _SERVER_STATUS if refuse_code in _SERVER_CODES
            else None
        ),
        timed_out=refuse_code == _TIMEOUT,
        malformed=refuse_code == STREAM_MALFORMED_UPSTREAM,
    )
    return () if found is None else (found,)


def feed_attempt(sink: object, holds: RequestHolds, refuse_code: str | None) -> None:
    """Hand this request's attempt samples to ``sink`` when it can take them. Never raises."""
    try:
        if not isinstance(sink, AttemptSink):
            return
        model = holds.attempt_model or ""
        for outcome in attempt_outcomes(holds, refuse_code):
            sink.record_attempt(org_id=holds.org_id, model=model, outcome=outcome)
    except Exception:  # noqa: BLE001 - risk accounting never changes a request outcome
        return
