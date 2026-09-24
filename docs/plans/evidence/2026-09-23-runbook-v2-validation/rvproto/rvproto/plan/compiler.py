"""Compile a raw plan document into an immutable ExecutionPlan (control-plane side).

Validation rejects: unknown detectors, a detector outside the rule's category,
semantic rules without a threshold in (0, 1], two rules selecting the same
detector in the same phase. required_* sets are derived here, never at request time.
"""

from __future__ import annotations

import time
from types import MappingProxyType
from typing import Any

from rvproto.domain.findings import DETECTORS, SEMANTIC_DETECTOR, Category
from rvproto.domain.plan import (
    Action,
    ExecutionPlan,
    FailurePosture,
    Mode,
    Phase,
    Posture,
    Rule,
    Scope,
    StreamingMode,
)


class PlanInvalid(ValueError):
    pass


def _expand(category: Category, raw: list[str]) -> frozenset[str]:
    out: set[str] = set()
    for d in raw:
        if d.endswith(".*"):
            prefix = d[:-1]
            hits = {k for k in DETECTORS if k.startswith(prefix) and k != SEMANTIC_DETECTOR}
            if not hits:
                raise PlanInvalid(f"pattern {d!r} selects no detector")
            out |= hits
        elif d in DETECTORS:
            out.add(d)
        else:
            raise PlanInvalid(f"unknown detector {d!r}")
    for d in out:
        if DETECTORS[d] is not category:
            raise PlanInvalid(f"detector {d} is {DETECTORS[d]}, rule category is {category}")
    return frozenset(out)


def _rule(raw: dict[str, Any]) -> Rule:
    category = Category(raw["category"])
    detectors = _expand(category, list(raw["detectors"]))
    posture_raw = raw.get("on_unavailable", {"kind": "fail_closed"})
    kind = Posture(posture_raw["kind"])
    degrade = Action(posture_raw["degrade_to"]) if kind is Posture.DEGRADE_TO else None
    threshold = raw.get("threshold")
    if SEMANTIC_DETECTOR in detectors and not (threshold is not None and 0 < threshold <= 1):
        raise PlanInvalid(f"rule {raw['rule_id']}: semantic detector needs threshold in (0, 1]")
    return Rule(
        rule_id=str(raw["rule_id"]),
        category=category,
        detectors=detectors,
        mode=Mode(raw["mode"]),
        action=Action(raw["action"]),
        threshold=float(threshold) if threshold is not None else None,
        priority=int(raw["priority"]),
        scope=Scope(raw["scope"]),
        on_unavailable=FailurePosture(kind, degrade),
    )


def compile_plan(doc: dict[str, Any]) -> ExecutionPlan:
    rules = tuple(sorted((_rule(r) for r in doc["rules"]), key=lambda r: (r.priority, r.rule_id)))
    selection: dict[tuple[Phase, str], Rule] = {}
    req: dict[Phase, set[str]] = {Phase.INPUT: set(), Phase.OUTPUT: set()}
    for rule in rules:
        if rule.mode is Mode.OFF:
            continue
        for phase in (Phase.INPUT, Phase.OUTPUT):
            if not rule.applies(phase):
                continue
            if phase is Phase.OUTPUT and SEMANTIC_DETECTOR in rule.detectors:
                raise PlanInvalid("output semantic detection is not supported by this build")
            for d in rule.detectors:
                if (phase, d) in selection:
                    raise PlanInvalid(f"{d} selected twice for {phase}")
                selection[(phase, d)] = rule
                req[phase].add(d)
    return ExecutionPlan(
        org_id=str(doc["org_id"]),
        version=str(doc["version"]),
        compiled_at=time.time(),
        rules=rules,
        required_input=frozenset(req[Phase.INPUT]),
        required_output=frozenset(req[Phase.OUTPUT]),
        streaming_mode=StreamingMode(doc.get("streaming_mode", "incremental")),
        integrity=str(doc.get("integrity", "platform-v1")),
        selection=MappingProxyType(selection),
    )
