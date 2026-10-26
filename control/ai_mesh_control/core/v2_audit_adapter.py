"""Translate one gateway-v2 audit JSON object into EnforcementEvent fields.

This module does not import gateway_v2. It does not write event_type, it does
not write risk_score, and it does not fill module_id with ``1.1`` when the
record did not name the chat route. The v1 drain in ``core.tasks`` is a
different function and stays responsible for v1 telemetry.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

_SOURCE = "gateway_v2"
_CHAT_ROUTE = "chat"
_CHAT_MODULE = "1.1"
_REQUIRED = ("request_id", "org_id", "phase", "outcome")


@dataclass(frozen=True, slots=True)
class TranslatedEvent:
    """Fields an EnforcementEvent writer may copy. Absent keys stay absent."""

    action: str
    metadata: dict[str, object]
    module_id: str | None


@dataclass(frozen=True, slots=True)
class RejectedV2:
    """A versioned record that must be counted and not stored."""

    reason: str


def translate(payload: object) -> TranslatedEvent | RejectedV2 | None:
    """Return None when the object is not a v2 record (no ``v`` key)."""
    if not isinstance(payload, Mapping) or "v" not in payload:
        return None
    missing = _missing(payload)
    if missing is not None:
        return RejectedV2(missing)
    if _has_evidence(payload):
        return RejectedV2("evidence")
    phase = payload["phase"]
    outcome = payload["outcome"]
    request_id = payload["request_id"]
    if not isinstance(phase, str) or not phase:
        return RejectedV2("phase")
    if not isinstance(outcome, str) or not outcome:
        return RejectedV2("outcome")
    if not isinstance(request_id, str) or not request_id:
        return RejectedV2("request_id")
    metadata: dict[str, object] = {
        "source": _SOURCE,
        "phase": phase,
        "request_id": request_id,
    }
    console = _console_of(payload)
    if console is not None:
        metadata["pipeline_trace"] = console.get("pipeline_trace")
        metadata["zeroshield"] = console.get("zeroshield")
    module_id = _CHAT_MODULE if _route(payload) == _CHAT_ROUTE else None
    return TranslatedEvent(action=outcome, metadata=metadata, module_id=module_id)


def _missing(payload: Mapping[str, object]) -> str | None:
    for key in _REQUIRED:
        if key not in payload:
            return key
    return None


def _route(payload: Mapping[str, object]) -> str | None:
    detail = payload.get("detail")
    if not isinstance(detail, Mapping):
        return None
    route = detail.get("route")
    return route if isinstance(route, str) else None


def _console_of(payload: Mapping[str, object]) -> Mapping[str, object] | None:
    top = payload.get("console")
    if isinstance(top, Mapping):
        return top
    detail = payload.get("detail")
    if isinstance(detail, Mapping):
        nested = detail.get("console")
        if isinstance(nested, Mapping):
            return nested
    return None


def _has_evidence(value: object) -> bool:
    if isinstance(value, Mapping):
        if "evidence" in value:
            return True
        return any(_has_evidence(child) for child in value.values())
    if isinstance(value, list):
        return any(_has_evidence(child) for child in value)
    return False
