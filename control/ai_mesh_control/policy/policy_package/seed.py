"""
Idempotent seeding of the organization policy package.

Creates 50+ org-scoped policies (each a category with multiple rules) across
the pipeline / rag / mcp domains (Policy + Rule) plus the vector domain
(VectorCollectionPolicy), then compiles both bundles to Redis for the gateway.

Safe to re-run: policies are keyed by a stable per-org code and rules by a
stable ``rule_key`` in their condition, so re-running adds only what is missing.
"""

from __future__ import annotations

import logging

from django.db import transaction

from .catalog import (
    PACKAGE_ID,
    build_rule_dicts,
    package_metadata,
    policy_code,
    policy_specs,
    vector_specs,
)

logger = logging.getLogger(__name__)

_SEVERITY_PRIORITY = {"CRITICAL": 400, "HIGH": 300, "MEDIUM": 200, "LOW": 100}


def _org_project_id(organization) -> str:
    """Display label stored on VectorCollectionPolicy.project_id (the org slug).

    This is DISPLAY-ONLY: vector-policy enforcement is keyed by organization_id,
    not by project_id (the gateway authenticates with a 'simulator-{slug}' key
    whose project_id does not equal the slug), so this value is not load-bearing.
    """
    return organization.slug


@transaction.atomic
def seed_policy_package(organization, *, reset: bool = False) -> dict:
    """
    Ensure ``organization`` has the full policy package.

    Returns a summary dict with counts.
    """
    from policy.models import Policy, Rule
    from policy.vector_models import VectorCollectionPolicy

    meta = package_metadata()
    summary = {
        "policies_created": 0,
        "policies_existing": 0,
        "rules_added": 0,
        "rules_updated": 0,
        "vector_created": 0,
        "vector_updated": 0,
    }

    # ── Policy + Rule domains (pipeline / rag / mcp) ──────────────────────
    for spec in policy_specs():
        code = policy_code(organization.id, spec["key"])
        defaults = {
            "name": spec["name"],
            "policy_domain": spec["domain"],
            "organization": organization,
            "category": spec.get("category", ""),
            "severity": spec["severity"],
            "description": spec.get("description", ""),
            "enabled": True,
            "is_system": True,
            "priority": _SEVERITY_PRIORITY.get(spec["severity"], 200),
            "metadata": {
                "package_id": PACKAGE_ID,
                "package_key": spec["key"],
                "frameworks": spec.get("frameworks", []),
            },
            "redaction_fields": list(spec.get("redaction_fields") or []),
        }
        policy, created = Policy.objects.get_or_create(code=code, defaults=defaults)
        if created:
            summary["policies_created"] += 1
        else:
            summary["policies_existing"] += 1
            # Keep redaction_fields + metadata fresh without clobbering edits.
            changed = False
            if not policy.redaction_fields and defaults["redaction_fields"]:
                policy.redaction_fields = defaults["redaction_fields"]
                changed = True
            merged = {**(policy.metadata or {}), **defaults["metadata"]}
            if merged != policy.metadata:
                policy.metadata = merged
                changed = True
            if changed:
                policy.save(update_fields=["redaction_fields", "metadata", "updated_at"])

        if reset:
            policy.rules.all().delete()

        existing_by_key = {
            (r.condition or {}).get("rule_key"): r
            for r in policy.rules.all()
            if (r.condition or {}).get("rule_key")
        }

        new_rules: list[Rule] = []
        for rd in build_rule_dicts(spec):
            existing = existing_by_key.get(rd["_rule_key"])
            if existing is not None:
                # Keep package rules in sync (e.g. PEM block→redact) without
                # requiring --reset. Operator-added rules without rule_key
                # are left untouched.
                changed_fields: list[str] = []
                if existing.name != rd["name"]:
                    existing.name = rd["name"]
                    changed_fields.append("name")
                if existing.action != rd["action"]:
                    existing.action = rd["action"]
                    changed_fields.append("action")
                if (existing.redaction_config or {}) != (rd["redaction_config"] or {}):
                    existing.redaction_config = rd["redaction_config"]
                    changed_fields.append("redaction_config")
                if (existing.condition or {}) != (rd["condition"] or {}):
                    existing.condition = rd["condition"]
                    changed_fields.append("condition")
                if existing.description != rd["description"]:
                    existing.description = rd["description"]
                    changed_fields.append("description")
                if changed_fields:
                    existing.save(update_fields=[*changed_fields, "updated_at"])
                    summary["rules_updated"] += 1
                continue
            new_rules.append(
                Rule(
                    policy=policy,
                    name=rd["name"],
                    rule_type=rd["rule_type"],
                    condition=rd["condition"],
                    action=rd["action"],
                    redaction_config=rd["redaction_config"],
                    priority=rd["priority"],
                    enabled=rd["enabled"],
                    pipeline_stage=rd["pipeline_stage"],
                    target_tool=rd["target_tool"],
                    description=rd["description"],
                )
            )
        if new_rules:
            Rule.objects.bulk_create(new_rules)
            summary["rules_added"] += len(new_rules)

    # ── Vector domain (VectorCollectionPolicy) ───────────────────────────
    project_id = _org_project_id(organization)
    for vspec in vector_specs():
        defaults = {
            "name": vspec["name"],
            "namespace": vspec.get("namespace", "") or "",
            "default_action": vspec["default_action"],
            "allowed_operations": list(vspec.get("allowed_operations") or ["query"]),
            "max_results_per_query": int(vspec.get("max_results_per_query", 10)),
            "max_query_length": int(vspec.get("max_query_length", 2000)),
            "sensitive_fields": list(vspec.get("sensitive_fields") or []),
            "require_context_scan": bool(vspec.get("require_context_scan", True)),
            "block_sensitive_documents": bool(vspec.get("block_sensitive_documents", True)),
            "anomaly_distance_threshold": float(vspec.get("anomaly_distance_threshold", 0.85)),
            "enabled": True,
            "metadata": {
                "package_id": PACKAGE_ID,
                "package_key": vspec["key"],
                "severity": vspec.get("severity", "MEDIUM"),
                "frameworks": vspec.get("frameworks", []),
            },
        }
        _, created = VectorCollectionPolicy.objects.get_or_create(
            organization=organization,
            project_id=project_id,
            collection_name=vspec["collection_name"],
            vector_db_type=vspec["vector_db_type"],
            defaults=defaults,
        )
        if created:
            summary["vector_created"] += 1
        else:
            summary["vector_updated"] += 0  # leave operator edits untouched

    logger.info(
        "Seeded policy package for org=%s (%s): %s",
        organization.id,
        organization.slug,
        summary,
    )
    summary["meta"] = meta
    return summary


def compile_organization_policies(organization) -> None:
    """Compile + push both the policy bundle (org-scoped) and the vector bundle."""
    from policy.compiler import PolicyCompiler
    from policy.vector_compiler import VectorPolicyCompiler

    PolicyCompiler().compile_and_push(
        organization=organization, trigger="seed_policy_package"
    )
    # Vector bundle is global (all enabled vector policies); recompile once.
    VectorPolicyCompiler().compile_and_push(trigger="seed_policy_package")
