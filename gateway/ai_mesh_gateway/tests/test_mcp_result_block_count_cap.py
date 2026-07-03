"""CHG-0104: cap the NUMBER of content blocks in a tool result (resource-bomb limit).

The 10MB response byte cap does NOT stop a many-tiny-block bomb: ~50k blocks of ~200B is
~3-10MB (UNDER the byte cap) but amplifies cost across every per-block loop (scan, JSON
serialize, filter) and stalls the event loop. ``_scan_tool_result_floor`` now fails CLOSED
(O(1) length check, before the expensive scan) when a result has more than
``_MCP_MAX_CONTENT_BLOCKS`` blocks — a resource limit missing alongside the byte limit.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_GW = Path(__file__).resolve().parents[1]
if str(_GW) not in sys.path:
    sys.path.insert(0, str(_GW))
_SHARED = _GW.parents[1] / "shared"
if _SHARED.is_dir() and str(_SHARED) not in sys.path:
    sys.path.append(str(_SHARED))

import mcp_proxy  # noqa: E402


async def _floor(result, *, enabled_info=None):
    return await mcp_proxy._scan_tool_result_floor(
        result, tool_name="fetch", enabled_info=enabled_info,
        org_slug="o", server_slug="s", actor=None)


def _blocks(n):
    return {"content": [{"type": "text", "text": f"b{i}"} for i in range(n)]}


@pytest.mark.asyncio
async def test_over_cap_many_block_result_fails_closed():
    scanned, blocked, tags, findings, meta = await _floor(_blocks(mcp_proxy._MCP_MAX_CONTENT_BLOCKS + 1))
    assert blocked is True
    assert meta.get("result_too_many_content_blocks") is True
    assert meta.get("content_block_count") == mcp_proxy._MCP_MAX_CONTENT_BLOCKS + 1
    assert "RESOURCE_LIMIT" in tags


@pytest.mark.asyncio
async def test_at_cap_allowed():
    scanned, blocked, tags, findings, meta = await _floor(_blocks(mcp_proxy._MCP_MAX_CONTENT_BLOCKS))
    assert blocked is False
    assert meta.get("result_too_many_content_blocks") is None


@pytest.mark.asyncio
async def test_bare_list_result_also_capped():
    # a result that is a bare block LIST (not wrapped in {"content": ...}) is also counted
    scanned, blocked, tags, findings, meta = await _floor(
        [{"type": "text", "text": f"b{i}"} for i in range(mcp_proxy._MCP_MAX_CONTENT_BLOCKS + 1)])
    assert blocked is True
    assert meta.get("result_too_many_content_blocks") is True


@pytest.mark.asyncio
async def test_normal_result_unaffected_and_still_scanned():
    scanned, blocked, tags, findings, meta = await _floor(
        {"content": [{"type": "text", "text": "contact jane.doe@corp.example"}]})
    assert blocked is False
    assert meta.get("result_too_many_content_blocks") is None
    assert "jane.doe@corp.example" not in json.dumps(scanned)  # still masked


@pytest.mark.asyncio
async def test_monitor_posture_does_not_block_many_blocks():
    scanned, blocked, tags, findings, meta = await _floor(
        _blocks(mcp_proxy._MCP_MAX_CONTENT_BLOCKS + 1),
        enabled_info={"tool_scan_actions": {"fetch": "monitor"}})
    assert blocked is False  # observe-only wins
    assert meta.get("result_too_many_content_blocks") is None


@pytest.mark.asyncio
async def test_string_result_not_a_block_bomb():
    # a bare-string result has no content blocks — the cap does not apply
    scanned, blocked, tags, findings, meta = await _floor("just a plain string result")
    assert meta.get("result_too_many_content_blocks") is None


def test_cap_is_positive_and_env_configurable():
    import os
    assert mcp_proxy._MCP_MAX_CONTENT_BLOCKS > 0
    assert mcp_proxy._MCP_MAX_CONTENT_BLOCKS == int(os.environ.get("MCP_MAX_CONTENT_BLOCKS", "10000"))




# ── CHG-0115: PROACTIVE nesting-depth cap (deeply-nested result resource bomb). A
# structure nested past the recursion limit would RecursionError mid-scan (fail-closed but
# fragile / generic reason); the floor now blocks it O(depth-bounded) BEFORE the scan with a
# clear reason, and the guard itself is ITERATIVE so it cannot be DoS'd by the deep payload.


def _nest_dict(n):
    o = {"leaf": "x"}
    for _ in range(n):
        o = {"k": o}
    return o


def test_exceeds_nesting_depth_is_iterative_no_recursion():
    # 50k deep must NOT RecursionError the guard itself.
    assert mcp_proxy._exceeds_nesting_depth(_nest_dict(50000), 500) is True
    assert mcp_proxy._exceeds_nesting_depth({"a": {"b": {"c": "x"}}}, 500) is False
    # wide-but-shallow is not falsely flagged
    assert mcp_proxy._exceeds_nesting_depth({"content": [{"t": "x"}] * 5000}, 500) is False


@pytest.mark.asyncio
async def test_deeply_nested_result_fails_closed_with_clear_reason():
    scanned, blocked, tags, findings, meta = await _floor(
        {"structuredContent": _nest_dict(20000)},
        enabled_info={"default_scan_action": "redact"})
    assert blocked is True
    assert meta.get("result_too_deeply_nested") is True
    assert meta.get("max_result_depth") == mcp_proxy._MCP_MAX_RESULT_DEPTH
    assert "RESOURCE_LIMIT" in tags


@pytest.mark.asyncio
async def test_shallow_result_not_blocked_by_depth_cap():
    # A realistic (a few levels) result scans normally + masks PII (not depth-blocked).
    scanned, blocked, tags, findings, meta = await _floor(
        {"content": [{"type": "text", "text": "user bob@corp.example"}]},
        enabled_info={"default_scan_action": "redact"})
    assert blocked is False
    assert "bob@corp.example" not in json.dumps(scanned)


@pytest.mark.asyncio
async def test_monitor_action_skips_depth_cap():
    # A per-tool "monitor" override is observe-only and must not block (parity with the
    # block-count cap).
    _, blocked, _, _, _ = await _floor(
        {"structuredContent": _nest_dict(20000)},
        enabled_info={"tool_scan_actions": {"fetch": "monitor"}})
    assert blocked is False



# ── CHG-0116: input-side twin of CHG-0115 — proactive depth cap on inbound tool ARGS
# (attacker-controlled). Deep args would RecursionError the recursive arg scan (caught as a
# generic arg_scan_error, and under "monitor" that fail-closed block violates observe-only).


async def _args(arguments, *, enabled_info=None):
    return await mcp_proxy._scan_tool_args_block(
        arguments, tool_name="fetch", enabled_info=enabled_info,
        org_slug="o", server_slug="s", actor=None)


@pytest.mark.asyncio
async def test_deeply_nested_args_fail_closed_with_clear_reason():
    scanned, blocked, tags, findings, meta = await _args(
        {"payload": _nest_dict(20000)}, enabled_info={"default_scan_action": "redact"})
    assert blocked is True
    assert meta.get("args_too_deeply_nested") is True
    assert meta.get("max_arg_depth") == mcp_proxy._MCP_MAX_ARG_DEPTH
    assert "RESOURCE_LIMIT" in tags


@pytest.mark.asyncio
async def test_deeply_nested_args_monitor_forwarded_not_blocked():
    scanned, blocked, tags, findings, meta = await _args(
        {"payload": _nest_dict(20000)}, enabled_info={"tool_scan_actions": {"fetch": "monitor"}})
    assert blocked is False  # observe-only: never block, even a deep-args bomb
    assert meta.get("monitor_scan_skipped") is True


@pytest.mark.asyncio
async def test_shallow_args_still_scanned_and_masked():
    scanned, blocked, tags, findings, meta = await _args(
        {"q": "email bob@corp.example"}, enabled_info={"default_scan_action": "redact"})
    assert blocked is False
    assert "bob@corp.example" not in json.dumps(scanned)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
