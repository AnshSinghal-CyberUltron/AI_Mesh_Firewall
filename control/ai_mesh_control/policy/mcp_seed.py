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

import json
import logging

from django.db import transaction

logger = logging.getLogger(__name__)


def _rule_sig(action: str, condition: dict, target_tool: str = "") -> tuple:
    """Hashable identity of a rule for reconcile/dedup — condition may hold list values
    (keywords), so JSON-serialize it (sorted) rather than hashing its items tuple. target_tool
    is part of the identity so a per-tool rule doesn't collide with the server-wide one."""
    return (action, target_tool or "", json.dumps(condition or {}, sort_keys=True, default=str))

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


def detector_policy_code(org_id: int, server_id) -> str:
    """Deterministic code for a server's Phase-2 seeded detector policy."""
    return f"MCP_DETECTOR_{org_id}_{server_id}"


# Phase 2b (2026-07-23) — collapse-to-one-surface, control-plane seeding.
# Replicate each MCP server's CURRENT effective enforcement (server posture + Tier-1
# scan-control actions) as a system ``detector`` policy, so that when Phase 3 retires the
# posture/scan-control ACTION as an enforcement input, NO org loses coverage. detector_class
# = "all" carries the full 63-pattern detect_* suite the posture used to provide.
_ENFORCING_ACTIONS = frozenset({"redact", "block"})
# Marks an auto-seeded detector rule so re-seed can reconcile ONLY its own rules
# (retiring stale ones on a config change) while preserving operator-added rules.
_SEED_MARKER = "Auto-seeded Phase-2 detector rule"


def _detector_rules_for_server(posture: str, effective: dict) -> list[dict]:
    """Return the detector-rule specs replicating a server's effective Tier-1 enforcement.

    For each direction (input/output) the resolved Tier-1 control is honoured: a disabled
    slot enforces nothing; ``inherit`` falls back to the server ``posture``; only an ENFORCING
    action (redact/block) seeds a rule (tag/monitor were observe-only → nothing to replicate).
    Identical input+output specs collapse to a single ``both`` rule. Scope mirrors the
    control's ``target_mode``/``key_path`` (entire → entire; key_path → key)."""
    by_dir: dict[str, dict] = {}
    for direction in ("input", "output"):
        ctrl = effective.get(f"tier1_{direction}") or {}
        if not ctrl.get("enabled", True):
            continue
        action = (ctrl.get("action") or "inherit").strip().lower()
        if action in ("inherit", ""):
            action = (posture or "").strip().lower()
        if action not in _ENFORCING_ACTIONS:
            continue  # tag/monitor/unknown → observe-only, nothing to seed
        cond = {"detector_class": "all", "direction": direction}
        if (ctrl.get("target_mode") or "entire") == "key_path" and (ctrl.get("key_path") or "").strip():
            cond["scope"] = "key"
            cond["key"] = ctrl["key_path"].strip()
        else:
            cond["scope"] = "entire"
        by_dir[direction] = {"action": action, "condition": cond}

    inp, out = by_dir.get("input"), by_dir.get("output")
    specs: list[dict] = []
    if inp and out and inp["action"] == out["action"] and \
            {k: v for k, v in inp["condition"].items() if k != "direction"} == \
            {k: v for k, v in out["condition"].items() if k != "direction"}:
        merged = dict(inp["condition"]); merged["direction"] = "both"
        specs.append({"action": inp["action"], "condition": merged, "target_tool": ""})
    else:
        for d in ("input", "output"):
            if by_dir.get(d):
                specs.append({**by_dir[d], "target_tool": ""})
    return specs


# Action severity for RAISED/LOWERED per-tool comparison (observe-only = 0).
_ACTION_RANK = {"tag": 0, "monitor": 0, "": 0, "inherit": 0, "allow": 0, "redact": 1, "block": 2}


def _resolved_dir_action(ctrl: dict, tool_scan_action: str | None, posture: str) -> str | None:
    """Effective action for one direction with the full inherit chain:
    scan-control action → per-tool MCPToolRegistration.scan_action → server posture → observe.
    Returns None for a disabled direction (enforces nothing)."""
    if not ctrl.get("enabled", True):
        return None
    a = (ctrl.get("action") or "inherit").strip().lower()
    if a in ("inherit", ""):
        a = (tool_scan_action or "").strip().lower()
    if a in ("inherit", ""):
        a = (posture or "").strip().lower()
    return a or "monitor"


def _per_tool_specs(rows: list, server_id: str, posture: str,
                    tool_actions: dict[str, str]) -> list[dict]:
    """Per-tool overrides vs the server baseline (red-team #2 raised + #5 lowered).

    For each tool whose effective enforcement DIFFERS from the server-wide baseline:
      * RAISED (tool more severe) → a per-tool ``target_tool`` detector rule at the tool's
        action (else the elevated coverage is LOST — a leak once posture is retired).
      * LOWERED (tool observe-only below an enforcing server) → a per-tool EXEMPTION rule the
        gateway honours to downgrade the server-wide rule to observe-only for that tool (the
        additive model can't otherwise un-enforce a tool).
    Tools matching the server baseline need nothing (the server-wide rule covers them)."""
    from mcp_connector.scan_controls import resolve_effective_controls

    eff_server = resolve_effective_controls(rows, server_id=server_id, tool_name="")
    specs: list[dict] = []
    for tool, tool_scan_action in tool_actions.items():
        eff_tool = resolve_effective_controls(rows, server_id=server_id, tool_name=tool)
        by_dir: dict[str, dict] = {}   # direction -> raised detector spec
        lowered = False
        for direction in ("input", "output"):
            srv_a = _resolved_dir_action(eff_server.get(f"tier1_{direction}") or {}, None, posture) or "monitor"
            tool_a = _resolved_dir_action(eff_tool.get(f"tier1_{direction}") or {}, tool_scan_action, posture) or "monitor"
            sr, tr = _ACTION_RANK.get(srv_a, 0), _ACTION_RANK.get(tool_a, 0)
            if tr > sr and tool_a in _ENFORCING_ACTIONS:
                ctrl = eff_tool.get(f"tier1_{direction}") or {}
                cond = {"detector_class": "all", "direction": direction}
                if (ctrl.get("target_mode") or "entire") == "key_path" and (ctrl.get("key_path") or "").strip():
                    cond["scope"] = "key"; cond["key"] = ctrl["key_path"].strip()
                else:
                    cond["scope"] = "entire"
                by_dir[direction] = {"action": tool_a, "condition": cond}
            elif tr < sr and sr > 0:
                lowered = True

        # Collapse identical input+output raised specs into one ``both`` rule (the common
        # MCPToolRegistration.scan_action override is direction-agnostic).
        inp, out = by_dir.get("input"), by_dir.get("output")
        if inp and out and inp["action"] == out["action"] and \
                {k: v for k, v in inp["condition"].items() if k != "direction"} == \
                {k: v for k, v in out["condition"].items() if k != "direction"}:
            merged = dict(inp["condition"]); merged["direction"] = "both"
            specs.append({"action": inp["action"], "condition": merged, "target_tool": tool})
        else:
            for d in ("input", "output"):
                if by_dir.get(d):
                    specs.append({**by_dir[d], "target_tool": tool})

        if lowered:  # one blanket exemption for an observe-only tool below an enforcing server
            specs.append({"action": "allow",
                          "condition": {"exempt": True, "direction": "both"},
                          "target_tool": tool})
    return specs


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
