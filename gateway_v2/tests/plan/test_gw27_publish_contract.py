"""GW27: what the control plane publishes is what the gateway decodes, compiles and enforces."""
from __future__ import annotations

import json
import random
from collections.abc import Mapping
from types import SimpleNamespace
from typing import Any

import pytest

from gateway_v2.domain.plan import AuditControl, OrgControls, RateControl
from gateway_v2.plan.compiler import compile_plan
from gateway_v2.plan.document import decode_plan_body
from tests.plan._gw27_control import load

_BASE: dict[str, Any] = {
    "firewall_enabled": True,
    "content_filtering_enabled": True,
    "enforcement_mode": "block",
    "tier2_fail_closed_enabled": True,
    "rate_limit_enabled": True,
    "requests_per_minute": 600,
    "burst_limit": 20,
    "org_tpm_limit": 5000,
    "max_response_tokens": 1024,
    "audit_logging_enabled": True,
    "output_incident_logging_enabled": False,
    "retention_days": 45,
    "compliance_frameworks": ["SOC2", "ISO27001"],
    "blocked_keywords": "password, Secret, api_key",
    "threat_intel_enabled": True,
    "auto_block_threats": True,
    "threat_score_threshold": 60,
}


def _publish(payload: Mapping[str, Any], *, controls: bool = True, mode: str = "enforce") -> Any:
    mod = load()
    pub = mod.publish
    rows: list[dict[str, Any]] = []
    keyword = pub.keyword_row(payload, mode)
    if keyword is not None:
        rows, _ = pub.policy_drafts([keyword])
    return pub.document_bytes("org-a", payload, rows, controls_enabled=controls, rules_mode=mode)


def test_the_published_document_decodes_and_compiles_to_the_same_controls() -> None:
    raw = _publish(_BASE)
    document = decode_plan_body(raw)
    plan = compile_plan("org-a", document.drafts, compiled_at=1.0, controls=document.controls)
    assert plan.controls == OrgControls(
        rate=RateControl(True, 600, 20),
        org_tpm_limit=5000,
        max_response_tokens=1024,
        audit=AuditControl(True, False, 45),
        compliance_frameworks=("ISO27001", "SOC2"),
    )
    ids = {rule.rule_id for rule in plan.rules}
    assert {"fw-threat-prompt_injection-input", "fw-threat-jailbreak-input",
            "fw-threat-command-input", "pol-fw-blocked-keywords-keywords"} <= ids
    body = json.loads(raw)
    assert body["requires"] == ["controls/1"] and body["controls"]["version"] == 1


def test_controls_are_withheld_when_the_gate_is_closed() -> None:
    body = json.loads(_publish(_BASE, controls=False))
    assert "controls" not in body and "requires" not in body
    assert decode_plan_body(_publish(_BASE, controls=False)).controls == OrgControls()


def test_an_old_style_payload_publishes_exactly_as_before() -> None:
    mod = load().publish
    payload = {"firewall_enabled": True, "pii_detection_enabled": False,
               "output_pii_enabled": False, "jailbreak_detection_enabled": True}
    body = json.loads(mod.document_bytes("dev-org", payload))
    assert set(body) == {"org_id", "rules", "streaming_mode"}
    assert all(not r["rule_id"].startswith("fw-threat") for r in body["rules"])


# --- threat intel ---------------------------------------------------------------------------- #


def _threats(payload: Mapping[str, Any], **kw: Any) -> list[dict[str, Any]]:
    return load().controls.threat_drafts(payload, **kw)


