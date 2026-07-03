"""CHG-0100: a secret SPLIT ACROSS content-array items evaded the tool-result scan.

A malicious upstream can split a secret so each half is a benign sub-pattern in adjacent
content blocks (``…AKIAIOSFOD`` / ``NN7EXAMPLE…``). The whole-payload scan never sees the
value contiguous (the blocks are separated by JSON structure), yet a client that
CONCATENATES the text blocks reconstructs it. ``_scan_tool_result_floor`` now detects a
high-confidence secret present in the block-text concatenation but NOT wholly inside any
single block, and fails CLOSED (a cross-block split cannot be masked in place).
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

_SEC = "AKIAIOSFODNN7EXAMPLE"  # aws_access_key (SECRET)


async def _floor(result, *, enabled_info=None):
    return await mcp_proxy._scan_tool_result_floor(
        result, tool_name="fetch", enabled_info=enabled_info,
        org_slug="o", server_slug="s", actor=None)


def _blocks(*texts):
    return {"content": [{"type": "text", "text": t} for t in texts]}


@pytest.mark.asyncio
async def test_secret_split_across_two_blocks_blocked():
    scanned, blocked, tags, findings, meta = await _floor(
        _blocks("the key is " + _SEC[:10], _SEC[10:] + " end"))
    assert blocked is True
    assert meta.get("cross_block_split_secret") is True
    assert "SECRET" in tags


@pytest.mark.asyncio
async def test_secret_split_across_three_blocks_blocked():
    scanned, blocked, tags, findings, meta = await _floor(
        _blocks("key " + _SEC[:6], _SEC[6:14], _SEC[14:] + " ok"))
    assert blocked is True
    assert meta.get("cross_block_split_secret") is True


@pytest.mark.asyncio
async def test_contiguous_secret_redacted_not_split_blocked():
    """A secret wholly inside ONE block must be REDACTED by the normal floor, NOT
    force-blocked by the cross-block check (no over-block)."""
    scanned, blocked, tags, findings, meta = await _floor(
        _blocks("the key is " + _SEC, "thanks"))
    assert blocked is False
    assert meta.get("cross_block_split_secret") is None
    assert _SEC not in json.dumps(scanned)  # redacted
    assert "***" in json.dumps(scanned)


@pytest.mark.asyncio
async def test_benign_multiblock_passes():
    scanned, blocked, tags, findings, meta = await _floor(
        _blocks("Here is part one of the report.", "And here is part two, all good."))
    assert blocked is False
    assert meta.get("cross_block_split_secret") is None


@pytest.mark.asyncio
async def test_single_block_no_split_check():
    scanned, blocked, tags, findings, meta = await _floor(_blocks("just one block, no secret"))
    assert blocked is False


@pytest.mark.asyncio
async def test_monitor_posture_does_not_block_split():
    """A per-tool 'monitor' action is observe-only and wins — the split is not blocked."""
    scanned, blocked, tags, findings, meta = await _floor(
        _blocks("the key is " + _SEC[:10], _SEC[10:] + " end"),
        enabled_info={"tool_scan_actions": {"fetch": "monitor"}})
    assert blocked is False
    assert meta.get("cross_block_split_secret") is None


def test_result_content_texts_helper():
    fn = mcp_proxy._result_content_texts
    assert fn({"content": [{"type": "text", "text": "a"}, {"type": "text", "text": "b"}]}) == ["a", "b"]
    assert fn([{"type": "text", "text": "x"}]) == ["x"]
    assert fn({"content": [{"type": "image", "data": "..."}]}) == []  # non-text blocks skipped
    assert fn("plain string") == []


def test_result_has_split_secret_helper():
    fn = mcp_proxy._result_has_split_secret
    split, kinds = fn(_blocks("k " + _SEC[:8], _SEC[8:]))
    assert split is True and kinds
    # contiguous -> not a split
    assert fn(_blocks(_SEC, "other"))[0] is False
    # benign -> not a split
    assert fn(_blocks("hello", "world"))[0] is False
    # single block -> never a split
    assert fn(_blocks(_SEC))[0] is False


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
