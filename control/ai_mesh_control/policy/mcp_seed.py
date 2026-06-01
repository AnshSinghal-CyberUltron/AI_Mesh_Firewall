"""
Idempotent seeding of a baseline PII guardrail policy for the MCP domain.

Every organization gets one enabled, system-owned MCP policy
(``PII_MCP_<org_id>``) with redact rules for the most common PII classes
(credit card, SSN, email, phone). This guarantees the MCP Guardrail
Simulator and live tool calls detect & redact PII out-of-the-box, instead
of returning ALLOW / "No policies matched" until an operator hand-builds a
rule.

The same function backs both the ``seed_mcp_pii_policy`` management command
and the ``Organization`` post_save signal, so newly created orgs are seeded
automatically and existing orgs can be backfilled on demand.
"""

from __future__ import annotations

import logging

from django.db import transaction

logger = logging.getLogger(__name__)

# (preset key, rule action, direction, human label)
# direction "both" => evaluated on tool input args AND tool output.
_DEFAULT_RULES: list[tuple[str, str, str, str]] = [
    ("credit_card", "redact", "both", "Credit Card (Luhn)"),
    ("us_ssn", "redact", "both", "US Social Security Number"),
    ("email", "redact", "both", "Email Address"),
    ("phone", "redact", "both", "Phone Number"),
]


def policy_code_for_org(org_id: int) -> str:
    """Deterministic, globally-unique policy code for an org's baseline."""
    return f"PII_MCP_{org_id}"


@transaction.atomic
def seed_mcp_pii_policy(organization, *, reset_rules: bool = False):
    """
    Ensure ``organization`` has its baseline enabled MCP PII policy.

    Idempotent: safe to call repeatedly. Creates the Policy if missing and
    adds any missing preset rules. Existing operator edits are preserved
    unless ``reset_rules=True`` (which deletes & recreates the default rules
    — used by the management command's ``--reset`` flag).

    Returns ``(policy, created: bool)``.
    """
    from policy.models import Policy, Rule

    code = policy_code_for_org(organization.id)

    policy, created = Policy.objects.get_or_create(
        code=code,
        defaults={
            "name": "MCP PII Guardrail",
            "policy_domain": "mcp",
            "organization": organization,
            "category": "pii",
            "severity": "HIGH",
            "description": (
                "Baseline PII protection for MCP tool calls. Redacts credit "
                "card numbers, SSNs, emails and phone numbers in tool input "
                "arguments and tool responses. Auto-seeded; edit or disable "
                "rules as needed."
            ),
            "enabled": True,
            "is_system": True,
            "priority": 100,
        },
    )

    if reset_rules:
        policy.rules.all().delete()

    existing_presets = {
        (r.condition or {}).get("preset")
        for r in policy.rules.all()
        if (r.condition or {}).get("preset")
    }

    new_rules = []
    for idx, (preset_key, action, direction, label) in enumerate(_DEFAULT_RULES):
        if preset_key in existing_presets:
            continue
        new_rules.append(
            Rule(
                policy=policy,
                name=label,
                rule_type="regex",
                condition={
                    "preset": preset_key,
                    "direction": direction,
                    "scope": "entire",
                },
                action=action,
                redaction_config={},
                priority=len(_DEFAULT_RULES) - idx,
                enabled=True,
                description=f"Auto-seeded baseline rule for {label}.",
            )
        )
    if new_rules:
        Rule.objects.bulk_create(new_rules)
        logger.info(
            "Seeded %d MCP PII rule(s) for org=%s policy=%s",
            len(new_rules),
            organization.id,
            code,
        )

    return policy, created