def test_threat_rules_follow_the_three_fields() -> None:
    rows = _threats({**_BASE, "threat_score_threshold": 60})
    assert [r["rule_id"] for r in rows] == [
        "fw-threat-prompt_injection-input", "fw-threat-jailbreak-input", "fw-threat-command-input"
    ]
    assert {(r["action"], r["mode"], r["threshold"]) for r in rows} == {("block", "enforce", 0.6)}
    flagged = _threats({**_BASE, "auto_block_threats": False})
    assert {r["action"] for r in flagged} == {"flag"}
    assert _threats({**_BASE, "threat_score_threshold": 0})[0]["threshold"] == 0.0
    assert _threats({**_BASE, "threat_score_threshold": 100})[0]["threshold"] == 1.0
    assert _threats({**_BASE, "threat_intel_enabled": False}) == []
    assert _threats({**_BASE, "firewall_enabled": False}) == []
    assert _threats({**_BASE, "enforcement_mode": "monitor"})[0]["mode"] == "monitor"


def test_new_rules_mode_gates_the_threat_rows() -> None:
    assert _threats(_BASE, mode="off") == []
    row = _threats(_BASE, mode="monitor")[0]
    assert (row["action"], row["mode"]) == ("flag", "monitor")


def test_toggling_threat_intel_off_removes_only_the_threat_rules() -> None:
    pub = load().publish
    def ids(payload: Mapping[str, Any]) -> set[str]:
        body = json.loads(pub.document_bytes("o", payload, rules_mode="enforce"))
        return {r["rule_id"] for r in body["rules"]}

    on, off = ids(_BASE), ids({**_BASE, "threat_intel_enabled": False})
    names = ("prompt_injection", "jailbreak", "command")
    # F4/F3: each threat row also has its non-chat extension row; both go with the toggle.
    chat = {f"fw-threat-{c}-input" for c in names}
    assert on - off == chat | {f"{rid}-ext" for rid in chat}
    assert off <= on


# --- blocked keywords ------------------------------------------------------------------------ #


def _expected_keywords(text: str) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for part in text.split(","):
        word = part.strip()
        if word and word.casefold() not in seen:
            seen.add(word.casefold())
            out.append(word)
    return out


def _keyword_rule(text: str, **payload: Any) -> dict[str, Any] | None:
    pub = load().publish
    row = pub.keyword_row({**_BASE, "blocked_keywords": text, **payload}, "enforce")
    if row is None:
        return None
    drafts, _ = pub.policy_drafts([row])
    return drafts[0] if drafts else None


def test_keyword_rule_shape() -> None:
    rule = _keyword_rule("password, Secret, api_key, SECRET")
    assert rule is not None
    assert rule["rule_id"] == "pol-fw-blocked-keywords-keywords"
    assert (rule["scope"], rule["action"], rule["mode"]) == ("input", "block", "enforce")
    assert rule["condition"]["keywords"] == ["password", "Secret", "api_key"]
    assert rule["on_unavailable"] == "fail_closed"
    assert _keyword_rule("", ) is None and _keyword_rule(" , ,, ") is None
    assert _keyword_rule("a", firewall_enabled=False) is None
    assert _keyword_rule("a", content_filtering_enabled=False) is None


def test_keyword_rule_monitor_posture_and_case_insensitive_semantics() -> None:
    rule = _keyword_rule("token", enforcement_mode="monitor")
    assert rule is not None and (rule["mode"], rule["action"]) == ("monitor", "flag")
    from gateway_v2.detect.conditions import _keyword_spans  # shipped matcher semantics

    assert _keyword_spans(("token",), "my Tokenizer")


# Feature: gw27-firewall-config-fields, Property 9: keyword shape for any comma text
# Validates: Requirements 4.1
def test_property_keyword_shape() -> None:
    seed = 2709
    rng = random.Random(seed)
    alphabet = ["a", "B", "c", " ", ",", ",", "A", "b", "é", "x y"]
    for i in range(10_000):
        text = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 24)))
        rule = _keyword_rule(text)
        expected = _expected_keywords(text)
        if not expected:
            assert rule is None, (seed, i, text)
        else:
            assert rule is not None, (seed, i, text)
            assert rule["condition"]["keywords"] == expected, (seed, i, text)


# --- output policy --------------------------------------------------------------------------- #


