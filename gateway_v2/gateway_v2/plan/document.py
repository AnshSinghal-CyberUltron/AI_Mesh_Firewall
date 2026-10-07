"""The plan record body: a tenant's SELECTION, not a compiled plan.

The control plane publishes what the tenant chose; every gateway compiles it with the same
`compile_plan`. That keeps one validation authority (GW05's exit criterion) and keeps the
published record independent of the compiler's internal representation, so a compiler change
does not require republishing every tenant. The cost is one compile per CHANGED record, which
is what GW05c is for.

A malformed or unsupported body is a `CompileError`, not a `StoreDataUnavailable`: the record is
genuinely signed, so the store is not at fault. The tenant keeps serving its previous valid plan
and the round continues for every other tenant — GW05's "never fail to an empty plan".
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from gateway_v2.domain.category import Category
from gateway_v2.domain.plan import (
    Action,
    FailurePosture,
    Mode,
    RuleScope,
    StreamingMode,
    Surface,
)
from gateway_v2.plan.compiler import CompileError, RuleDraft


@dataclass(frozen=True, slots=True)
class PlanDocument:
    """One tenant's published selection."""

    org_id: str
    streaming_mode: StreamingMode
    drafts: tuple[RuleDraft, ...]


def encode_plan_body(document: PlanDocument) -> dict[str, object]:
    """The body the control plane signs. Canonicalisation is the signer's job."""
    return {
        "org_id": document.org_id,
        "streaming_mode": document.streaming_mode.value,
        "rules": [
            {
                "rule_id": draft.rule_id,
                "category": draft.category.value,
                "mode": draft.mode.value,
                "action": draft.action.value,
                "threshold": draft.threshold,
                "priority": draft.priority,
                "scope": draft.scope.value,
                "on_unavailable": draft.on_unavailable.value,
                "surfaces": sorted(surface.value for surface in draft.surfaces),
            }
            for draft in document.drafts
        ],
    }


def decode_plan_body(raw: bytes) -> PlanDocument:
    """Parse a signed plan body. Raises CompileError on anything the compiler cannot accept."""
    body = _object(raw)
    rules = body.get("rules")
    if not isinstance(rules, list):
        raise CompileError("plan body field 'rules' must be a list")
    return PlanDocument(
        org_id=_text(body, "org_id"),
        streaming_mode=_member(StreamingMode, _text(body, "streaming_mode"), "streaming_mode"),
        drafts=tuple(_draft(entry, position) for position, entry in enumerate(rules)),
    )


def _object(raw: bytes) -> Mapping[str, object]:
    try:
        parsed: object = json.loads(raw)
    except (ValueError, UnicodeDecodeError) as exc:
        raise CompileError(f"plan body is not valid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise CompileError("plan body is not a JSON object")
    return parsed


def _text(body: Mapping[str, object], name: str) -> str:
    value = body.get(name)
    if not isinstance(value, str) or not value.strip():
        raise CompileError(f"plan body field {name!r} must be a non-empty string")
    return value


def _whole(body: Mapping[str, object], name: str) -> int:
    value = body.get(name)
    if isinstance(value, bool) or not isinstance(value, int):
        raise CompileError(f"plan body field {name!r} must be an integer")
    return value


def _member[T: str](enum: type[T], value: str, name: str) -> T:
    try:
        return enum(value)
    except ValueError as exc:
        raise CompileError(f"plan body field {name!r} has unsupported value {value!r}") from exc


def _threshold(entry: Mapping[str, object]) -> float | None:
    value = entry.get("threshold")
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CompileError("plan rule field 'threshold' must be a number or null")
    return float(value)


def _surfaces(entry: Mapping[str, object]) -> frozenset[Surface]:
    value = entry.get("surfaces")
    if not isinstance(value, list) or not value:
        raise CompileError("plan rule field 'surfaces' must be a non-empty list")
    out: set[Surface] = set()
    for item in value:
        if not isinstance(item, str):
            raise CompileError("plan rule field 'surfaces' must hold strings")
        out.add(_member(Surface, item, "surfaces"))
    return frozenset(out)


def _draft(entry: object, position: int) -> RuleDraft:
    if not isinstance(entry, dict):
        raise CompileError(f"plan rule at position {position} is not an object")
    rules: Mapping[str, object] = entry
    return RuleDraft(
        rule_id=_text(rules, "rule_id"),
        category=_member(Category, _text(rules, "category"), "category"),
        mode=_member(Mode, _text(rules, "mode"), "mode"),
        action=_member(Action, _text(rules, "action"), "action"),
        threshold=_threshold(rules),
        priority=_whole(rules, "priority"),
        scope=_member(RuleScope, _text(rules, "scope"), "scope"),
        on_unavailable=_member(
            FailurePosture, _text(rules, "on_unavailable"), "on_unavailable",
        ),
        surfaces=_surfaces(rules),
    )


def plan_body_of(
    org_id: str,
    drafts: Sequence[RuleDraft],
    streaming_mode: StreamingMode = StreamingMode.INCREMENTAL,
) -> dict[str, object]:
    """Convenience for the writer and for tests."""
    return encode_plan_body(PlanDocument(org_id, streaming_mode, tuple(drafts)))
