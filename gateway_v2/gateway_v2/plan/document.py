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
    UNSET_CONTROLS,
    Action,
    AuditControl,
    FailurePosture,
    Mode,
    OrgControls,
    RateControl,
    RuleCondition,
    RuleKind,
    RuleScope,
    StreamingMode,
    Surface,
)
from gateway_v2.plan.compiler import CompileError, RuleDraft
from gateway_v2.plan.controls_codec import CONTROLS_VERSION, REQUIRES_CONTROLS, controls_body

_SUPPORTED_REQUIRES = frozenset({REQUIRES_CONTROLS})


@dataclass(frozen=True, slots=True)
class PlanDocument:
    """One tenant's published selection."""

    org_id: str
    streaming_mode: StreamingMode
    drafts: tuple[RuleDraft, ...]
    controls: OrgControls = UNSET_CONTROLS
    rag_settings: Mapping[str, object] | None = None


def encode_plan_body(document: PlanDocument) -> dict[str, object]:
    """The body the control plane signs. Canonicalisation is the signer's job."""
    body: dict[str, object] = {
        "org_id": document.org_id,
        "streaming_mode": document.streaming_mode.value,
        "rules": [_rule_body(draft) for draft in document.drafts],
    }
    if document.controls != UNSET_CONTROLS:
        body["controls"] = controls_body(document.controls)
        body["requires"] = [REQUIRES_CONTROLS]
    if document.rag_settings is not None:
        body["rag_settings"] = dict(document.rag_settings)
    return body


def _rule_body(draft: RuleDraft) -> dict[str, object]:
    body: dict[str, object] = {
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
    encoded = _encode_condition(draft.condition)
    if encoded is not None:
        body["condition"] = encoded
    return body


def _encode_condition(condition: RuleCondition | None) -> dict[str, object] | None:
    if condition is None:
        return None
    return {
        "kind": condition.kind.value,
        "field": condition.field,
        "direction": condition.direction.value,
        "keywords": list(condition.keywords),
        "pattern": condition.pattern,
        "detector_class": condition.detector_class,
        "pipeline_stage": condition.pipeline_stage,
    }


def decode_plan_body(raw: bytes) -> PlanDocument:
    """Parse a signed plan body. Raises CompileError on anything the compiler cannot accept."""
    body = _object(raw)
    rules = body.get("rules")
    if not isinstance(rules, list):
        raise CompileError("plan body field 'rules' must be a list")
    _check_requires(body)
    return PlanDocument(
        org_id=_text(body, "org_id"),
        streaming_mode=_member(StreamingMode, _text(body, "streaming_mode"), "streaming_mode"),
        drafts=tuple(_draft(entry, position) for position, entry in enumerate(rules)),
        controls=_controls(body),
        rag_settings=_rag_settings(body),
    )


def _check_requires(body: Mapping[str, object]) -> None:
    """A body from a newer control plane is refused, never half-applied."""
    if "requires" not in body:
        return
    value = body["requires"]
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise CompileError("plan body field 'requires' must be a list of strings")
    unknown = sorted(str(item) for item in value if item not in _SUPPORTED_REQUIRES)
    if unknown:
        raise CompileError(f"plan body requires unsupported features {unknown}")


def _controls(body: Mapping[str, object]) -> OrgControls:
    if "controls" not in body:
        return UNSET_CONTROLS
    raw = body["controls"]
    section = _section(raw, "controls", {"version", "rate", "org_tpm_limit", "max_response_tokens",
                                        "audit", "compliance"})
    version = section.get("version")
    if isinstance(version, bool) or not isinstance(version, int) or version != CONTROLS_VERSION:
        raise CompileError("plan controls 'version' is unsupported")
    return OrgControls(
        rate=_rate(section),
        org_tpm_limit=_count(section, "org_tpm_limit", "controls"),
        max_response_tokens=_count(section, "max_response_tokens", "controls"),
        audit=_audit(section),
        compliance_frameworks=_frameworks(section),
    )


def _section(raw: object, name: str, allowed: set[str]) -> Mapping[str, object]:
    if not isinstance(raw, dict):
        raise CompileError(f"plan {name} must be an object")
    unknown = sorted(str(key) for key in raw if key not in allowed)
    if unknown:
        raise CompileError(f"plan {name} has unknown keys {unknown}")
    return raw


def _count(body: Mapping[str, object], name: str, where: str, *, required: bool = False) -> int:
    if required and name not in body:
        raise CompileError(f"plan {where} field {name!r} is required")
    value = body.get(name, 0)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise CompileError(f"plan {where} field {name!r} must be a non-negative integer")
    return value


def _flag(body: Mapping[str, object], name: str, where: str) -> bool:
    value = body.get(name)
    if not isinstance(value, bool):
        raise CompileError(f"plan {where} field {name!r} must be a boolean")
    return value


def _rate(section: Mapping[str, object]) -> RateControl | None:
    if "rate" not in section:
        return None
    rate = _section(section["rate"], "controls.rate", {"enabled", "requests_per_minute", "burst"})
    return RateControl(
        enabled=_flag(rate, "enabled", "controls.rate"),
        requests_per_minute=_count(rate, "requests_per_minute", "controls.rate", required=True),
        burst=_count(rate, "burst", "controls.rate", required=True),
    )


def _audit(section: Mapping[str, object]) -> AuditControl | None:
    if "audit" not in section:
        return None
    audit = _section(
        section["audit"], "controls.audit", {"enabled", "incident_logging", "retention_days"},
    )
    return AuditControl(
        enabled=_flag(audit, "enabled", "controls.audit"),
        incident_logging=_flag(audit, "incident_logging", "controls.audit"),
        retention_days=_count(audit, "retention_days", "controls.audit", required=True),
    )


def _frameworks(section: Mapping[str, object]) -> tuple[str, ...]:
    if "compliance" not in section:
        return ()
    compliance = _section(section["compliance"], "controls.compliance", {"frameworks"})
    value = compliance.get("frameworks")
    if not isinstance(value, list) or not value:
        raise CompileError("plan controls.compliance 'frameworks' must be a non-empty list")
    if any(not isinstance(item, str) or not item.strip() for item in value):
        raise CompileError("plan controls.compliance 'frameworks' must hold non-empty strings")
    if len(set(value)) != len(value):
        raise CompileError("plan controls.compliance 'frameworks' has duplicates")
    return tuple(value)


def _rag_settings(body: Mapping[str, object]) -> Mapping[str, object] | None:
    raw = body.get("rag_settings")
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise CompileError("plan body field 'rag_settings' must be an object")
    return raw


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
            FailurePosture,
            _text(rules, "on_unavailable"),
            "on_unavailable",
        ),
        surfaces=_surfaces(rules),
        condition=_condition(rules),
    )