def _policy(action: str, field: str) -> dict[str, Any]:
    return {
        "code": "p", "policy_domain": "pipeline", "enabled": True, "category": "pii", "priority": 5,
        "rules": [{"id": 1, "name": "r", "enabled": True, "rule_type": "keywords",
                   "action": action, "priority": 5, "pipeline_stage": "",
                   "condition": {"keywords": ["k"], "field": field}}],
    }


def _decide(action: str, field: str, **kw: Any) -> tuple[str, str, str] | None:
    drafts, _ = load().publish.policy_drafts([_policy(action, field)], **kw)
    return (drafts[0]["scope"], drafts[0]["action"], drafts[0]["mode"]) if drafts else None


@pytest.mark.parametrize(("own", "fallback", "want"), [
    ("block", "allow", ("block", "enforce")), ("redact", "block", ("redact", "enforce")),
    ("flag", "block", ("flag", "enforce")), ("allow", "block", ("allow", "enforce")),
    ("monitor", "redact", ("redact", "enforce")), ("monitor", "block", ("block", "enforce")),
    ("monitor", "flag", ("flag", "enforce")), ("monitor", "allow", ("flag", "monitor")),
    ("", "redact", ("redact", "enforce")), ("", "allow", ("allow", "enforce")),
])
def test_output_fallback_table(own: str, fallback: str, want: tuple[str, str]) -> None:
    got = _decide(own, "output", output_fallback=fallback)
    assert got is not None and got[1:] == want
    both = _decide(own, "both", output_fallback=fallback)
    assert both is not None and both[1:] == want


def test_output_fallback_leaves_input_rules_and_unknown_actions_alone() -> None:
    assert _decide("monitor", "input", output_fallback="block") == ("input", "flag", "monitor")
    assert _decide("", "input", output_fallback="block") is None  # legacy: omitted + listed
    drafts, unsupported = load().publish.policy_drafts([_policy("bogus", "output")])
    assert drafts == [] and unsupported
    drafts, unsupported = load().publish.policy_drafts([_policy("rewrite", "output")])
    # GW33: rewrite is built but per-org gated, so the omission names its reason.
    assert drafts == [] and unsupported == ["p/r (rewrite: not enabled for this org)"]


def test_output_policy_disabled_drops_output_and_keeps_the_input_half_of_both() -> None:
    assert _decide("block", "output", output_enabled=False) is None
    assert _decide("block", "both", output_enabled=False) == ("input", "block", "enforce")
    assert _decide("block", "input", output_enabled=False) == ("input", "block", "enforce")
    assert _decide("block", "both", output_enabled=True) == ("both", "block", "enforce")


def test_monitor_posture_forces_monitor_on_the_rows_the_fallback_decided() -> None:
    assert _decide("block", "output", output_fallback="block", monitor=True)[2] == "monitor"  # type: ignore[index]
    assert _decide("block", "input", output_fallback="block", monitor=True)[2] == "enforce"  # type: ignore[index]


# --- built_value_errors and the gates -------------------------------------------------------- #


def _errors(merged: Mapping[str, Any], changed: set[str] | None, **kw: Any) -> dict[str, str]:
    return load().controls.built_value_errors(merged, changed, **kw)


def test_rate_pair_is_refused_in_both_directions() -> None:
    on_with_zero = {"rate_limit_enabled": True, "requests_per_minute": 0, "burst_limit": 5}
    assert set(_errors(on_with_zero, {"rate_limit_enabled"})) == {"requests_per_minute"}
    assert set(_errors(on_with_zero, {"requests_per_minute"})) == {"requests_per_minute"}
    zero_burst = {**on_with_zero, "requests_per_minute": 5, "burst_limit": 0}
    assert set(_errors(zero_burst, {"rate_limit_enabled"})) == {"burst_limit"}
    off = {**on_with_zero, "rate_limit_enabled": False}
    assert _errors(off, {"rate_limit_enabled", "requests_per_minute"}) == {}
    assert _errors(on_with_zero, {"org_tpm_limit"}) == {}  # unchanged stored value is accepted


