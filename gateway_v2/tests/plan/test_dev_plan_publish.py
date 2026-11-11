"""A published FirewallConfig replaces the booted plan. A bad body does not."""

from __future__ import annotations

import importlib.util
import json
import sys
import types
from pathlib import Path
from typing import Any

from gateway_v2.domain.category import Category
from gateway_v2.domain.plan import Action, ExecutionPlan, FailurePosture, Mode, RuleScope, Surface
from gateway_v2.edge.dev_plan_feed import PUBLISHED_PLAN_KEY, DevPlanFeed
from gateway_v2.plan.compiler import RuleDraft, compile_plan
from gateway_v2.plan.document import decode_plan_body

_CONTROL = Path(__file__).resolve().parents[3] / "control" / "ai_mesh_control" / "core"
_PUBLISH = _CONTROL / "firewall_v2_publish.py"


def test_a_published_body_replaces_the_booted_pii_rule() -> None:
    feed = DevPlanFeed(_boot(), now=0.0)
    body = _body("jailbreak", "block")
    version = feed.apply(body, compiled_at=2.0, now=1.0)
    state = feed.source().lookup("dev-org", now=1.0)
    assert isinstance(state, ExecutionPlan)
    assert version == state.version
    assert [rule.category for rule in state.rules] == [Category.JAILBREAK]
    assert feed.apply(body, compiled_at=3.0, now=2.0) == "unchanged"


def test_a_rejected_body_keeps_the_booted_plan() -> None:
    feed = DevPlanFeed(_boot(), now=0.0)
    before = feed.source().lookup("dev-org", now=0.0)
    assert isinstance(before, ExecutionPlan)
    assert feed.apply(b"{", compiled_at=2.0, now=1.0) == "rejected"
    mismatch = _body("pii", "redact", org_id="other")
    assert feed.apply(mismatch, compiled_at=2.0, now=1.0) == "rejected"
    after = feed.source().lookup("dev-org", now=1.0)
    assert isinstance(after, ExecutionPlan)
    assert after.version == before.version


def test_the_console_document_decodes_and_uses_the_same_key() -> None:
    publisher = _load_publisher()
    assert publisher.PUBLISHED_PLAN_KEY == PUBLISHED_PLAN_KEY
    assert publisher.gateway_org_for("zeroshield", "dev-org=zeroshield") == "dev-org"
    assert publisher.gateway_org_for("zeroshield", "a=zeroshield,b=zeroshield") is None
    payload = {
        "firewall_enabled": True,
        "pii_detection_enabled": False,
        "output_pii_enabled": False,
        "jailbreak_detection_enabled": True,
    }
    raw = publisher.document_bytes("dev-org", payload)
    document = decode_plan_body(raw)
    plan = compile_plan("dev-org", document.drafts, compiled_at=1.0)
    assert Category.PII not in {rule.category for rule in plan.rules}
    assert Category.JAILBREAK in {rule.category for rule in plan.rules}


def _boot() -> ExecutionPlan:
    return compile_plan("dev-org", (_draft("pii", Action.REDACT),), compiled_at=1.0)


def _body(category: str, action: str, org_id: str = "dev-org") -> bytes:
    return json.dumps(
        {
            "org_id": org_id,
            "rules": [
                {
                    "action": action,
                    "category": category,
                    "mode": "enforce",
                    "on_unavailable": "fail_closed",
                    "priority": 100,
                    "rule_id": f"fw-{category}-input",
                    "scope": "input",
                    "surfaces": ["chat"],
                    "threshold": None,
                }
            ],
            "streaming_mode": "incremental",
        },
        sort_keys=True,
    ).encode("utf-8")


def _draft(category: str, action: Action) -> RuleDraft:
    return RuleDraft(
        rule_id=f"fw-{category}-input",
        category=Category(category),
        mode=Mode.ENFORCE,
        action=action,
        threshold=None,
        priority=100,
        scope=RuleScope.INPUT,
        on_unavailable=FailurePosture.FAIL_CLOSED,
        surfaces=frozenset({Surface.CHAT}),
    )


def _load_publisher() -> Any:
    package = types.ModuleType("core")
    package.__path__ = [str(_CONTROL)]
    sys.modules["core"] = package
    for name in ("firewall_v2_map", "v2_audit_adapter", "firewall_v2_publish"):
        _load(name)
    loaded = sys.modules["core.firewall_v2_publish"]
    return loaded


def _load(name: str) -> None:
    path = _CONTROL / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"core.{name}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
