"""MCP scan control matrix — resolution and serialization helpers.

Precedence (highest wins within the same tier + direction):
  1. tool-specific (server + tool_name match)
  2. server-specific (server match, no tool_name)
  3. org-wide (no server)
  4. built-in safe defaults

Within the same scope level, higher ``priority`` wins.
"""

from __future__ import annotations

from typing import Any

SCOPE_RANK = {"tool": 3, "server": 2, "org": 1}

DEFAULT_TIER1 = {
    "tier": "tier1",
    "enabled": True,
    "direction": "both",
    "scope_type": "org",
    "target_mode": "entire",
    "key_path": "",
    "strict_mode": "fail_open",
    "action": "inherit",
    "priority": 0,
    "control_id": None,
}

DEFAULT_TIER2 = {
    "tier": "tier2",
    "enabled": False,
    "direction": "both",
    "scope_type": "org",
    "target_mode": "entire",
    "key_path": "",
    "strict_mode": "strict",
    "action": "inherit",
    "priority": 0,
    "control_id": None,
}


def _direction_matches(control_dir: str, scan_dir: str) -> bool:
    if control_dir == "both":
        return True
    return control_dir == scan_dir


def _scope_matches(
    row: dict[str, Any],
    *,
    server_id: str | None,
    tool_name: str,
) -> bool:
    scope = row.get("scope_type") or "org"
    if scope == "org":
        return not row.get("server_id")
    if scope == "server":
        return bool(row.get("server_id")) and str(row["server_id"]) == str(server_id) and not row.get("tool_name")
    if scope == "tool":
        return (
            bool(row.get("server_id"))
            and str(row["server_id"]) == str(server_id)
            and (row.get("tool_name") or "") == (tool_name or "")
        )
    return False


def _pick_control(
    rows: list[dict[str, Any]],
    *,
    tier: str,
    scan_direction: str,
    server_id: str | None,
    tool_name: str,
) -> dict[str, Any]:
    """Pick the best matching control for tier + scan direction (input|output)."""
    candidates: list[dict[str, Any]] = []
    for row in rows:
        if row.get("tier") != tier:
            continue
        if not row.get("enabled", True):
            # Disabled rows still participate only if explicitly enabled=False
            # means "turn off this tier for this scope"; we treat enabled=False
            # as a candidate that forces disabled when it wins precedence.
            pass
        ctrl_dir = row.get("direction") or "both"
        if not _direction_matches(ctrl_dir, scan_direction):
            continue
        if not _scope_matches(row, server_id=server_id, tool_name=tool_name):
            continue
        candidates.append(row)

    if not candidates:
        base = DEFAULT_TIER1 if tier == "tier1" else DEFAULT_TIER2
        out = dict(base)
        out["direction"] = scan_direction
        return out

    def sort_key(r: dict[str, Any]) -> tuple[int, int]:
        scope = r.get("scope_type") or "org"
        return (SCOPE_RANK.get(scope, 0), int(r.get("priority") or 0))

    best = max(candidates, key=sort_key)
    return {
        "tier": tier,
        "enabled": bool(best.get("enabled", True)),
        "direction": scan_direction,
        "scope_type": best.get("scope_type") or "org",
        "target_mode": best.get("target_mode") or "entire",
        "key_path": (best.get("key_path") or "").strip(),
        "strict_mode": best.get("strict_mode") or ("strict" if tier == "tier2" else "fail_open"),
        "action": best.get("action") or "inherit",
        "priority": int(best.get("priority") or 0),
        "control_id": best.get("id"),
    }


def serialize_control(instance) -> dict[str, Any]:
    """ORM instance → JSON-serializable dict for gateway payloads."""
    return {
        "id": str(instance.id),
        "tier": instance.tier,
        "enabled": instance.enabled,
        "direction": instance.direction,
        "scope_type": instance.scope_type,
        "server_id": str(instance.server_id) if instance.server_id else None,
        "tool_name": instance.tool_name or "",
        "target_mode": instance.target_mode,
        "key_path": instance.key_path or "",
        "strict_mode": instance.strict_mode,
        "action": instance.action,
        "priority": instance.priority,
    }


def resolve_effective_controls(
    rows: list[dict[str, Any]],
    *,
    server_id: str | None,
    tool_name: str = "",
) -> dict[str, Any]:
    """Resolve per-tier, per-direction effective controls for a tool call."""
    # Always use the two-tier pipeline; empty rows resolve to built-in defaults.
    return {
        "scan_controls_configured": True,
        "tier1_input": _pick_control(
            rows, tier="tier1", scan_direction="input", server_id=server_id, tool_name=tool_name
        ),
        "tier1_output": _pick_control(
            rows, tier="tier1", scan_direction="output", server_id=server_id, tool_name=tool_name
        ),
        "tier2_input": _pick_control(
            rows, tier="tier2", scan_direction="input", server_id=server_id, tool_name=tool_name
        ),
        "tier2_output": _pick_control(
            rows, tier="tier2", scan_direction="output", server_id=server_id, tool_name=tool_name
        ),
    }
