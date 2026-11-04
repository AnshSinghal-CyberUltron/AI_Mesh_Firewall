"""GW29 attempt re-check helpers (design 6.3a, 6.4), kept out of ``edge/routes.py`` (799 lines).

The gate decides at phase 4b; the kill switch can engage (or a model be isolated) before the
provider is actually opened. So the ALREADY-ROUTED model is re-checked at every provider attempt,
always ABOVE ``holds.mark_dispatched()``: a refusal leaves the holds undispatched, so the existing
``RequestHolds.release`` refunds the spend (P9). The recheck never reroutes (R4.4, AC20).

* :func:`first_attempt_refusal` runs before the streaming 200 head is written: a refusal is the
  ordinary 503 JSON envelope with ``Retry-After`` + ``x-should-retry: false``, no SSE head.
* :func:`attempt_recheck_cut` runs at a retry attempt (head already sent): it flips the kill latch
  with the stream code mapped from the underlying verdict code.
* :func:`recheck_or_raise` runs on the non-streaming path and raises :class:`IncidentRecheckRefused`
  for ``collect_and_enforce`` to turn into a 503 envelope.
* :func:`note_stream_cut` stamps the cut cause on the holds so exactly one terminal audit record is
  written for a stream ended by an incident code.

A missing binding is never guessed: it is the ``unwired`` refusal, so the failure is a refusal,
never a missing in-flight kill.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from types import MappingProxyType

from gateway_v2.domain import posture
from gateway_v2.domain.incident import GateVerdict, fail_closed_recheck
from gateway_v2.edge.asgi_sse import ASGISend
from gateway_v2.edge.cancel import KillLatch
from gateway_v2.edge.chat_lifecycle import LifecycleOutcome
from gateway_v2.edge.console_projection import refusal_console
from gateway_v2.edge.request_holds import RequestHolds
from gateway_v2.edge.route_emit import emit_error_code

__all__ = (
    "IncidentRecheckRefused",
    "Recheck",
    "attempt_recheck_cut",
    "first_attempt_refusal",
    "note_stream_cut",
    "recheck_or_raise",
    "stream_code_for",
)

#: ``recheck(org_id=..., model=...)`` -> :class:`GateVerdict` (``IncidentGate.recheck``).
Recheck = Callable[..., GateVerdict]

#: Underlying refuse code -> the terminal stream code a cut after the head renders.
_STREAM_CODE: Mapping[str, str] = MappingProxyType(
    {
        posture.KILL_SWITCH_ENGAGED: posture.STREAM_KILLED,
        posture.MODEL_KILLED: posture.STREAM_KILLED,
        posture.KILL_SWITCH_UNAVAILABLE: posture.STREAM_SNAPSHOT_STALE,
    }
)

#: Stream terminal code -> the closed ``cut_*`` cause of the single terminal audit record. Codes
#: absent here (timeouts, max duration, plan) are not incident cuts and keep the generic record.
_CUT_CAUSE: Mapping[str, str] = MappingProxyType(
    {
        posture.STREAM_KILLED: "cut_killed",
        posture.STREAM_KEY_REVOKED: "cut_key_revoked",
        posture.STREAM_SNAPSHOT_STALE: "cut_snapshot_stale",
        posture.STREAM_MODEL_ISOLATED: "cut_model_isolated",
    }
)


class IncidentRecheckRefused(Exception):
    """The non-streaming recheck refused; carries the verdict (module-local, no state)."""

    def __init__(self, verdict: GateVerdict) -> None:
        super().__init__(verdict.refuse_code)
        self.verdict = verdict


def stream_code_for(refuse_code: str | None) -> str:
    """The stream terminal code for an underlying refuse code (isolated is the default)."""
    return _STREAM_CODE.get(refuse_code or "", posture.STREAM_MODEL_ISOLATED)


def _verdict(recheck: Recheck, org_id: str | None, model: str | None) -> GateVerdict:
    if org_id is None or model is None:
        return fail_closed_recheck(org_id="", model="")
    return recheck(org_id=org_id, model=model)


def recheck_or_raise(recheck: Recheck, *, org_id: str, model: str) -> None:
    """Non-streaming: raise :class:`IncidentRecheckRefused` when the recheck refuses."""
    verdict = _verdict(recheck, org_id, model)
    if verdict.refuse_code is not None:
        raise IncidentRecheckRefused(verdict)


def attempt_recheck_cut(
    recheck: Recheck, *, org_id: str, model: str, kill_latch: KillLatch, holds: RequestHolds
) -> bool:
    """Streaming retry attempt: on a refusal flip the kill latch and note it; ``True`` = refused.

    Called ABOVE ``holds.mark_dispatched()`` so a refusal is refunded. The cause is ``recheck`` with
    the underlying code, so exactly one ``incident_refusal`` audit record is written.
    """
    verdict = _verdict(recheck, org_id, model)
    code = verdict.refuse_code
    if code is None:
        return False
    holds.note_incident_cause("recheck", underlying=code, retry_after_s=verdict.retry_after_s)
    kill_latch.kill(stream_code_for(code))
    return True


async def first_attempt_refusal(
    recheck: Recheck, send: ASGISend, outcome: LifecycleOutcome, holds: RequestHolds
) -> str | None:
    """Recheck BEFORE the SSE head; on a refusal render the 503 JSON and return the code."""
    binding = outcome.binding
    verdict = _verdict(
        recheck,
        None if binding is None else binding.org_id,
        outcome.routed_model if binding is not None else None,
    )
    code = verdict.refuse_code
    if code is None:
        return None
    holds.note_incident_cause("recheck", underlying=code, retry_after_s=verdict.retry_after_s)
    await emit_error_code(
        send,
        code,
        holds.request_id or None,
        console=refusal_console(holds, code),
        retry_after_s=verdict.retry_after_s,
    )
    return code


def note_stream_cut(holds: RequestHolds | None, stream_code: str) -> None:
    """Stamp the cut cause for an incident stream code, unless a cause is already recorded.

    The code is also kept as the provider attempt's end (risk accounting maps provider codes only).
    """
    if holds is None:
        return
    if holds.attempt_code is None:
        holds.note_attempt_code(stream_code)
    if holds.incident_cause is not None:
        return
    cause = _CUT_CAUSE.get(stream_code)
    if cause is not None:
        holds.note_incident_cause(cause, underlying=stream_code)
