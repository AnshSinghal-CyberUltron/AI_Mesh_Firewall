"""Policy enforcement integration layer for MCP tool calls.

The ZeroShield platform provides built-in regex/keyword/pattern policy
enforcement via the policy engine (backend/policy/engine.py and
gateway/policy_engine.py).  This module originally called an external
mcp-firewall HTTP service, but that project is a CLI wrapper — not a
standalone API.  All enforcement is now handled by the built-in engine;
these functions are retained as no-op stubs to preserve the call-site
interface and allow future external integration if needed.
"""

import logging

logger = logging.getLogger(__name__)


def health() -> dict:
    """Report built-in policy engine health (always healthy)."""
    return {
        "status": "healthy",
        "detail": (
            "Policy enforcement is handled by the built-in ZeroShield policy engine. "
            "Regex, keyword, and pattern rules are evaluated inline — no external service required."
        ),
    }


def preflight_check(
    tool_name: str,
    arguments: dict,
    org_id: str = "",
    user_id: str = "",
    server_name: str = "",
) -> dict:
    """No-op preflight — enforcement handled by built-in policy engine upstream."""
    return {"allowed": True, "reason": "handled_by_builtin_engine", "decision": "allow"}


def postflight_audit(
    tool_name: str,
    org_id: str = "",
    user_id: str = "",
    server_name: str = "",
    decision: str = "allow",
    success: bool = True,
    latency_ms: int = 0,
    metadata: dict | None = None,
) -> None:
    """No-op postflight — audit handled by MCPEvent records in views.py."""
    pass


def get_server_killswitch(server_name: str, org_id: str = "") -> dict:
    """Kill-switch check — returns inactive (not blocked) by default.

    Server-level enable/disable is managed via MCPServerRegistration.is_active
    and MCPToolRegistration.enabled in the built-in models.
    """
    return {"active": False, "reason": "Kill-switch managed via platform server/tool controls"}
