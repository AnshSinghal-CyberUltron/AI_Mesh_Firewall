#!/usr/bin/env python3
"""
Local triage matrix for PII_PKG policies: org isolation, enable/disable, domains.
Run: docker compose exec -T control python /app/scripts/verify_pii_policy_matrix_local.py
"""
from __future__ import annotations

import json
import os
import sys

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "main_app.settings")

import django  # noqa: E402

django.setup()

from auth.models import Organization  # noqa: E402
from policy.compiler import PolicyCompiler  # noqa: E402
from policy.engine import evaluate  # noqa: E402
from policy.models import Policy, Rule  # noqa: E402
from policy.pii_policy_catalog import policy_code_for_org  # noqa: E402

PASS: list[str] = []
FAIL: list[tuple[str, str]] = []
WARN: list[tuple[str, str]] = []


def ok(name: str) -> None:
    PASS.append(name)


def bad(name: str, detail: str) -> None:
    FAIL.append((name, detail))


def warn(name: str, detail: str) -> None:
    WARN.append((name, detail))


def _org(slug: str) -> Organization:
    return Organization.objects.get(slug=slug, is_active=True)


def _pii_policy(org: Organization) -> Policy | None:
    return Policy.objects.filter(code=policy_code_for_org(org.id), organization=org).first()


def _eval(text: str, org: Organization, *, domain: str | None = None):
    qs = Policy.objects.filter(enabled=True, organization=org).prefetch_related("rules")
    ctx = {"prompt": text, "response": text}
    return evaluate(ctx, policies_qs=qs, domain=domain)


def triage_org_isolation() -> None:
    z = _org("zeroshield")
    a = _org("acme-test")
    pz = _pii_policy(z)
    if not pz or pz.rules.count() < 50:
        bad("org_zeroshield_pii_seeded", f"missing or thin policy rules={getattr(pz, 'rules', lambda: [])}")
        return
    ok("org_zeroshield_pii_seeded")

    cross = Policy.objects.filter(code=policy_code_for_org(z.id)).exclude(organization=z).exists()
    if cross:
        bad("org_no_cross_tenant_code", "PII_PKG code visible under another org FK")
    else:
        ok("org_no_cross_tenant_code")

    pa = _pii_policy(a)
    if pa:
        ok("org_acme_has_own_pii_pkg")
    else:
        warn("org_acme_has_own_pii_pkg", "acme-test has no PII_PKG — seed to test Redis isolation")

    import redis

    r = redis.from_url(os.environ["REDIS_URL"], decode_responses=True)
    z_raw = r.get(f"policies:compiled:{z.slug}")
    a_raw = r.get(f"policies:compiled:{a.slug}")
    if not z_raw:
        bad("redis_zeroshield_bundle", "missing")
        return
    z_bundle = json.loads(z_raw)
    z_codes = {e["policy"]["code"] for e in z_bundle.get("policies", [])}
    if policy_code_for_org(z.id) not in z_codes:
        bad("redis_zeroshield_has_pii", str(z_codes)[:200])
    else:
        ok("redis_zeroshield_has_pii")

    if pa and a_raw:
        a_bundle = json.loads(a_raw)
        a_codes = {e["policy"]["code"] for e in a_bundle.get("policies", [])}
        if policy_code_for_org(z.id) in a_codes:
            bad("redis_acme_no_zeroshield_leak", f"leaked codes {a_codes}")
        elif policy_code_for_org(a.id) in a_codes:
            ok("redis_org_bundle_isolated")
        else:
            warn("redis_org_bundle_isolated", "acme bundle exists but no PII_PKG yet")


