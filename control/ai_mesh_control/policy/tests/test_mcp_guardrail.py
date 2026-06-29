"""Tests for the revamped MCP guardrail policy engine.

Covers the new rule shape (preset × direction × scope × action), strict MCP
domain isolation, Luhn validation on the credit-card preset, per-key scope
targeting, and the per-org PII MCP seed.

Run inside the control container:
    cd /app/control && pytest ai_mesh_control/policy/tests/test_mcp_guardrail.py
"""

from __future__ import annotations

import pytest

from policy.engine import evaluate
from policy.models import Policy, Rule

VALID_CC = "4111111111111111"  # passes Luhn (13-19 digits)
INVALID_CC = "8929554991"  # 10 digits, fails the preset regex/Luhn gate


def _mk_policy(code, *, domain="mcp", **kwargs):
    return Policy.objects.create(
        name=kwargs.pop("name", code),
        code=code,
        policy_domain=domain,
        category=kwargs.pop("category", "pii"),
        severity=kwargs.pop("severity", "HIGH"),
        enabled=True,
        **kwargs,
    )


def _mk_rule(policy, *, preset=None, action="redact", direction="both", scope="entire",
             key="", regex=None, keywords=None, rule_type="regex", priority=0):
    cond: dict = {"direction": direction, "scope": scope}
    if preset:
        cond["preset"] = preset
    if key:
        cond["key"] = key
    if regex:
        cond["regex"] = regex
    if keywords:
        cond["keywords"] = keywords
    return Rule.objects.create(
        policy=policy,
        name=f"{policy.code}-{preset or regex or 'kw'}",
        rule_type="keywords" if keywords else rule_type,
        condition=cond,
        action=action,
        redaction_config={},
        priority=priority,
        enabled=True,
    )


@pytest.mark.django_db
def test_credit_card_preset_redacts_valid_card_in_input():
    policy = _mk_policy("MCP_CC_INPUT")
    _mk_rule(policy, preset="credit_card", action="redact", direction="input")

    qs = Policy.objects.filter(enabled=True).prefetch_related("rules")
    result = evaluate({"input_args": {"note": f"card {VALID_CC}"}}, policies_qs=qs, domain="mcp")

    assert result.action == "redact"
    assert policy.id in result.matched_policy_ids
    assert result.redaction_hints, "expected a redaction hint for the matched preset rule"


@pytest.mark.django_db
def test_credit_card_preset_ignores_invalid_card():
    policy = _mk_policy("MCP_CC_INVALID")
    _mk_rule(policy, preset="credit_card", action="redact", direction="input")

    qs = Policy.objects.filter(enabled=True).prefetch_related("rules")
    result = evaluate({"input_args": {"note": f"value {INVALID_CC}"}}, policies_qs=qs, domain="mcp")

    assert result.action == "allow"
    assert not result.matched_rule_ids


@pytest.mark.django_db
def test_direction_output_only_does_not_match_input():
    policy = _mk_policy("MCP_CC_OUTPUT")
    _mk_rule(policy, preset="credit_card", action="redact", direction="output")

    qs = Policy.objects.filter(enabled=True).prefetch_related("rules")
    # Card only present in the INPUT -> an output-only rule must not fire.
    res_in = evaluate({"input_args": {"x": VALID_CC}}, policies_qs=qs, domain="mcp")
    assert res_in.action == "allow"

    # Card present in the OUTPUT -> rule fires.
    res_out = evaluate({"output_data": {"x": VALID_CC}}, policies_qs=qs, domain="mcp")
    assert res_out.action == "redact"


@pytest.mark.django_db
def test_scope_key_targets_named_field_only():
    policy = _mk_policy("MCP_SSN_KEY")
    _mk_rule(policy, preset="us_ssn", action="redact", direction="input",
             scope="key", key="ssn")

    qs = Policy.objects.filter(enabled=True).prefetch_related("rules")
    # SSN under the targeted key -> match.
    hit = evaluate({"input_args": {"ssn": "123-45-6789"}}, policies_qs=qs, domain="mcp")
    assert hit.action == "redact"

    # Same SSN under a DIFFERENT key -> scope=key must not match.
    miss = evaluate({"input_args": {"other": "123-45-6789"}}, policies_qs=qs, domain="mcp")
    assert miss.action == "allow"


