"""GW14b schema for an audit record plus the console projection.

Version 1 readers still see ``outcome``, ``phase``, and ``detail``. The console
projection is an optional sibling object ``console``. An unknown field fails,
and a required field that is absent fails (L14b-4). ``Finding.evidence`` is not
a field: a record that carries it is rejected, because the matched text is the
secret the pipeline just removed.

The control-plane translation built here does not apply the v1 drain defaults
(missing event type becomes ``request``, missing module becomes ``1.1``, missing
risk becomes ``0``). A v2 record that lacks a fact omits the fact.
"""

from __future__ import annotations

from collections.abc import Mapping

from gateway_v2.audit.record import AuditRecord

SOURCE = "gateway_v2"

_REQUIRED = ("v", "request_id", "org_id", "phase", "outcome", "at_ns", "detail")
_TOP = frozenset(_REQUIRED) | {"console"}
_PHASES = frozenset({"input", "output", "admission"})
_ACTIONS = frozenset({"allow", "flag", "redact", "block"})
_STAGE_ACTIONS = _ACTIONS | {"skip"}
_CONSOLE_KEYS = frozenset({"source", "zeroshield", "pipeline_trace"})
_TRACE_KEYS = frozenset({"final_action", "plan_version", "stages"})
_STAGE_KEYS = frozenset({"name", "action", "ran", "deciding_rule"})
_ZERO_KEYS = frozenset({"action", "request_id"})


class SchemaError(ValueError):
    """A record or a console object disagrees with the GW14b schema."""


def validate_audit_record(mapping: Mapping[str, object]) -> None:
    """Fail when ``mapping`` has an unknown field or lacks a required one."""
    extra = set(mapping) - _TOP
    if extra:
        name = sorted(extra)[0]
        raise SchemaError(f"field not in the schema: {name}")
    for key in _REQUIRED:
        if key not in mapping:
            raise SchemaError(f"required field absent: {key}")
    phase = mapping["phase"]
    if phase not in _PHASES:
        raise SchemaError(f"phase {phase!r} is not in the schema")
    detail = mapping["detail"]
    if not isinstance(detail, dict):
        raise SchemaError("detail must be an object")
    _reject_evidence(detail)
    console = mapping.get("console")
    if console is not None:
        validate_console(console)


def validate_console(console: object) -> None:
    """Fail when the console projection is not the object the card froze."""
    if not isinstance(console, dict):
        raise SchemaError("console must be an object")
    extra = set(console) - _CONSOLE_KEYS
    if extra:
        raise SchemaError(f"field not in the schema: {sorted(extra)[0]}")
    for key in _CONSOLE_KEYS:
        if key not in console:
            raise SchemaError(f"required field absent: {key}")
    if console["source"] != SOURCE:
        raise SchemaError("console source must be gateway_v2")
    _validate_zeroshield(console["zeroshield"])
    _validate_trace(console["pipeline_trace"])
    _reject_evidence(console)


def read_required(mapping: Mapping[str, object], field: str) -> object:
    """Read ``field``. Absence is a schema failure, not a default."""
    validate_audit_record(mapping)
    if field not in mapping:
        raise SchemaError(f"required field absent: {field}")
    return mapping[field]


def with_console(record: AuditRecord, console: Mapping[str, object]) -> dict[str, object]:
    """The stored record plus the projection. Version 1 keys are unchanged."""
    body = dict(record.as_mapping())
    body["console"] = dict(console)
    validate_audit_record(body)
    return body


def enforcement_fields(mapping: Mapping[str, object]) -> dict[str, object]:
    """Translate a v2 record. Does not invent event type, module, or risk."""
    validate_audit_record(mapping)
    phase = mapping["phase"]
    metadata: dict[str, object] = {
        "source": SOURCE,
        "phase": phase,
        "request_id": mapping["request_id"],
    }
    console = mapping.get("console")
    if isinstance(console, dict):
        metadata["pipeline_trace"] = console["pipeline_trace"]
        metadata["zeroshield"] = console["zeroshield"]
    return {"action": mapping["outcome"], "metadata": metadata}


def _validate_zeroshield(value: object) -> None:
    if not isinstance(value, dict):
        raise SchemaError("zeroshield must be an object")
    extra = set(value) - _ZERO_KEYS
    if extra:
        raise SchemaError(f"field not in the schema: {sorted(extra)[0]}")
    action = value.get("action")
    if action not in _ACTIONS:
        raise SchemaError("required field absent: action")
    if not isinstance(value.get("request_id"), str):
        raise SchemaError("required field absent: request_id")


def _validate_trace(value: object) -> None:
    if not isinstance(value, dict):
        raise SchemaError("pipeline_trace must be an object")
    extra = set(value) - _TRACE_KEYS
    if extra:
        raise SchemaError(f"field not in the schema: {sorted(extra)[0]}")
    for key in _TRACE_KEYS:
        if key not in value:
            raise SchemaError(f"required field absent: {key}")
    if value["final_action"] not in _ACTIONS:
        raise SchemaError("final_action is not an action")
    if not isinstance(value["plan_version"], str) or not value["plan_version"]:
        raise SchemaError("required field absent: plan_version")
    stages = value["stages"]
    if not isinstance(stages, list):
        raise SchemaError("stages must be a list")
    for stage in stages:
        _validate_stage(stage)


def _validate_stage(stage: object) -> None:
    if not isinstance(stage, dict):
        raise SchemaError("stage must be an object")
    extra = set(stage) - _STAGE_KEYS
    if extra:
        raise SchemaError(f"field not in the schema: {sorted(extra)[0]}")
    if not isinstance(stage.get("name"), str) or not stage["name"]:
        raise SchemaError("required field absent: name")
    if stage.get("action") not in _STAGE_ACTIONS:
        raise SchemaError("stage action is not in the schema")
    if not isinstance(stage.get("ran"), bool):
        raise SchemaError("required field absent: ran")


def _reject_evidence(value: object) -> None:
    if isinstance(value, dict):
        if "evidence" in value:
            raise SchemaError("field not in the schema: evidence")
        for child in value.values():
            _reject_evidence(child)
    elif isinstance(value, list):
        for child in value:
            _reject_evidence(child)
