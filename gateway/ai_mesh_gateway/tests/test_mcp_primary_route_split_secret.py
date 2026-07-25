"""T3-01 (lifecycle red-team wf_a29f8f21): the primary org_mcp_jsonrpc route
forwarded a secret SPLIT across content blocks RAW, because it scanned results with
bare _mcp_security_scan instead of the cross-block split floor the other routes get
via _scan_tool_result_floor. This locks the shared _apply_cross_block_split_floor
helper + the floor's continued behavior.
"""
import asyncio
import mcp_proxy


# AKIAIOSFOD + NN7EXAMPLE reassemble to a full AWS key across two benign blocks.
SPLIT = {"content": [{"type": "text", "text": "the key is AKIAIOSFOD"},
                     {"type": "text", "text": "NN7EXAMPLE end"}]}
REDACT = {"default_scan_action": "redact"}
BLOCK = {"default_scan_action": "block"}
MONITOR = {"default_scan_action": "monitor"}


def _join(content):
    blocks = content.get("content") if isinstance(content, dict) else content
    return "".join(b.get("text", "") for b in (blocks or []) if isinstance(b, dict))


def test_helper_redact_masks_split():
    out, blocked, tags = mcp_proxy._apply_cross_block_split_floor(
        SPLIT, SPLIT, tool_name="fetch", enabled_info=REDACT, org_slug="o", server_slug="s")
    assert blocked is False
    assert "SECRET" in tags
    assert "AKIAIOSFODNN7EXAMPLE" not in _join(out), "split key still reassembles!"


def test_helper_block_withholds_split():
    out, blocked, tags = mcp_proxy._apply_cross_block_split_floor(
        SPLIT, SPLIT, tool_name="fetch", enabled_info=BLOCK, org_slug="o", server_slug="s")
    assert blocked is True
    assert "SECRET" in tags


def test_helper_monitor_observe_only_never_fires():
    out, blocked, tags = mcp_proxy._apply_cross_block_split_floor(
        SPLIT, SPLIT, tool_name="fetch", enabled_info=MONITOR, org_slug="o", server_slug="s")
    assert blocked is False and tags == []
    assert out is SPLIT  # untouched under observe-only


def test_helper_no_split_untouched():
    clean = {"content": [{"type": "text", "text": "hello"}, {"type": "text", "text": "world"}]}
    out, blocked, tags = mcp_proxy._apply_cross_block_split_floor(
        clean, clean, tool_name="fetch", enabled_info=REDACT, org_slug="o", server_slug="s")
    assert blocked is False and tags == [] and out is clean


def test_floor_still_masks_split_after_refactor():
    # the shared refactor must not regress _scan_tool_result_floor (the other routes).
    scanned, blocked, tags, _f, _m = asyncio.run(mcp_proxy._scan_tool_result_floor(
        SPLIT, tool_name="fetch", enabled_info=REDACT, org_slug="o", server_slug="s"))
    assert blocked is False
    assert "AKIAIOSFODNN7EXAMPLE" not in _join(scanned)


def test_floor_blocks_split_under_block_posture():
    scanned, blocked, tags, _f, _m = asyncio.run(mcp_proxy._scan_tool_result_floor(
        SPLIT, tool_name="fetch", enabled_info=BLOCK, org_slug="o", server_slug="s"))
    assert blocked is True
    assert "SECRET" in tags
