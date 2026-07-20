"""CHG-0103: the CPU-bound Tier-1 scan (detect_* + redact_all + render-leak neutralizers,
all synchronous regex) ran INLINE on the event loop, so a LARGE tool result blocked the
loop for seconds and froze every other concurrent request on the worker (measured: an 8MB
scan stalled a trivial coroutine ~9.8s). The scan is now offloaded to a worker thread for
inputs over ``_TIER1_OFFLOAD_THRESHOLD`` (the ``re`` loop releases the GIL between patterns,
so the loop stays responsive — the same 8MB scan then stalls it only ~0.16s); small inputs
run inline to avoid thread-pool pressure.

These tests are deterministic (patch ``to_thread``, assert the offload decision + scan
correctness) — no flaky timing.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

_GW = Path(__file__).resolve().parents[1]
if str(_GW) not in sys.path:
    sys.path.insert(0, str(_GW))
_SHARED = _GW.parents[1] / "shared"
if _SHARED.is_dir() and str(_SHARED) not in sys.path:
    sys.path.append(str(_SHARED))

import mcp_proxy  # noqa: E402
import mcp_scan_orchestrator as orch  # noqa: E402


def _tier1_offloads(mock):
    """The to_thread calls that offloaded the Tier-1 sync scan."""
    return [c for c in mock.call_args_list if c.args and c.args[0] is orch._scan_text_tier1_sync]


async def _floor(result):
    return await mcp_proxy._scan_tool_result_floor(
        result, tool_name="fetch", enabled_info=None, org_slug="o", server_slug="s", actor=None)


@pytest.mark.asyncio
async def test_large_result_offloads_tier1_to_thread():
    # The Tier-1 target is the whole payload JSON-serialized, so a large text block makes it
    # exceed the offload threshold.
    big = "word " * 20_000  # ~100 KB > _TIER1_OFFLOAD_THRESHOLD (64 KB)
    result = {"content": [{"type": "text", "text": big + " key AKIAIOSFODNN7EXAMPLE"}]}
    with patch.object(orch.asyncio, "to_thread", wraps=orch.asyncio.to_thread) as m:
        scanned, blocked, tags, findings, meta = await _floor(result)
    assert _tier1_offloads(m), "large Tier-1 scan was NOT offloaded to a thread"
    # correctness preserved through the thread: the secret is still masked
    assert "AKIAIOSFODNN7EXAMPLE" not in json.dumps(scanned)


@pytest.mark.asyncio
async def test_small_result_scans_inline():
    result = {"content": [{"type": "text", "text": "contact jane.doe@corp.example please"}]}
    with patch.object(orch.asyncio, "to_thread", wraps=orch.asyncio.to_thread) as m:
        scanned, blocked, tags, findings, meta = await _floor(result)
    assert not _tier1_offloads(m), "small Tier-1 scan should run inline (no thread offload)"
    assert "jane.doe@corp.example" not in json.dumps(scanned)  # still masked inline


@pytest.mark.asyncio
async def test_offload_threshold_env_configurable_and_positive():
    assert orch._TIER1_OFFLOAD_THRESHOLD > 0
    # a text just under the threshold runs inline; just over offloads
    import os
    assert orch._TIER1_OFFLOAD_THRESHOLD == int(os.environ.get("MCP_TIER1_OFFLOAD_BYTES", str(64 * 1024)))


def test_sync_and_async_tier1_both_exist():
    # the pure-CPU body is a sync function; the offloading entrypoint is async
    import inspect
    assert not inspect.iscoroutinefunction(orch._scan_text_tier1_sync)
    assert inspect.iscoroutinefunction(orch._scan_text_tier1)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
