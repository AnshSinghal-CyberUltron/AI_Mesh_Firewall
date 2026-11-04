# ruff: noqa: E501  (GW33 test file: long assertion/docstring lines kept readable)
"""GW33 task 11: the control-plane map, publisher and serializer agree with the compiler (Property 12)."""
from __future__ import annotations

import importlib.util
import json
import random
import sys
import types
from pathlib import Path
from typing import Any

import pytest

from gateway_v2.domain import Action, StreamingMode
from gateway_v2.plan.compiler import CompileError, compile_plan
from gateway_v2.plan.document import decode_plan_body

_CONTROL = Path(__file__).resolve().parents[3] / "control" / "ai_mesh_control" / "core"
_ITERATIONS = 400


def _publisher() -> Any:
    package = types.ModuleType("core")
    package.__path__ = [str(_CONTROL)]
    sys.modules["core"] = package
    for name in ("firewall_v2_map", "v2_audit_adapter", "firewall_v2_publish"):
        path = _CONTROL / f"{name}.py"
        spec = importlib.util.spec_from_file_location(f"core.{name}", path)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
    return sys.modules["core.firewall_v2_publish"]


def _map() -> Any:
    _publisher()
    return sys.modules["core.firewall_v2_map"]


def _payload(**kw: object) -> dict[str, object]:
    base: dict[str, object] = {
        "firewall_enabled": True, "enforcement_mode": "block", "content_filtering_enabled": True,
        "pii_detection_enabled": True, "jailbreak_detection_enabled": True,
        "prompt_injection_threshold": 0.8, "toxicity_threshold": 0.7,
        "tier2_fail_closed_enabled": True, "response_filtering_enabled": True,
        "output_pii_enabled": True, "output_pii_action": "redact",
        "output_credential_enabled": True, "output_credential_action": "block",
        "output_ip_leakage_enabled": True, "output_ip_leakage_action": "flag",
        "factuality_check_enabled": False, "output_hallucination_action": "flag",
    }
    base.update(kw)
    return base


def _compile(raw: bytes):  # type: ignore[no-untyped-def]
    document = decode_plan_body(raw)
    return compile_plan("org", document.drafts, streaming_mode=document.streaming_mode,
                        compiled_at=1.0), document


def test_only_model_downgrade_is_still_unsupported_and_names_its_owner() -> None:
    pub = _publisher()
    assert pub._UNSUPPORTED_ACTIONS == {"model_downgrade"}
    policy = {"code": "P", "policy_domain": "pipeline", "category": "pii", "enabled": True,
              "rules": [{"id": 1, "name": "n", "action": "model_downgrade", "enabled": True,
                         "rule_type": "keywords", "condition": {"keywords": ["x"]}}]}
    rows, unsupported = pub.policy_drafts([policy])
    assert rows == [] and unsupported == ["P/n (model_downgrade): owner GW28"]


