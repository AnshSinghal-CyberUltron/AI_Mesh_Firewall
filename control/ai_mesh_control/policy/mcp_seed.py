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

# Phase 2b (2026-07-23) — collapse-to-one-surface, control-plane seeding.
# The pure (Django-free) rule-generation lives in ``mcp_seed_rules`` so the gateway's
# pre-cutover DIFF-GATE can replay the SAME logic without importing the ORM. Re-exported
# here so existing callers/tests (``from policy.mcp_seed import _detector_rules_for_server``)
# keep working unchanged.
from policy.mcp_seed_rules import (  # noqa: F401  (re-exported for callers/tests)
    _ACTION_RANK,
    _augment_injection_specs,
    _DEFAULT_RULES,
    _detector_rules_for_server,
    _ENFORCING_ACTIONS,
    _per_tool_specs,
    _resolved_dir_action,
    _rule_sig,
    _SEED_MARKER,
    detector_policy_code,
    policy_code_for_org,
)

logger = logging.getLogger(__name__)


@transaction.atomic
def seed_mcp_detector_policies(organization, *, reset_rules: bool = False):
    """Seed a system ``detector`` policy per active MCP server, replicating its CURRENT
    effective enforcement (posture + Tier-1 scan-control actions). Idempotent; preserves
    operator edits unless ``reset_rules=True``. Returns a list of ``(code, created, n_rules)``.

    Posture stays live as the safety net through Phase 2 — these seeded policies enforce the
    SAME action at the SAME scope the posture/scan-control already do, so the extra policy
    lane is behavior-identical (idempotent redaction) until Phase 3 retires the posture."""
    from policy.models import Policy, Rule
    from mcp_connector.models import (
        MCPScanControl,
        MCPServerRegistration,
        MCPToolRegistration,
    )
    from mcp_connector.scan_controls import resolve_effective_controls, serialize_control

    all_rows = [serialize_control(c) for c in MCPScanControl.objects.filter(organization=organization)]
    results: list[tuple[str, bool, int]] = []

    for server in MCPServerRegistration.objects.filter(organization=organization, is_active=True):
        sid = str(server.id)
        posture = server.default_scan_action
        rows = all_rows  # resolve_effective_controls scopes by server_id/tool_name internally
        effective = resolve_effective_controls(rows, server_id=sid, tool_name="")
        specs = _detector_rules_for_server(posture, effective)

        # Per-tool overrides (#2 raised / #5 lowered): the tools an operator gave a different
        # action via MCPToolRegistration.scan_action or a tool-scoped MCPScanControl.
        tool_actions: dict[str, str] = {}
        for treg in MCPToolRegistration.objects.filter(server=server, enabled=True):
            tool_actions[treg.tool_name] = treg.scan_action
        for r in rows:
            if (r.get("scope_type") == "tool" and str(r.get("server_id")) == sid
                    and (r.get("tool_name") or "")):
                tool_actions.setdefault(r["tool_name"], "inherit")
        specs = specs + _per_tool_specs(rows, sid, posture, tool_actions)
        specs = _augment_injection_specs(specs)  # #4: block postures also block injection

        code = detector_policy_code(organization.id, server.id)

        if not specs:
            # Nothing enforcing to replicate now (tag posture, no enforcing scan-control).
            # RECONCILE (red-team #6): a stale seeded policy from a PRIOR enforcing config must
            # not keep enforcing — retire its auto-seeded rules regardless of ``reset_rules``
            # (a posture redact→tag downgrade would otherwise leave the old redact rule live).
            existing = Policy.objects.filter(code=code, organization=organization).first()
            if existing:
                stale = [r for r in existing.rules.all()
                         if reset_rules or (r.description or "").startswith(_SEED_MARKER)]
                for r in stale:
                    r.delete()
            continue

        policy, created = Policy.objects.get_or_create(
            code=code,
            defaults={
                "name": f"MCP Detector Guardrail — {server.name}",
                "policy_domain": "mcp",
                "organization": organization,
                "mcp_server": server,
                "category": "detector",
                "severity": "HIGH",
                "description": (
                    "Auto-seeded (Phase 2) detector-class guardrail replicating this MCP "
                    "server's posture + scan-control enforcement so no coverage is lost when "
                    "the posture is retired. Edit or disable as needed."
                ),
                "enabled": True,
                "is_system": True,
                "priority": 90,
            },
        )
        if reset_rules:
            policy.rules.all().delete()

        # RECONCILE (red-team #6): re-seed must converge to the CURRENT config, not append.
        # Delete AUTO-SEEDED rules whose (action, condition) is no longer in the current spec
        # set (a stale rule from a prior posture/scan-control config would otherwise keep
        # enforcing the OLD action — drift / over- or under-enforcement). Operator-added rules
        # (without the seed marker) are preserved.
        current_sigs = {_rule_sig(s["action"], s["condition"], s.get("target_tool", "")) for s in specs}
        existing_sigs = set()
        for r in policy.rules.all():
            sig = _rule_sig(r.action, r.condition or {}, r.target_tool or "")
            if (r.description or "").startswith(_SEED_MARKER):
                if sig not in current_sigs:
                    r.delete()  # stale seeded rule → retire it
                    continue
            existing_sigs.add(sig)

        new_rules = []
        for idx, spec in enumerate(specs):
            tt = spec.get("target_tool", "")
            sig = _rule_sig(spec["action"], spec["condition"], tt)
            if sig in existing_sigs:
                continue
            if (spec["condition"] or {}).get("exempt"):
                name = f"Exempt {tt} (observe-only)"
            else:
                name = (f"Detector {spec['condition'].get('detector_class', 'all')} "
                        f"({spec['condition'].get('direction', 'both')})"
                        + (f" @ {tt}" if tt else ""))
            new_rules.append(Rule(
                policy=policy,
                name=name,
                rule_type="detector",
                condition=spec["condition"],
                action=spec["action"],
                target_tool=tt,
                redaction_config={},
                priority=len(specs) - idx,
                enabled=True,
                description=f"{_SEED_MARKER} (replicates posture/scan-control).",
            ))
        if new_rules:
            Rule.objects.bulk_create(new_rules)
        results.append((code, created, len(new_rules)))
        logger.info("Seeded MCP detector policy %s for org=%s server=%s (%d new rules)",
                    code, organization.id, server.id, len(new_rules))

    return results


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
