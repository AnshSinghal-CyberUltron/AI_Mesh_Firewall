"""
Idempotent seeding of the CISO 100-rule policy package for an organization.
"""

from __future__ import annotations

import logging

from django.db import transaction

from policy.ciso_policy_catalog import (
    PACKAGE_ID,
    build_rule_dicts,
    package_metadata,
    policy_code_for_org,
)

logger = logging.getLogger(__name__)


@transaction.atomic
def seed_ciso_policy_package(organization, *, reset_rules: bool = False):
    """
    Ensure ``organization`` has the CISO 100-rule policy package.

    Returns ``(policy, created: bool, rules_added: int)``.
    """
    from policy.models import Policy, Rule

    code = policy_code_for_org(organization.id)
    meta = package_metadata()

    policy, created = Policy.objects.get_or_create(
        code=code,
        defaults={
            "name": "CISO 100 Enterprise Guardrails",
            "policy_domain": "pipeline",
            "organization": organization,
            "category": "ciso",
            "severity": "CRITICAL",
            "description": (
                "Organization-scoped CISO 100-rule package covering OWASP LLM Top-10, "
                "GDPR/PCI/HIPAA regulated data, secrets, IP exfiltration, jailbreak, "
                "toxicity, unauthorized advice, brand integrity, and sector packs. "
                "Seeded from docs/policies/CISO_100.md."
            ),
            "enabled": True,
            "is_system": True,
            "priority": 400,
            "metadata": meta,
        },
    )

    if not created:
        merged = {**(policy.metadata or {}), **meta}
        if merged != policy.metadata:
            policy.metadata = merged
            policy.save(update_fields=["metadata", "updated_at"])

    if reset_rules:
        deleted, _ = policy.rules.all().delete()
        logger.info("Reset CISO package: removed %d rule(s) for policy=%s", deleted, code)

    existing_ids = {
        (r.condition or {}).get("ciso_rule_id")
        for r in policy.rules.all()
        if (r.condition or {}).get("ciso_rule_id")
    }

    catalog = build_rule_dicts()
    new_rules: list[Rule] = []
    for spec in catalog:
        ciso_id = (spec.get("condition") or {}).get("ciso_rule_id")
        if ciso_id and ciso_id in existing_ids:
            continue
        new_rules.append(
            Rule(
                policy=policy,
                name=spec["name"],
                rule_type=spec["rule_type"],
                condition=spec["condition"],
                action=spec["action"],
                redaction_config=spec.get("redaction_config") or {},
                priority=spec.get("priority", 0),
                enabled=spec.get("enabled", True),
                description=spec.get("description", ""),
            )
        )

    if new_rules:
        Rule.objects.bulk_create(new_rules)
        logger.info(
            "Seeded %d CISO package rule(s) for org=%s policy=%s package=%s",
            len(new_rules),
            organization.id,
            code,
            PACKAGE_ID,
        )

    return policy, created, len(new_rules)


def compile_organization_policies(organization) -> dict:
    """Compile and push the org policy bundle to Redis (POLICY_SYNC)."""
    from policy.compiler import PolicyCompiler

    compiler = PolicyCompiler()
    bundle = compiler.compile_all(organization=organization)
    ok = compiler.push_to_redis(bundle, organization=organization)
    return {
        "ok": ok,
        "rule_count": bundle.get("rule_count", 0),
        "policy_count": bundle.get("policy_count", 0),
    }
