"""GW17c: the per-key tool allow-list. Pure; no I/O, no clock, no state.

An empty set means unrestricted. A set holding `DENY_ALL_TOOLS` permits nothing, because no valid
tool name can equal the sentinel. `resources/read`, `prompts/get` and `completion/complete` are not
restricted (R16.9, recorded decision, v1 parity).
"""

from __future__ import annotations

from collections.abc import Sequence

from gateway_v2.domain.identity import Principal

__all__ = ("filter_listed", "tool_name_valid", "tool_permitted")


def tool_permitted(principal: Principal, name: str) -> bool:
    """Short-circuits on an empty set so an unrestricted key pays one attribute read."""
    allowed = principal.mcp_allowed_tools
    return not allowed or name in allowed


def tool_name_valid(name: object, limit: int) -> bool:
    """DD-5: a string of 1..limit characters with no control characters."""
    return isinstance(name, str) and 1 <= len(name) <= limit and name.isprintable()


def filter_listed(principal: Principal, tools: Sequence[object]) -> list[object]:
    """Keep a tool only if it is a ``dict`` with a ``str`` ``name`` the key may call.

    Never reorders and never mutates a kept entry (Property 2). An allow-list matching nothing
    gives a valid empty list.
    """
    if not principal.mcp_allowed_tools:
        return list(tools)
    return [
        tool
        for tool in tools
        if isinstance(tool, dict)
        and isinstance(tool.get("name"), str)
        and tool_permitted(principal, tool["name"])
    ]