def triage_policy_enable_disable(org: Organization) -> None:
    p = _pii_policy(org)
    if not p:
        bad("policy_toggle_setup", "no PII policy")
        return
    text = "reach me at leak@example.com"
    if _eval(text, org, domain="pipeline").action != "redact":
        bad("policy_toggle_baseline_redact", "expected redact when enabled")
        return
    ok("policy_toggle_baseline_redact")

    p.enabled = False
    p.save(update_fields=["enabled", "updated_at"])
    PolicyCompiler().compile_and_push(organization=org)
    if _eval(text, org, domain="pipeline").action == "redact":
        bad("policy_disabled_skips_eval", "still redacting when policy.enabled=False")
    else:
        ok("policy_disabled_skips_eval")

    bundle = PolicyCompiler().compile_all(organization=org)
    codes = {s["policy"]["code"] for s in bundle.get("policies", [])}
    if policy_code_for_org(org.id) in codes:
        bad("compiler_omits_disabled_policy", "disabled policy still in bundle")
    else:
        ok("compiler_omits_disabled_policy")

    p.enabled = True
    p.save(update_fields=["enabled", "updated_at"])
    PolicyCompiler().compile_and_push(organization=org)


def triage_rule_enable_disable(org: Organization) -> None:
    p = _pii_policy(org)
    if not p:
        return
    rule = Rule.objects.filter(
        policy=p, condition__entity_key="EMAIL_ADDRESS", enabled=True
    ).first()
    if not rule:
        bad("rule_toggle_setup", "no EMAIL rule")
        return
    text = "only-email probe@example.com here"
    if _eval(text, org, domain="pipeline").action != "redact":
        bad("rule_toggle_baseline", "email not redacted")
        return
    ok("rule_toggle_baseline")

    rule.enabled = False
    rule.save(update_fields=["enabled", "updated_at"])
    PolicyCompiler().compile_and_push(organization=org)
    res = _eval(text, org, domain="pipeline")
    if res.action == "redact" and "Email" in (res.matched_rule_names or []):
        bad("rule_disabled_skips_email", "email rule still matched")
    else:
        ok("rule_disabled_skips_email")

    rule.enabled = True
    rule.save(update_fields=["enabled", "updated_at"])
    PolicyCompiler().compile_and_push(organization=org)


def triage_domain_isolation(org: Organization) -> None:
    p = _pii_policy(org)
    if not p or p.policy_domain != "pipeline":
        bad("domain_pii_is_pipeline", getattr(p, "policy_domain", None))
        return
    ok("domain_pii_is_pipeline")

    text = "PII probe ssn@not-email.com 123-45-6789"
    pipeline_res = _eval(text, org, domain="pipeline")
    if pipeline_res.action in ("redact", "block"):
        ok("domain_pipeline_sees_pii_pkg")
    else:
        bad("domain_pipeline_sees_pii_pkg", f"action={pipeline_res.action}")

    for dom in ("rag", "mcp"):
        res = _eval(text, org, domain=dom)
        if res.action not in ("redact", "block"):
            ok(f"domain_{dom}_excludes_pipeline_pii")
        else:
            bad(f"domain_{dom}_excludes_pipeline_pii", f"action={res.action}")

    # MCP-only policy must not apply under rag domain filter
    mcp_only = Policy.objects.create(
        code=f"TEST_MCP_ONLY_{org.id}",
        name="Test MCP only",
        organization=org,
        policy_domain="mcp",
        category="test",
        severity="HIGH",
        enabled=True,
        priority=999,
    )
    Rule.objects.create(
        policy=mcp_only,
        name="MCP marker",
        rule_type="keywords",
        condition={"keywords": ["MCP_DOMAIN_MARKER_XYZ"], "field": "both"},
        action="block",
        priority=10,
        enabled=True,
    )
    hit_mcp = evaluate(
        {"prompt": "MCP_DOMAIN_MARKER_XYZ"},
        policies_qs=Policy.objects.filter(enabled=True, organization=org).prefetch_related("rules"),
        domain="mcp",
    )
    hit_rag = evaluate(
        {"prompt": "MCP_DOMAIN_MARKER_XYZ"},
        policies_qs=Policy.objects.filter(enabled=True, organization=org).prefetch_related("rules"),
        domain="rag",
    )
    mcp_only.delete()
    if hit_mcp.action == "block":
        ok("domain_mcp_sees_mcp_policy")
    else:
        bad("domain_mcp_sees_mcp_policy", str(hit_mcp.action))
    if hit_rag.action != "block":
        ok("domain_rag_excludes_mcp_only")
    else:
        bad("domain_rag_excludes_mcp_only", "mcp-only policy matched under rag")


