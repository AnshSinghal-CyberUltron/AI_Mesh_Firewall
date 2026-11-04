"""GW17c on the MCP request path: the allow-list, the `tools/list` filter and the call cap.

`_forward` is the one place every transport reaches, so these functions are called from there and
no transport can bypass them (DD-17). They return a refusal code (or a verdict) and never raise.
An unrestricted key (empty allow-list, cap 0) is untouched: no store call, no lookup (R16.8).
"""

from __future__ import annotations

from collections.abc import Mapping

from gateway_v2.admit.mcp_cap import CallCapTable, CapVerdict
from gateway_v2.admit.mcp_metrics import McpPolicyMetrics
from gateway_v2.admit.mcp_policy import filter_listed, tool_name_valid, tool_permitted
from gateway_v2.domain import posture
from gateway_v2.domain.identity import Principal
from gateway_v2.runtime.resources_mcp import mcp_policy_bounds

__all__ = ("apply_tool_policy", "filter_tools_list", "spend_cap")


def apply_tool_policy(
    principal: Principal,
    method: str,
    params: Mapping[str, object],
    metrics: McpPolicyMetrics | None,
) -> str | None:
    """``None`` = proceed; else the refusal code. Only ``tools/call`` is restricted."""
    if method != "tools/call" or not principal.mcp_allowed_tools:
        return None
    name = params.get("name")
    if not tool_name_valid(name, mcp_policy_bounds().tool_name_max):
        return "mcp_invalid_params"
    if tool_permitted(principal, str(name)):
        return None
    if metrics is not None:
        metrics.observe_tool_denied()
    return posture.MCP_TOOL_NOT_PERMITTED


def filter_tools_list(principal: Principal, envelope: object) -> object:
    """Drop tools the key may not call from a ``tools/list`` result (before the output scan)."""
    if not principal.mcp_allowed_tools or not isinstance(envelope, dict):
        return envelope
    result = envelope.get("result")
    if not isinstance(result, dict) or not isinstance(result.get("tools"), list):
        return envelope
    return {**envelope, "result": {**result, "tools": filter_listed(principal, result["tools"])}}


def spend_cap(
    table: CallCapTable | None, principal: Principal, method: str, request_id: str
) -> CapVerdict | None:
    """``None`` = no cap applies / admitted; a refusing :class:`CapVerdict` otherwise.

    A capped key with no cap table wired is refused (fail closed, DD-17 note in the design).
    """
    if method != "tools/call" or principal.mcp_max_tool_calls <= 0:
        return None
    if table is None:
        return CapVerdict(
            False, posture.MCP_TOOL_CALL_CAP_EXCEEDED, 0.0, False, request_id
        )
    verdict = table.try_spend(principal, request_id)
    return None if verdict.admitted else verdict