def test_rewrite_policy_rows_are_gated_per_org_and_use_the_platform_template(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pub = _publisher()
    policy = {"code": "P", "policy_domain": "pipeline", "category": "pii", "enabled": True,
              "rules": [{"id": 1, "name": "n", "action": "rewrite", "enabled": True,
                         "rule_type": "keywords", "condition": {"keywords": ["zz-top"]}}]}
    rows, unsupported = pub.policy_drafts([policy], "acme")
    assert rows == [] and "not enabled for this org" in unsupported[0]  # default: refused, loud
    monkeypatch.setenv("AMF_V2_REWRITE_ORGS", "acme")
    rows, unsupported = pub.policy_drafts([policy], "acme")
    assert unsupported == [] and rows[0]["action"] == "rewrite"
    assert rows[0]["rewrite_template"] == pub.REWRITE_TEMPLATES["pii"]
    rows, _ = pub.policy_drafts([policy], "other")
    assert rows == []
    # A row-supplied template is used only when demonstrably safe.
    unsafe = {**policy, "rules": [{**policy["rules"][0], "rewrite_template": "see http://x.co"}]}
    assert pub.policy_drafts([unsafe], "acme")[0][0]["rewrite_template"] == pub.REWRITE_TEMPLATES["pii"]
    safe = {**policy, "rules": [{**policy["rules"][0], "rewrite_template": "[hidden]"}]}
    assert pub.policy_drafts([safe], "acme")[0][0]["rewrite_template"] == "[hidden]"
    own = {**policy, "rules": [{**policy["rules"][0], "rewrite_template": "zz-top fan"}]}
    assert pub.policy_drafts([own], "acme")[0][0]["rewrite_template"] == pub.REWRITE_TEMPLATES["pii"]


def test_every_platform_template_compiles_under_the_contract_limit() -> None:
    from gateway_v2.runtime.resources import ResourceContract
    from tests.e2e.gw33_support import contract

    pub = _publisher()
    limit = contract().rewrite_template_max_chars()
    assert isinstance(contract(), ResourceContract)
    for category, template in pub.REWRITE_TEMPLATES.items():
        assert 0 < len(template) <= limit, category
    # Compile each as a real REWRITE rule through the real compiler.
    for field, category in (("output_pii_action", "pii"), ("output_credential_action", "credential")):
        raw = pub.document_bytes("org", _payload(**{field: "rewrite"}))
        plan, _ = _compile(raw)
        assert any(r.action is Action.REWRITE and r.rewrite_template == pub.REWRITE_TEMPLATES[category]
                   for r in plan.rules)


def test_builtin_rows_are_detector_rules_with_identity_and_ordering() -> None:
    pub = _publisher()
    raw = pub.document_bytes("org", _payload(output_ip_leakage_action="redact",
                                             factuality_check_enabled=True))
    body = json.loads(raw)
    by_id = {r["rule_id"]: r for r in body["rules"]}
    assert "fw-exfil-output" not in by_id  # the legacy catch-all IP row is gone
    ip = by_id["out_ip_leak"]
    assert ip["category"] == "ip_leak" and ip["priority"] == 200
    assert ip["condition"]["detector_class"] == "output.ip_leak" and ip["on_unavailable"] == "fail_closed"
    ground = by_id["out_hallucination"]
    assert ground["condition"]["detector_class"] == "output.grounding.lexical"
    assert ground["on_unavailable"] == "fail_open" and ground["action"] == "flag"
    assert by_id["fw-pii-output"]["priority"] == 100  # customer/firewall rows keep the stamp
    assert body["streaming_mode"] == "strict_withhold"  # D-20: redact on ip_leak cannot be held


def test_default_org_is_unchanged_incremental_with_no_exfil_or_canary() -> None:
    pub = _publisher()
    body = json.loads(pub.document_bytes("org", _payload()))
    ids = {r["rule_id"] for r in body["rules"]}
    assert body["streaming_mode"] == "incremental"
    assert "out_exfil" not in ids and "out_canary" not in ids
    plan, _ = _compile(pub.document_bytes("org", _payload()))
    assert plan.streaming_mode is StreamingMode.INCREMENTAL


def test_exfil_and_canary_are_emitted_only_for_listed_orgs(monkeypatch: pytest.MonkeyPatch) -> None:
    pub = _publisher()
    monkeypatch.setenv("AMF_V2_EXFIL_ORGS", "acme")
    monkeypatch.setenv("AMF_V2_EXFIL_ALLOW_HOSTS", "cdn.example.com, img.example.org")
    monkeypatch.setenv("AMF_V2_CANARY_ORGS", "acme")
    for org, listed in (("acme", True), ("other", False)):
        body, promoted = pub.document_body(org, _payload(), org_slug=org)
        ids = {r["rule_id"] for r in body["rules"]}
        assert ("out_exfil" in ids) is listed and ("out_canary" in ids) is listed
        assert (promoted == ["out_exfil"]) is listed  # canary alone does not promote (D-26)
        assert body["streaming_mode"] == ("strict_withhold" if listed else "incremental")
    body, _ = pub.document_body("acme", _payload(), org_slug="acme")
    exfil = next(r for r in body["rules"] if r["rule_id"] == "out_exfil")
    assert exfil["action"] == "block" and exfil["condition"]["allow_hosts"] == [
        "cdn.example.com", "img.example.org"]
    plan, document = _compile(json.dumps(body).encode())  # compiles through the real compiler
    assert plan.streaming_mode is StreamingMode.STRICT_WITHHOLD
    assert next(d for d in document.drafts if d.rule_id == "out_exfil").condition.allow_hosts == (
        "cdn.example.com", "img.example.org")
    monkeypatch.setenv("AMF_V2_EXFIL_ACTION", "bogus")
    exfil = next(r for r in pub.document_body("acme", _payload(), org_slug="acme")[0]["rules"]
                 if r["rule_id"] == "out_exfil")
    assert exfil["action"] == "block"  # an invalid action falls back to block
    monkeypatch.setenv("AMF_V2_CANARY_STRICT_ORGS", "acme")
    monkeypatch.delenv("AMF_V2_EXFIL_ORGS")
    mode, promoted = pub.document_body("acme", _payload(), org_slug="acme")[0]["streaming_mode"], None
    assert mode == "strict_withhold"  # the canary promotes only via V2_CANARY_STRICT_ORGS


def test_unmapped_category_customer_rows_compile_unchanged_and_are_never_promoted() -> None:
    pub = _publisher()
    rows = []
    for rule_id, cond in (
        ("pol-1-kw", {"kind": "keywords", "field": "", "direction": "output", "keywords": ["k"],
                      "pattern": "", "detector_class": "", "pipeline_stage": ""}),
        ("pol-1-rx", {"kind": "regex", "field": "", "direction": "output", "keywords": [],
                      "pattern": "ab+c", "detector_class": "", "pipeline_stage": ""}),
    ):
        for action in ("redact", "block"):
            rows.append({"rule_id": f"{rule_id}-{action}", "category": "exfil", "scope": "output",
                         "action": action, "mode": "enforce", "on_unavailable": "fail_closed",
                         "threshold": None, "priority": 300, "surfaces": ["chat"],
                         "condition": cond})
    body, promoted = pub.document_body("org", _payload(), rows)
    assert body["streaming_mode"] == "incremental" and promoted == []
    plan, _ = _compile(json.dumps(body).encode())
    assert {r.rule_id for r in plan.rules} >= {r["rule_id"] for r in rows}


def test_a_customer_row_in_the_reserved_output_namespace_is_reported_not_published() -> None:
    pub = _publisher()
    policy = {"code": "P", "policy_domain": "pipeline", "category": "exfil", "enabled": True,
              "rules": [{"id": 1, "name": "spoof", "action": "block", "enabled": True,
                         "rule_type": "detector", "condition": {"detector_class": "output.exfil"}}]}
    rows, unsupported = pub.policy_drafts([policy])
    assert rows == [] and unsupported == ["P/spoof (reserved detector class)"]


def test_map_rewrite_and_grounding_validation() -> None:
    mapping = _map()
    errors = mapping.selection_errors({"output_pii_action": "rewrite"}, {"output_pii_action": "redact"})
    assert set(errors) == {"output_pii_action"}  # default: not served by v2
    assert mapping.rewrite_served_by_v2("acme") is False and mapping.rewrite_served_by_v2(None) is False
    # The five fields are no longer unsupported/retired.
    for field in ("factuality_check_enabled", "hallucination_grounding_mode",
                  "hallucination_grounding_threshold", "hallucination_grounding_model",
                  "output_hallucination_action"):
        assert mapping.FIELDS[field]["kind"] == "mapped"
    assert "reject" not in mapping.FIELDS["output_ip_leakage_action"]
    bad = mapping.selection_errors(
        {"hallucination_grounding_threshold": 1.5, "hallucination_grounding_mode": "model",
         "hallucination_grounding_model": "x"},
        {"hallucination_grounding_threshold": 0.5, "hallucination_grounding_mode": "lexical"},
    )
    assert set(bad) == {"hallucination_grounding_threshold", "hallucination_grounding_mode"}
    assert "no backend advertises hallucination" in bad["hallucination_grounding_mode"]
    ok = mapping.selection_errors(
        {"hallucination_grounding_threshold": 0.4, "hallucination_grounding_mode": "lexical",
         "factuality_check_enabled": True, "output_hallucination_action": "block"},
        {},
    )
    assert ok == {}


def test_property_12_map_serializer_and_compiler_agree(monkeypatch: pytest.MonkeyPatch) -> None:
    """Feature: gw33-output-parity, Property 12: Map/serializer/compiler agreement. Validates 9.2, 12.9."""
    # Feature: gw33-output-parity, Property 12: Map/serializer/compiler agreement
    pub, mapping = _publisher(), _map()
    monkeypatch.setenv("AMF_V2_REWRITE_ORGS", "served")
    rng = random.Random(33_012)
    actions = ["allow", "flag", "redact", "rewrite", "block"]
    for _ in range(_ITERATIONS):
        payload = _payload(
            output_pii_action=rng.choice(actions), output_credential_action=rng.choice(actions),
            output_ip_leakage_action=rng.choice(actions),
            output_hallucination_action=rng.choice(actions),
            factuality_check_enabled=rng.random() < 0.7,
            hallucination_grounding_mode="lexical",
            hallucination_grounding_threshold=round(rng.random(), 3),
        )
        org = rng.choice(["served", "plain"])
        errors = mapping.selection_errors(payload, {}, org)
        if org == "plain" and "rewrite" in {
            payload["output_pii_action"], payload["output_credential_action"],
            payload["output_ip_leakage_action"], payload["output_hallucination_action"],
        }:
            assert errors  # refused at save
            continue
        assert errors == {}
        raw = pub.document_bytes("org", payload, org_slug=org)  # every accepted value compiles
        plan, document = _compile(raw)
        assert plan.rules
        strict_needed = any(
            r.condition is not None and r.condition.detector_class.startswith(
                ("output.ip_leak", "output.grounding", "output.exfil"))
            and r.action in (Action.REDACT, Action.REWRITE, Action.BLOCK)
            for r in plan.rules
        )
        assert (plan.streaming_mode is StreamingMode.STRICT_WITHHOLD) is strict_needed


def test_the_compiler_refuses_what_the_publisher_would_never_emit() -> None:
    with pytest.raises(CompileError):
        _compile(json.dumps({
            "org_id": "org", "streaming_mode": "incremental",
            "rules": [{"rule_id": "x", "category": "ip_leak", "mode": "enforce", "action": "rewrite",
                       "priority": 1, "scope": "output", "on_unavailable": "fail_closed",
                       "surfaces": ["chat"], "threshold": None, "rewrite_template": "[x]",
                       "condition": {"kind": "detector", "direction": "output",
                                     "detector_class": "output.ip_leak"}}],
        }).encode())
