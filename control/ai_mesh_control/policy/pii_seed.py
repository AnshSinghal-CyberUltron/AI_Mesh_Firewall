"""
Idempotent seeding of the comprehensive PII policy package for an organization.

Creates one enabled pipeline policy (``PII_PKG_<org_id>``) with 50+ regex rules
from ``pii_policy_catalog``. Safe to run on local Docker and EC2 deploys.
"""

from __future__ import annotations

import logging

from django.db import transaction

from policy.pii_policy_catalog import (
    PACKAGE_ID,
    build_rule_dicts,
    package_metadata,
    policy_code_for_org,
)

logger = logging.getLogger(__name__)


@transaction.atomic
def seed_pii_policy_package(organization, *, reset_rules: bool = False):
    """
    Ensure ``organization`` has the comprehensive PII policy package.

    Returns ``(policy, created: bool, rules_added: int)``.
    """
    from policy.models import Policy, Rule

    code = policy_code_for_org(organization.id)
    meta = package_metadata()

    policy, created = Policy.objects.get_or_create(
        code=code,
        defaults={
            "name": "PII Detection & Redaction (Comprehensive)",
            "policy_domain": "pipeline",
            "organization": organization,
            "category": "pii",
            "severity": "HIGH",
            "description": (
                "Organization-scoped PII/PHI/PCI and secrets policy with 50+ "
                "regex rules (email, phone, SSN, payment cards, government IDs, "
                "medical identifiers, network addresses, API keys, JWTs, etc.). "
                "Seeded from the built-in PII policy package; edit rules as needed."
            ),
            "enabled": True,
            "is_system": True,
            "priority": 200,
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
        logger.info("Reset PII package: removed %d rule(s) for policy=%s", deleted, code)

    existing_keys = {
        (r.condition or {}).get("entity_key")
        for r in policy.rules.all()
        if (r.condition or {}).get("entity_key")
    }

    catalog = build_rule_dicts()
    new_rules: list[Rule] = []
    for spec in catalog:
        entity_key = (spec.get("condition") or {}).get("entity_key")
        if entity_key and entity_key in existing_keys:
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
            "Seeded %d PII package rule(s) for org=%s policy=%s package=%s",
            len(new_rules),
            organization.id,
            code,
            PACKAGE_ID,
        )

    return policy, created, len(new_rules)


def compile_organization_policies(organization) -> None:
    from policy.compiler import PolicyCompiler

    compiler = PolicyCompiler()
    compiler.compile_and_push(organization=organization)