def _condition(entry: Mapping[str, object]) -> RuleCondition | None:
    if "condition" not in entry or entry.get("condition") is None:
        return None
    raw = entry["condition"]
    if not isinstance(raw, dict):
        raise CompileError("plan rule field 'condition' must be an object")
    body: Mapping[str, object] = raw
    return RuleCondition(
        kind=_member(RuleKind, _text(body, "kind"), "condition.kind"),
        field=_maybe_text(body, "field"),
        direction=_member(RuleScope, _text(body, "direction"), "condition.direction"),
        keywords=_keywords(body),
        pattern=_maybe_text(body, "pattern"),
        detector_class=_maybe_text(body, "detector_class"),
        pipeline_stage=_maybe_text(body, "pipeline_stage"),
    )


def _maybe_text(body: Mapping[str, object], name: str) -> str:
    value = body.get(name, "")
    if value is None:
        return ""
    if not isinstance(value, str):
        raise CompileError(f"plan rule field {name!r} must be a string")
    return value


def _keywords(body: Mapping[str, object]) -> tuple[str, ...]:
    value = body.get("keywords", [])
    if value is None:
        return ()
    if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
        raise CompileError("plan rule field 'keywords' must be a list of non-empty strings")
    return tuple(value)


def plan_body_of(
    org_id: str,
    drafts: Sequence[RuleDraft],
    streaming_mode: StreamingMode = StreamingMode.INCREMENTAL,
    controls: OrgControls = UNSET_CONTROLS,
) -> dict[str, object]:
    """Convenience for the writer and for tests."""
    return encode_plan_body(PlanDocument(org_id, streaming_mode, tuple(drafts), controls))
