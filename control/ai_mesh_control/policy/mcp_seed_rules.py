"""Pure (Django-free) rule-generation for the Phase-2 MCP detector seeding.

Split out of ``mcp_seed.py`` so the SAME rule-generation code that the control-plane
seeding writes to the DB can be imported and replayed by the gateway's pre-cutover
DIFF-GATE (``test_mcp_phase3_diff_gate``) without pulling in ``django.db``. Keeping the
two on one code path is the whole point: the gate proves the SEEDED policies reproduce
the LIVE posture verdict, and it can only prove that if it exercises the real seeding
logic — not a copy that can silently drift.

Nothing here touches the ORM. ``resolve_effective_controls`` (imported lazily inside
``_per_tool_specs``) is itself pure. The DB writers stay in ``mcp_seed.py``.
"""

from __future__ import annotations

import json

# (preset key, rule action, direction, human label) for the baseline PII policy.
# direction "both" => evaluated on tool input args AND tool output.
_DEFAULT_RULES: list[tuple[str, str, str, str]] = [
    ("credit_card", "redact", "both", "Credit Card (Luhn)"),
    ("us_ssn", "redact", "both", "US Social Security Number"),
    ("email", "redact", "both", "Email Address"),
    ("phone", "redact", "both", "Phone Number"),
]

# Phase 2b (2026-07-23) — collapse-to-one-surface, control-plane seeding.
_ENFORCING_ACTIONS = frozenset({"redact", "block"})
# Marks an auto-seeded detector rule so re-seed can reconcile ONLY its own rules
# (retiring stale ones on a config change) while preserving operator-added rules.
_SEED_MARKER = "Auto-seeded Phase-2 detector rule"
# Action severity for RAISED/LOWERED per-tool comparison (observe-only = 0).
_ACTION_RANK = {"tag": 0, "monitor": 0, "": 0, "inherit": 0, "allow": 0, "redact": 1, "block": 2}


def _rule_sig(action: str, condition: dict, target_tool: str = "") -> tuple:
    """Hashable identity of a rule for reconcile/dedup — condition may hold list values
    (keywords), so JSON-serialize it (sorted) rather than hashing its items tuple. target_tool
    is part of the identity so a per-tool rule doesn't collide with the server-wide one."""
    return (action, target_tool or "", json.dumps(condition or {}, sort_keys=True, default=str))


def policy_code_for_org(org_id: int) -> str:
    """Deterministic, globally-unique policy code for an org's baseline."""
    return f"PII_MCP_{org_id}"


def detector_policy_code(org_id: int, server_id) -> str:
    """Deterministic code for a server's Phase-2 seeded detector policy."""
    return f"MCP_DETECTOR_{org_id}_{server_id}"


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


def _augment_injection_specs(specs: list[dict]) -> list[dict]:
    """#4: a BLOCK action also blocks prompt-injection/jailbreak via the server posture's preset
    floor — coverage that detector_class=all (pii/credential/ip_leakage) does NOT carry. For
    every seeded BLOCK detector rule, add a parallel injection block rule at the same
    direction/target_tool so injection blocking survives Phase 3."""
    extra: list[dict] = []
    for s in specs:
        cond = s.get("condition") or {}
        if s.get("action") == "block" and cond.get("detector_class") == "all":
            # Mirror the source block rule's SCOPE binding (#3): a key_path-scoped block must
            # scope injection to the SAME fragment, else injection widens to the whole payload
            # (over-block on fields the posture never scanned).
            inj = {"detector_class": "injection", "direction": cond.get("direction", "both"),
                   "scope": cond.get("scope", "entire")}
            if cond.get("key"):
                inj["key"] = cond["key"]
            extra.append({"action": "block", "condition": inj, "target_tool": s.get("target_tool", "")})
    return specs + extra


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
        lowered_dirs: list[str] = []   # directions the tool is lowered below the server (#5/RC-B)
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
                lowered_dirs.append(direction)

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

        # PER-DIRECTION exemption (#5/RC-B): exempt ONLY the direction(s) the operator lowered,
        # so the still-enforcing direction (equal-to-server, or an explicitly raised per-tool
        # control emitted above) keeps its live action. Collapse to ``both`` when both lowered.
        if lowered_dirs:
            exempt_dir = "both" if set(lowered_dirs) == {"input", "output"} else lowered_dirs[0]
            if exempt_dir == "both":
                specs.append({"action": "allow", "condition": {"exempt": True, "direction": "both"},
                              "target_tool": tool})
            else:
                for d in lowered_dirs:
                    specs.append({"action": "allow", "condition": {"exempt": True, "direction": d},
                                  "target_tool": tool})
    return specs