def test_retention_floor_pair_is_refused_in_both_directions() -> None:
    merged = {"retention_days": 90, "compliance_frameworks": ["PCI-DSS"]}
    assert set(_errors(merged, {"compliance_frameworks"})) == {"retention_days"}
    assert set(_errors(merged, {"retention_days"})) == {"retention_days"}
    assert _errors({**merged, "retention_days": 365}, {"retention_days"}) == {}
    soc2 = {"retention_days": 30, "compliance_frameworks": ["SOC2"]}
    assert _errors(soc2, {"retention_days"}) == {}
    assert _errors(merged, {"burst_limit"}) == {}


def test_value_limits() -> None:
    assert "org_tpm_limit" in _errors({"org_tpm_limit": -1}, {"org_tpm_limit"})
    assert _errors({"org_tpm_limit": 0}, {"org_tpm_limit"}) == {}
    assert "max_response_tokens" in _errors({"max_response_tokens": 0}, {"max_response_tokens"})
    assert "retention_days" in _errors({"retention_days": 0}, {"retention_days"})
    assert "threat_score_threshold" in _errors({"threat_score_threshold": 101}, None)
    many = ",".join(f"k{n}" for n in range(101))
    assert "blocked_keywords" in _errors({"blocked_keywords": many}, {"blocked_keywords"})
    assert "blocked_keywords" in _errors({"blocked_keywords": "x" * 129}, {"blocked_keywords"})
    assert "compliance_frameworks" in _errors({"compliance_frameworks": ["x"] * 17}, None)
    assert "compliance_frameworks" in _errors({"compliance_frameworks": ["bad\nlabel"]}, None)
    assert "compliance_frameworks" in _errors({"compliance_frameworks": "SOC2"}, None)
    assert "compliance_frameworks" in _errors({"compliance_frameworks": ["y" * 33]}, None)


def test_controls_backed_changes_are_refused_while_the_fleet_gate_is_closed() -> None:
    merged = {"requests_per_minute": 5, "blocked_keywords": "a"}
    closed = _errors(merged, {"requests_per_minute", "blocked_keywords"}, controls_ok=False)
    assert set(closed) == {"requests_per_minute"}
    assert "plan schema 1" in closed["requests_per_minute"]
    assert _errors(merged, {"blocked_keywords"}, controls_ok=False) == {}  # rules-backed: no gate
    assert _errors(merged, {"requests_per_minute"}, controls_ok=True) == {}


def test_controls_eligibility_reads_the_flag_and_org_list(monkeypatch: pytest.MonkeyPatch) -> None:
    ctl = load().controls
    for name in ("V2_PLAN_CONTROLS_ENABLED", "V2_PLAN_CONTROLS_ORGS", "V2_PLAN_NEW_RULES_MODE"):
        monkeypatch.delenv(name, raising=False)
    assert ctl.controls_eligible("a") is False and ctl.new_rules_mode() == "monitor"
    monkeypatch.setenv("V2_PLAN_CONTROLS_ENABLED", "true")
    assert ctl.controls_eligible("a") is False  # enabled but no org listed
    monkeypatch.setenv("V2_PLAN_CONTROLS_ORGS", "a, b")
    assert ctl.controls_eligible("a") and ctl.controls_eligible("b")
    assert not ctl.controls_eligible("c")
    monkeypatch.setenv("V2_PLAN_CONTROLS_ORGS", "*")
    assert ctl.controls_eligible("anything")
    monkeypatch.setenv("V2_PLAN_NEW_RULES_MODE", "enforce")
    assert ctl.new_rules_mode() == "enforce"
    monkeypatch.setenv("V2_PLAN_NEW_RULES_MODE", "nonsense")
    assert ctl.new_rules_mode() == "monitor"