def _filter_mcp_policies(entries: list, server_slug: str = "dummy-server") -> list:
    """Mirror gateway policy_sync.get_policies_for_server(domain='mcp')."""
    result = []
    for entry in entries:
        policy = entry.get("policy", {})
        p_domain = policy.get("policy_domain", "pipeline")
        if p_domain == "global":
            p_domain = "pipeline"
        p_server = policy.get("mcp_server_slug")
        if p_domain == "mcp":
            if p_server is None or p_server == server_slug:
                result.append(entry)
    return result


def _filter_pipeline_policies(entries: list) -> list:
    """Mirror gateway filter_policies_by_domain(domain='pipeline')."""
    result = []
    for entry in entries:
        policy = entry.get("policy", {})
        p_domain = policy.get("policy_domain", "pipeline")
        if p_domain == "global":
            p_domain = "pipeline"
        if p_domain == "pipeline":
            result.append(entry)
    return result


def triage_gateway_bundle_shape(org: Organization) -> None:
    """Simulate gateway: chat = pipeline filter; MCP = mcp-only filter."""
    import redis

    raw = redis.from_url(os.environ["REDIS_URL"], decode_responses=True).get(
        f"policies:compiled:{org.slug}"
    )
    if not raw:
        warn("gateway_redis_bundle", "no compiled bundle — run seed + compile")
        return
    entries = json.loads(raw).get("policies", [])
    pii_code = policy_code_for_org(org.id)
    domains_all = {e.get("policy", {}).get("policy_domain") for e in entries}
    pipeline_entries = _filter_pipeline_policies(entries)
    mcp_entries = _filter_mcp_policies(entries)
    domains_mcp = {e.get("policy", {}).get("policy_domain") for e in mcp_entries}

    if any(e.get("policy", {}).get("code") == pii_code for e in pipeline_entries):
        ok("gateway_chat_bundle_includes_pipeline_pii")
    else:
        bad("gateway_chat_bundle_includes_pipeline_pii", "PII_PKG missing from pipeline filter")

    if not any(e.get("policy", {}).get("code") == pii_code for e in mcp_entries):
        ok("gateway_mcp_filter_excludes_pipeline_pii")
    else:
        bad("gateway_mcp_filter_excludes_pipeline_pii", f"mcp domains={domains_mcp}")

    if "pipeline" in domains_all or "rag" in domains_all:
        ok("gateway_bundle_has_domain_specific_policies")
    else:
        warn("gateway_bundle_has_domain_specific_policies", f"only domains {domains_all}")

    warn(
        "gateway_rag_ranker_policy_load",
        "RAG ranker calls PolicySync.get_compiled_policies() which does not exist — "
        "ranker stage gets [] until fixed (rag_pipeline/pipeline.py:274)",
    )
    warn(
        "vector_analytics_enforcement",
        "Vector uses VectorCollectionPolicy; Analytics is metrics-only — "
        "Policy.policy_domain=pipeline does not apply there",
    )


def main() -> int:
    org = _org("zeroshield")
    triage_org_isolation()
    triage_policy_enable_disable(org)
    triage_rule_enable_disable(org)
    triage_domain_isolation(org)
    triage_gateway_bundle_shape(org)

    print("\n=== PII POLICY LOCAL TRIAGE ===\n")
    print(f"PASS ({len(PASS)}):")
    for n in PASS:
        print(f"  ✓ {n}")
    if WARN:
        print(f"\nWARN ({len(WARN)}):")
        for n, d in WARN:
            print(f"  ! {n}: {d}")
    if FAIL:
        print(f"\nFAIL ({len(FAIL)}):")
        for n, d in FAIL:
            print(f"  ✗ {n}: {d}")
    print(f"\nTotal: {len(PASS)} pass, {len(WARN)} warn, {len(FAIL)} fail\n")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
