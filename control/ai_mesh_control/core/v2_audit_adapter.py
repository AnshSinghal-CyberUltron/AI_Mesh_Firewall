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


# The console vocabulary only. A row with no console (``rejected``/``shed``) is not ranked and
# reads -1, below every console action, exactly as before GW35 added ``error``.
_RANK = {"allow": 0, "flag": 1, "redact": 2, "error": 3, "block": 4}
# What ``translate`` accepts: the console vocabulary plus the two no-console admission outcomes.
_ACCEPTED = frozenset(_RANK) | {"rejected", "shed"}
_REFUSING = frozenset({"block", "error"})
_FINGERPRINT_LEN = 12


def parse_org_map(raw: str) -> dict[str, str]:
    """Parse ``dev-org=zeroshield,other=slug``. Blank input is an empty map."""
    mapping: dict[str, str] = {}
    for part in raw.split(","):
        piece = part.strip()
        if not piece:
            continue
        if "=" not in piece:
            continue
        source, slug = piece.split("=", 1)
        source = source.strip()
        slug = slug.strip()
        if source and slug:
            mapping[source] = slug
    return mapping


def merge_row(
    existing_action: str | None,
    existing_meta: Mapping[str, object] | None,
    translated: TranslatedEvent,
) -> tuple[str, dict[str, object]]:
    """One row per request. The stricter action wins. No event type and no risk score."""
    meta = dict(existing_meta or {})
    incoming = translated.metadata
    phase = incoming.get("phase")
    raw_phases = meta.get("phases")
    if isinstance(raw_phases, list):
        phases = [item for item in raw_phases if isinstance(item, str)]
    else:
        phases = []
    if isinstance(phase, str) and phase and phase not in phases:
        phases.append(phase)
    chosen = _stricter(existing_action, translated.action)
    stored: dict[str, object] = {
        "source": _SOURCE,
        "request_id": incoming.get("request_id"),
        "phases": phases,
    }
    fingerprint = incoming.get("key_fingerprint") or meta.get("key_fingerprint")
    if _fingerprint_ok(fingerprint):
        stored["key_fingerprint"] = fingerprint
    if chosen == translated.action or "pipeline_trace" not in meta:
        if "pipeline_trace" in incoming:
            stored["pipeline_trace"] = incoming["pipeline_trace"]
        if "zeroshield" in incoming:
            stored["zeroshield"] = incoming["zeroshield"]
    else:
        if "pipeline_trace" in meta:
            stored["pipeline_trace"] = meta["pipeline_trace"]
        if "zeroshield" in meta:
            stored["zeroshield"] = meta["zeroshield"]
    refused_at = _refused_at(stored.get("pipeline_trace"))
    if refused_at:
        stored["refused_at"] = refused_at
    module_id = translated.module_id or meta.get("module_id")
    if isinstance(module_id, str) and module_id:
        stored["module_id"] = module_id
    return chosen, stored


def _stricter(current: str | None, incoming: str) -> str:
    if current is None or _RANK.get(incoming, -1) >= _RANK.get(current, -1):
        return incoming
    return current


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
    action = outcome
    shown: object = None
    if console is not None:
        metadata["pipeline_trace"] = console.get("pipeline_trace")
        metadata["zeroshield"] = console.get("zeroshield")
        zero = console.get("zeroshield")
        if isinstance(zero, Mapping):
            shown = zero.get("action")
    if isinstance(shown, str):
        if shown not in _RANK:
            return RejectedV2("action")
        action = shown
    elif action not in _ACCEPTED:
        return RejectedV2("action")
    fingerprint = _detail_value(payload, "key_fingerprint")
    if _fingerprint_ok(fingerprint):
        metadata["key_fingerprint"] = fingerprint
    refused_at = _refused_at(metadata.get("pipeline_trace"))
    if refused_at:
        metadata["refused_at"] = refused_at
    module_id = _CHAT_MODULE if _route(payload) == _CHAT_ROUTE else None
    return TranslatedEvent(action=action, metadata=metadata, module_id=module_id)


def _fingerprint_ok(value: object) -> bool:
    """A 12-character lowercase hex fingerprint, nothing longer (a full hash is never stored)."""
    return (
        isinstance(value, str)
        and len(value) == _FINGERPRINT_LEN
        and all(ch in "0123456789abcdef" for ch in value)
    )


def _detail_value(payload: Mapping[str, object], key: str) -> object:
    detail = payload.get("detail")
    return detail.get(key) if isinstance(detail, Mapping) else None


def _refused_at(trace: object) -> str:
    """Name of the first stage that blocked or errored; empty when none did."""
    stages = trace.get("stages") if isinstance(trace, Mapping) else None
    if not isinstance(stages, list):
        return ""
    for stage in stages:
        if isinstance(stage, Mapping) and stage.get("action") in _REFUSING:
            name = stage.get("name")
            return name if isinstance(name, str) else ""
    return ""


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