def test_hipaa_with_audit_off_saves_and_the_violation_is_surfaced() -> None:
    mod = load()
    merged = {"compliance_frameworks": ["HIPAA"], "audit_logging_enabled": False,
              "retention_days": 365}
    assert mod.controls.built_value_errors(merged, {"audit_logging_enabled"}) == {}
    config = SimpleNamespace(**{**merged, "log_level": "full"}, **{
        k: True for k in ("pii_detection_enabled", "output_pii_enabled",
                          "output_credential_enabled", "output_incident_logging_enabled")})
    violations = mod.compliance.validate_compliance_requirements(config)
    assert any(v["field"] == "audit_logging_enabled" for v in violations)


# Feature: gw27-firewall-config-fields, Property 3: no BUILT field is stored and ignored
# Validates: Requirements 5.1
def test_property_every_built_field_reaches_the_document() -> None:
    seed = 2703
    rng = random.Random(seed)
    words = ["alpha", "Beta", "gamma", "alpha", "Delta"]
    pub = load().publish
    for i in range(10_000):
        payload = {
            **_BASE,
            "rate_limit_enabled": rng.random() < 0.7,
            "requests_per_minute": rng.randint(1, 10_000),
            "burst_limit": rng.randint(1, 1000),
            "org_tpm_limit": rng.choice((0, rng.randint(1, 10**6))),
            "max_response_tokens": rng.randint(1, 32_000),
            "audit_logging_enabled": rng.random() < 0.7,
            "output_incident_logging_enabled": rng.random() < 0.5,
            "retention_days": rng.randint(1, 365),
            "compliance_frameworks": rng.sample(["SOC2", "GDPR", "ISO27001"], rng.randint(0, 3)),
            "blocked_keywords": ",".join(rng.sample(words, rng.randint(0, 5))),
            "threat_intel_enabled": rng.random() < 0.7,
            "auto_block_threats": rng.random() < 0.5,
            "threat_score_threshold": rng.randint(0, 100),
        }
        body = json.loads(_publish(payload))
        ctx = (seed, i, payload)
        controls = body["controls"]
        rate = controls["rate"]
        assert (rate["enabled"], rate["requests_per_minute"], rate["burst"]) == (
            payload["rate_limit_enabled"], payload["requests_per_minute"], payload["burst_limit"]
        ), ctx
        assert controls.get("org_tpm_limit", 0) == payload["org_tpm_limit"], ctx
        assert controls["max_response_tokens"] == payload["max_response_tokens"], ctx
        assert controls["audit"] == {
            "enabled": payload["audit_logging_enabled"],
            "incident_logging": payload["output_incident_logging_enabled"],
            "retention_days": payload["retention_days"],
        }, ctx
        frameworks = controls.get("compliance", {}).get("frameworks", [])
        assert frameworks == sorted(payload["compliance_frameworks"]), ctx
        rules = {r["rule_id"]: r for r in body["rules"]}
        keywords = [r for rid, r in rules.items() if rid == "pol-fw-blocked-keywords-keywords"]
        assert [k["condition"]["keywords"] for k in keywords] == (
            [_expected_keywords(payload["blocked_keywords"])]
            if _expected_keywords(payload["blocked_keywords"]) else []
        ), ctx
        threat = [r for rid, r in rules.items() if rid.startswith("fw-threat-")]
        if payload["threat_intel_enabled"]:
            # F4/F3: three chat rows plus their three non-chat extension rows, same values.
            assert sorted(len(r["surfaces"]) for r in threat) == [1, 1, 1, 3, 3, 3], ctx
            assert {r["action"] for r in threat} == {
                "block" if payload["auto_block_threats"] else "flag"}, ctx
            want = payload["threat_score_threshold"] / 100
            assert {r["threshold"] for r in threat} == {want}, ctx
        else:
            assert threat == [], ctx
    for enabled in (True, False):  # output_policy_*: the two fields reach the policy rows
        drafts, _ = pub.policy_drafts(
            [_policy("monitor", "output")], output_enabled=enabled, output_fallback="redact"
        )
        assert bool(drafts) is enabled
        assert not enabled or drafts[0]["action"] == "redact"