@pytest.mark.django_db
def test_pipeline_policy_does_not_apply_to_mcp_domain():
    pipeline = _mk_policy("PIPELINE_EMAIL", domain="pipeline")
    _mk_rule(pipeline, preset="email", action="redact", direction="both")

    qs = Policy.objects.filter(enabled=True).prefetch_related("rules")
    result = evaluate({"input_args": {"to": "a@b.com"}}, policies_qs=qs, domain="mcp")

    assert result.action == "allow"
    assert pipeline.id not in result.matched_policy_ids


@pytest.mark.django_db
def test_block_beats_redact_when_both_match():
    blk = _mk_policy("MCP_BLOCK", severity="CRITICAL")
    _mk_rule(blk, preset="credit_card", action="block", direction="both")
    red = _mk_policy("MCP_REDACT")
    _mk_rule(red, preset="credit_card", action="redact", direction="both")

    qs = Policy.objects.filter(enabled=True).prefetch_related("rules")
    result = evaluate({"input_args": {"cc": VALID_CC}}, policies_qs=qs, domain="mcp")

    assert result.action == "block"


@pytest.mark.django_db
def test_monitor_action_allows_but_records_match():
    policy = _mk_policy("MCP_MONITOR")
    _mk_rule(policy, preset="credit_card", action="monitor", direction="both")

    qs = Policy.objects.filter(enabled=True).prefetch_related("rules")
    result = evaluate({"input_args": {"cc": VALID_CC}}, policies_qs=qs, domain="mcp")

    assert result.action == "monitor"
    assert policy.id in result.matched_policy_ids


@pytest.mark.django_db
def test_custom_regex_rule_without_preset():
    policy = _mk_policy("MCP_CUSTOM_REGEX")
    _mk_rule(policy, regex=r"SECRET-\d{4}", action="block", direction="input")

    qs = Policy.objects.filter(enabled=True).prefetch_related("rules")
    hit = evaluate({"input_args": {"v": "SECRET-1234"}}, policies_qs=qs, domain="mcp")
    assert hit.action == "block"

    miss = evaluate({"input_args": {"v": "SECRET-XYZ"}}, policies_qs=qs, domain="mcp")
    assert miss.action == "allow"


@pytest.mark.django_db
def test_rag_domain_excludes_mcp_only_policy():
    policy = _mk_policy("MCP_ONLY_CC")
    _mk_rule(policy, preset="credit_card", action="redact", direction="both")

    qs = Policy.objects.filter(enabled=True).prefetch_related("rules")
    # Evaluating the RAG domain must NOT pick up an mcp-domain policy.
    result = evaluate({"input_args": {"cc": VALID_CC}}, policies_qs=qs, domain="rag")
    assert result.action == "allow"


@pytest.mark.django_db
def test_seed_mcp_pii_policy_is_idempotent():
    from auth.models import Organization
    from policy.mcp_seed import policy_code_for_org, seed_mcp_pii_policy

    org = Organization.objects.create(name="Seed Test Org", slug="seed-test-org")
    code = policy_code_for_org(org.id)

    # The post_save signal already seeds the policy on org creation, so the
    # policy exists exactly once with the baseline rules before we touch it.
    seeded = Policy.objects.get(code=code)
    assert seeded.policy_domain == "mcp"
    first_rule_count = seeded.rules.count()
    assert first_rule_count >= 4  # cc, ssn, email, phone baseline

    # An explicit re-run must be idempotent: no new policy, no duplicate rules.
    policy2, created2 = seed_mcp_pii_policy(org)
    assert created2 is False
    assert policy2.id == seeded.id
    assert Policy.objects.filter(code=code).count() == 1
    assert policy2.rules.count() == first_rule_count
