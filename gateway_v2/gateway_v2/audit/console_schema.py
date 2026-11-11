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

import re
from collections.abc import Iterable, Mapping
from types import MappingProxyType

from gateway_v2.audit.record import AuditRecord

SOURCE = "gateway_v2"

#: The six chat stages, in canonical order.
STAGE_NAMES = (
    "auth",
    "rate_limit",
    "input_scan",
    "model_routing",
    "model_output",
    "output_guardrail",
)
#: The MCP surface has its own five (``tool_call`` replaces routing and output).
MCP_STAGE_NAMES = ("auth", "rate_limit", "input_scan", "tool_call", "output_guardrail")
#: A literal tuple: ``lint/check_console_inventory`` parses it and compares it to the frontend.
STAGE_ACTION_VOCAB = ("allow", "flag", "redact", "rewrite", "block", "error", "skip")

_REQUIRED = ("v", "request_id", "org_id", "phase", "outcome", "at_ns", "detail")
_TOP = frozenset(_REQUIRED) | {"console"}
_PHASES = frozenset({"input", "output", "admission"})
_DISPOSITIONS = frozenset({"allow", "flag", "redact", "rewrite", "block"})
CONSOLE_ACTIONS = _DISPOSITIONS | {"error"}
_STAGE_ACTIONS = CONSOLE_ACTIONS | {"skip"}
#: ``skip`` is ignored. ``error`` outranks ``redact`` and ``flag`` but never a ``block``.
ACTION_SEVERITY = MappingProxyType(
    {"allow": 0, "flag": 1, "redact": 2, "rewrite": 2, "error": 3, "block": 4}
)
_FINGERPRINT = re.compile(r"[0-9a-f]{12}")
_FULL_HASH = re.compile(r"[0-9a-f]{64}")
_CONSOLE_KEYS = frozenset({"source", "zeroshield", "pipeline_trace"})
_TRACE_KEYS = frozenset({"final_action", "plan_version", "stages"})
_TRACE_REQUIRED = frozenset({"final_action", "stages"})
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
        names = MCP_STAGE_NAMES if detail.get("route") == "mcp" else STAGE_NAMES
        validate_console(console, stage_names=names)


def final_action(actions: Iterable[str]) -> str:
    """The highest-severity action among ``actions``; ``skip`` is ignored; ``allow`` if none."""
    best = "allow"
    for action in actions:
        if action in ACTION_SEVERITY and ACTION_SEVERITY[action] > ACTION_SEVERITY[best]:
            best = action
    return best


def validate_console(
    console: object, *, stage_names: tuple[str, ...] = STAGE_NAMES
) -> None:
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
    trace = console["pipeline_trace"]
    _validate_trace(trace, stage_names)
    assert isinstance(trace, dict)
    if console["zeroshield"]["action"] != trace["final_action"]:
        raise SchemaError("zeroshield.action must equal final_action")
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
    if action not in CONSOLE_ACTIONS:
        raise SchemaError("required field absent: action")
    request_id = value.get("request_id")
    if not isinstance(request_id, str) or not request_id:
        raise SchemaError("required field absent: request_id")


def _validate_trace(value: object, stage_names: tuple[str, ...]) -> None:
    if not isinstance(value, dict):
        raise SchemaError("pipeline_trace must be an object")
    extra = set(value) - _TRACE_KEYS
    if extra:
        raise SchemaError(f"field not in the schema: {sorted(extra)[0]}")
    for key in _TRACE_REQUIRED:
        if key not in value:
            raise SchemaError(f"required field absent: {key}")
    if value["final_action"] not in CONSOLE_ACTIONS:
        raise SchemaError("final_action is not an action")
    stages = value["stages"]
    if not isinstance(stages, list):
        raise SchemaError("stages must be a list")
    for stage in stages:
        _validate_stage(stage, stage_names)
    _validate_order(stages, stage_names)
    if value["final_action"] != final_action(str(stage["action"]) for stage in stages):
        raise SchemaError("final_action disagrees with the stages")
    scan_ran = any(stage["name"] == "input_scan" and stage["ran"] for stage in stages)
    if "plan_version" in value:
        plan = value["plan_version"]
        if not isinstance(plan, str) or not plan:
            raise SchemaError("plan_version must be a non-empty string")
    elif scan_ran:
        raise SchemaError("required field absent: plan_version")


def _validate_order(stages: list[object], stage_names: tuple[str, ...]) -> None:
    last = -1
    for stage in stages:
        assert isinstance(stage, dict)
        index = stage_names.index(stage["name"])
        if index <= last:
            raise SchemaError("stages must be unique and in canonical order")
        last = index


def _validate_stage(stage: object, stage_names: tuple[str, ...]) -> None:
    if not isinstance(stage, dict):
        raise SchemaError("stage must be an object")
    extra = set(stage) - _STAGE_KEYS
    if extra:
        raise SchemaError(f"field not in the schema: {sorted(extra)[0]}")
    name = stage.get("name")
    if not isinstance(name, str) or not name:
        raise SchemaError("required field absent: name")
    if name not in stage_names:
        raise SchemaError(f"stage name not in the schema: {name}")
    action = stage.get("action")
    if action not in _STAGE_ACTIONS:
        raise SchemaError("stage action is not in the schema")
    ran = stage.get("ran")
    if not isinstance(ran, bool):
        raise SchemaError("required field absent: ran")
    if action == "skip" and ran:
        raise SchemaError("a skipped stage cannot have run")
    if not ran and action != "skip" and not (action == "error" and name == "input_scan"):
        raise SchemaError("a stage that did not run must be skip")
    if action == "allow" and not ran:
        raise SchemaError("an allowed stage must have run")
    if "deciding_rule" in stage:
        rule = stage["deciding_rule"]
        # GW28: ``model_routing`` always carries its routing decision, even when it allowed.
        plain = action in ("allow", "skip") and name != "model_routing"
        if not isinstance(rule, str) or not rule or plain:
            raise SchemaError("deciding_rule needs a non-empty rule on a non-allow stage")


def _reject_evidence(value: object) -> None:
    if isinstance(value, dict):
        if "evidence" in value:
            raise SchemaError("field not in the schema: evidence")
        fingerprint = value.get("key_fingerprint")
        if fingerprint is not None and (
            not isinstance(fingerprint, str) or not _FINGERPRINT.fullmatch(fingerprint)
        ):
            raise SchemaError("key_fingerprint must be 12 lowercase hex characters")
        for child in value.values():
            _reject_evidence(child)
    elif isinstance(value, list):
        for child in value:
            _reject_evidence(child)
    elif isinstance(value, str) and _FULL_HASH.fullmatch(value):
        raise SchemaError("a full credential hash must never be stored")
