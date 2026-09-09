"""L2-01 (lifecycle red-team wf_c7ea99b8): a JSON-RPC BATCH (array) SSE frame was
forwarded verbatim UNSCANNED (the reframer bailed on non-dict), so an untrusted upstream
could wrap its tool result in a 1-element batch to bypass the output floor — block didn't
block, redact didn't mask. The reframer now scans every message in a batch.
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

_SECRET = "AKIAIOSFODNN7EXAMPLE"
_ENFORCE_REDACT = {"default_scan_action": "redact"}
_ENFORCE_BLOCK = {"default_scan_action": "block"}


async def _reframe(sse, enabled_info):
    return await mcp_proxy._scan_reframe_sse_tool_result(
        sse, tool_name="fetch", org_slug="", server_slug="",
        enabled_info=enabled_info, actor=None)


# __PDD_PRESET_FIXTURE__

# policy-driven-detection cutover (task 9): the MCP built-in default detectors (the Tier-1
# PRESET pass) are now EFFECTIVE-DEFAULT OFF (mcp_scan_orchestrator._mcp_default_detection_enabled)
# so a zero-enabled-policy org is passthrough. This module exercises the RETAINED preset
# DETECTION MACHINERY (redaction / fail-closed byte-truth / exfil-defang / authz / audit), which
# stays reachable via the explicit opt-in env. Enable it for this module so those invariants are
# still tested. The default-OFF (Zero_Policy_State passthrough) contract is asserted by the
# dedicated test_policy_driven_* modules, not weakened here.
import os as _os_pdd


@pytest.fixture(autouse=True)
def _enable_builtin_mcp_presets(monkeypatch):
    monkeypatch.setenv("GATEWAY_MCP_DEFAULT_DETECTION", "true")
    monkeypatch.setenv("GATEWAY_MCP_REDACT_RESULT_ON_DETECT", "true")
    yield


def _batch_sse(*messages):
    return f"event: message\ndata: {json.dumps(list(messages))}\n\n"


@pytest.mark.asyncio
async def test_batch_frame_secret_is_masked_under_redact():
    msg = {"jsonrpc": "2.0", "id": 1,
           "result": {"content": [{"type": "text", "text": f"key={_SECRET}"}]}}
    reframed, block = await _reframe(_batch_sse(msg), _ENFORCE_REDACT)
    assert block is None
    assert _SECRET not in reframed, f"batch-wrapped secret egressed raw: {reframed!r}"


@pytest.mark.asyncio
async def test_batch_frame_blocks_under_block_posture():
    msg = {"jsonrpc": "2.0", "id": 1,
           "result": {"content": [{"type": "text", "text": f"key={_SECRET}"}]}}
    reframed, block = await _reframe(_batch_sse(msg), _ENFORCE_BLOCK)
    assert block is not None, "batch-wrapped secret was not blocked under block posture"
    assert _SECRET not in reframed


@pytest.mark.asyncio
async def test_batch_multiple_messages_all_scanned():
    m1 = {"jsonrpc": "2.0", "id": 1, "result": {"content": [{"type": "text", "text": "benign"}]}}
    m2 = {"jsonrpc": "2.0", "id": 2,
          "result": {"content": [{"type": "text", "text": f"secret {_SECRET} here"}]}}
    reframed, block = await _reframe(_batch_sse(m1, m2), _ENFORCE_REDACT)
    assert block is None
    assert _SECRET not in reframed


@pytest.mark.asyncio
async def test_benign_batch_untouched():
    m = {"jsonrpc": "2.0", "id": 1, "result": {"content": [{"type": "text", "text": "all clear"}]}}
    reframed, block = await _reframe(_batch_sse(m), _ENFORCE_REDACT)
    assert block is None
    assert "all clear" in reframed


@pytest.mark.asyncio
async def test_single_message_still_works():
    # regression: the extracted helper must not change single-dict behavior.
    sse = ('event: message\n'
           f'data: {{"jsonrpc":"2.0","id":1,"result":{{"content":[{{"type":"text","text":"key={_SECRET}"}}]}}}}\n\n')
    reframed, block = await _reframe(sse, _ENFORCE_REDACT)
    assert block is None
    assert _SECRET not in reframed
