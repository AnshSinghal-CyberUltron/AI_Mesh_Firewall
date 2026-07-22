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


# STRICT OPERATOR CONTROL (2026-07-21): the cross-block-split fail-closed check and
# the E12 result-redaction floor are static hardening floors — they fire only under an
# operator-selected ENFORCING posture. The default here used to be ``None``, which
# resolves to observe-only ``tag`` (detect + tag, never mutate, never block); that half
# of the contract is asserted by ``test_monitor_posture_does_not_block_split``.
_ENFORCING = {"default_scan_action": "redact"}


async def _floor(result, *, enabled_info=_ENFORCING):
    return await mcp_proxy._scan_tool_result_floor(
        result, tool_name="fetch", enabled_info=enabled_info,
        org_slug="o", server_slug="s", actor=None)


def _blocks(*texts):
    return {"content": [{"type": "text", "text": t} for t in texts]}


@pytest.mark.asyncio
async def test_secret_split_across_two_blocks_redacted_under_redact():
    # STRICT OPERATOR CONTROL (2026-07-22): redact means redact — the split is MASKED
    # in place (so it cannot be reconstructed) and forwarded, NOT force-blocked.
    scanned, blocked, tags, findings, meta = await _floor(
        _blocks("the key is " + _SEC[:10], _SEC[10:] + " end"))
    assert blocked is False
    assert meta.get("cross_block_split_redacted") is True
    assert _SEC not in json.dumps(scanned)   # cannot be reconstructed
    assert "SECRET" in tags


@pytest.mark.asyncio
async def test_secret_split_blocked_under_block_posture():
    scanned, blocked, tags, findings, meta = await _floor(
        _blocks("the key is " + _SEC[:10], _SEC[10:] + " end"),
        enabled_info={"default_scan_action": "block"})
    assert blocked is True
    assert meta.get("cross_block_split_secret") is True


@pytest.mark.asyncio
async def test_secret_split_across_three_blocks_redacted_under_redact():
    scanned, blocked, tags, findings, meta = await _floor(
        _blocks("key " + _SEC[:6], _SEC[6:14], _SEC[14:] + " ok"))
    assert blocked is False
    assert meta.get("cross_block_split_redacted") is True
    assert _SEC not in json.dumps(scanned)


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


def test_boundary_concat_bounds_cost_and_preserves_detection():
    """CHG-0102: the split scan uses a BOUNDARY concatenation (trim long-block interiors)
    so its cost is O(num_blocks * span), not O(total_text) — but boundary splits (incl.
    across a HUGE block) are still caught, and a contiguous secret in a long block's
    interior is NOT falsely flagged (the full-text scan covers it)."""
    fn = mcp_proxy._result_has_split_secret
    # boundary split where the LEFT block is far larger than 2*span (interior trimmed)
    huge_left = "filler " * 100_000 + _SEC[:10]
    assert fn(_blocks(huge_left, _SEC[10:] + " end"))[0] is True
    # a 3-way split with a short middle block fully inside the secret
    assert fn(_blocks("k " + _SEC[:6], _SEC[6:14], _SEC[14:] + " x"))[0] is True
    # a contiguous secret in a LONG block's interior is NOT a split (normal scan handles it)
    huge_interior = "filler " * 50_000 + _SEC + " filler " * 50_000
    assert fn(_blocks(huge_interior, "other"))[0] is False
    # the boundary concat of a big result is much smaller than the raw concat
    texts = ["word " * 2000] * 500  # ~5MB total
    assert len(mcp_proxy._boundary_concat(texts)) < 3 * mcp_proxy._MCP_SPLIT_SECRET_SPAN * len(texts)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
