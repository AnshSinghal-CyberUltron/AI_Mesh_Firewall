"""
Idempotent, DEFAULT-OFF seeding of the built-in detection-family policy packages
for an organization (policy-driven-detection task 5.2).

Re-homes the gateway's previously-built-in Tier-1 detection library (the
``ATTACK_PATTERNS`` families + the PII/secret set) as per-org, toggleable system
``Policy`` packages — one package per family from :mod:`policy.builtin_packs_catalog`.

Modeled on :mod:`policy.ciso_seed` (which seeds ``enabled=True``). The ONE
material difference required by Requirement 4.2 / task 4.2: every re-homed family
package is seeded **``enabled=False``**. Seeding a package therefore never — by
itself — causes any detection; the organization is unprotected until it explicitly
enables a package. Because :meth:`policy.compiler.PolicyCompiler.compile_all` only
compiles ``Policy.objects.filter(enabled=True, ...)``, a seeded-but-disabled family
contributes no rules to the org's compiled bundle (the default-OFF invariant).

Idempotency mirrors ``ciso_seed``: one ``Policy`` per family keyed by the per-org
``code`` (``policy_code_for_org(org_id, family_key)``) via ``get_or_create``; rules
are de-duplicated by their catalog identity (family + rule name recorded in
``condition``) so re-running the seeder adds no duplicate ``Policy`` or ``Rule``
rows and preserves counts.
"""

from __future__ import annotations

import logging

from django.db import transaction

from policy.builtin_packs_catalog import (
    FAMILIES,
    PACKAGE_ID,
    build_family_rule_dicts,
    family_metadata,
    policy_code_for_org,
)

logger = logging.getLogger(__name__)

# Severity per family (mirrors the built-ins' enforcement intent; block families
# are CRITICAL, the redact-by-default PII/secret family is HIGH).
_FAMILY_SEVERITY: dict[str, str] = {
    "prompt_injection": "CRITICAL",
    "jailbreak": "CRITICAL",
    "command_injection": "CRITICAL",
    "sql_injection": "CRITICAL",
    "data_leakage": "CRITICAL",
    "path_traversal": "HIGH",
    "goal_hijacking": "HIGH",
    "tool_overreach": "CRITICAL",
    "vector_injection": "HIGH",
    "pii_secret": "HIGH",
}


def _rule_identity(spec: dict) -> tuple[str | None, str | None]:
    """Stable identity for a catalog rule: (family, rule name).

    The catalog records ``family`` in ``condition`` (see
    ``builtin_packs_catalog._condition_for``); the rule ``name`` is unique within
    a family. Together they identify a rule across re-seeds without a schema field.
    """
    cond = spec.get("condition") or {}
    return cond.get("family"), spec.get("name")


@transaction.atomic
def seed_builtin_family_package(organization, family, *, reset_rules: bool = False):
    """
    Ensure ``organization`` has the DEFAULT-OFF system ``Policy`` for one family.

    ``family`` is a :class:`policy.builtin_packs_catalog.BuiltinFamily`.

    Returns ``(policy, created: bool, rules_added: int)``.

    The package is created with ``is_system=True`` and — the key difference from
    ``ciso_seed`` — ``enabled=False`` (Requirement 4.2). An existing package's
    ``enabled`` state is left UNTOUCHED on re-seed so an operator who has turned a
    package ON is not silently reverted to OFF by a re-run.
    """
    from policy.models import Policy, Rule

    code = policy_code_for_org(organization.id, family.key)
    meta = family_metadata(family)
    severity = _FAMILY_SEVERITY.get(family.key, "MEDIUM")

    policy, created = Policy.objects.get_or_create(
        code=code,
        organization=organization,
        defaults={
            "name": f"Built-in: {family.name}",
            "policy_domain": "pipeline",
            "category": family.category,
            "severity": severity,
            "description": family.description,
            # KEY DIFFERENCE FROM ciso_seed: seed DISABLED (Requirement 4.2).
            "enabled": False,
            "is_system": True,
            "priority": 300,
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
        logger.info(
            "Reset built-in family package: removed %d rule(s) for policy=%s",
            deleted,
            code,
        )

    existing = {
        _rule_identity({"condition": r.condition, "name": r.name})
        for r in policy.rules.all()
    }

    new_rules: list[Rule] = []
    for spec in build_family_rule_dicts(family):
        if _rule_identity(spec) in existing:
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
            "Seeded %d built-in rule(s) for org=%s family=%s policy=%s (enabled=%s)",
            len(new_rules),
            organization.id,
            family.key,
            code,
            policy.enabled,
        )

    return policy, created, len(new_rules)


@transaction.atomic
def seed_builtin_packs(organization, *, reset_rules: bool = False):
    """
    Seed EVERY built-in family package for ``organization`` (all DEFAULT-OFF).

    Idempotent: re-running does not duplicate ``Policy`` or ``Rule`` rows.

    Returns a summary dict::

        {
          "package_id": PACKAGE_ID,
          "families": [
            {"family": key, "code": ..., "created": bool,
             "rules_added": int, "rule_count": int, "enabled": bool},
            ...
          ],
          "policies_created": int,
          "rules_added": int,
        }
    """
    results: list[dict] = []
    policies_created = 0
    rules_added = 0

    for family in FAMILIES:
        policy, created, added = seed_builtin_family_package(
            organization, family, reset_rules=reset_rules
        )
        policies_created += int(created)
        rules_added += added
        results.append(
            {
                "family": family.key,
                "code": policy.code,
                "created": created,
                "rules_added": added,
                "rule_count": policy.rules.count(),
                "enabled": policy.enabled,
            }
        )

    return {
        "package_id": PACKAGE_ID,
        "families": results,
        "policies_created": policies_created,
        "rules_added": rules_added,
    }


def compile_organization_policies(organization) -> dict:
    """Compile and push the org policy bundle to Redis (POLICY_SYNC).

    Mirrors :func:`policy.ciso_seed.compile_organization_policies`. Note that a
    freshly-seeded org gains NO compiled rules from these packages because every
    family is seeded ``enabled=False`` and the compiler only includes enabled
    policies — the default-OFF posture is preserved end to end.
    """
    from policy.compiler import PolicyCompiler

    compiler = PolicyCompiler()
    bundle = compiler.compile_all(organization=organization)
    ok = compiler.push_to_redis(bundle, organization=organization)
    return {
        "ok": ok,
        "rule_count": bundle.get("rule_count", 0),
        "policy_count": bundle.get("policy_count", 0),
    }
