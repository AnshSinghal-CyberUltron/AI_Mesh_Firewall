"""CHG-0090: item-20 "1.4 guardrails holding under peak load" — the CONCURRENCY
dimension, provable at unit scale (the 300-500-sandbox scale stays host-blocked).

The MCP scan/redact chain (_scan_tool_result_floor -> mcp_scan_orchestrator ->
patterns.redact_all) runs against MODULE-LEVEL state (compiled-pattern LRU cache,
enabled-tools/server-config caches). If any of that were mutable per-scan or shared
across coroutines without isolation, a race could CROSS-CONTAMINATE concurrent scans —
one request's secret/PII leaking into ANOTHER request's redacted result, or a canary
surviving because a neighbour's scan clobbered shared state.

This test fires many concurrent scans, each carrying a UNIQUE canary secret + PII +
internal IP, and asserts (a) every call's OWN canary is masked and (b) NO call's result
contains ANY OTHER call's canary (zero cross-contamination). It is a regression backstop
against a future edit that introduces shared mutable state into the hot scan path.
"""
from __future__ import annotations

import asyncio
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

_N = 300


async def _scan_one(i: int):
    canary_secret = f"sk-ant-CANARY{i:04d}AAAABBBBCCCCDDDDEEEE"
    canary_email = f"user{i:04d}@corp.example"
    canary_ip = f"10.{i % 256}.{(i // 256) % 256}.5"
    text = f"[req {i}] key {canary_secret} email {canary_email} host {canary_ip} done"
    scanned, blocked, tags, findings, meta = await mcp_proxy._scan_tool_result_floor(
        {"content": [{"type": "text", "text": text}]},
        tool_name="fetch", enabled_info=None, org_slug=f"org{i % 10}", server_slug="s", actor=None)
    blob = json.dumps(scanned)
    return i, blob, (canary_secret in blob), (canary_email in blob), (canary_ip in blob)


@pytest.mark.asyncio
async def test_concurrent_scans_no_leak_no_cross_contamination():
    results = await asyncio.gather(*[_scan_one(i) for i in range(_N)])

    # (a) every call's OWN canary is masked (the guardrail holds under concurrency)
    own_leaks = [i for i, _b, s_leak, e_leak, ip_leak in results if s_leak or e_leak or ip_leak]
    assert not own_leaks, f"own-canary leaked under concurrency for reqs: {own_leaks[:10]}"

    # (b) ZERO cross-contamination — no result carries another request's canary
    cross = []
    for i, blob, *_ in results:
        for j in range(_N):
            if j == i:
                continue
            if f"CANARY{j:04d}" in blob or f"user{j:04d}@corp.example" in blob:
                cross.append((i, j))
                break
    assert not cross, f"cross-contamination between concurrent scans: {cross[:10]}"


@pytest.mark.asyncio
async def test_benign_concurrent_scans_unchanged():
    async def benign(i):
        txt = f"weather report {i}: sunny, high 21C, low 12C"
        scanned, blocked, _t, _f, _m = await mcp_proxy._scan_tool_result_floor(
            {"content": [{"type": "text", "text": txt}]},
            tool_name="fetch", enabled_info=None, org_slug="o", server_slug="s", actor=None)
        return blocked, (txt in json.dumps(scanned))

    out = await asyncio.gather(*[benign(i) for i in range(100)])
    assert all((not blocked) and intact for blocked, intact in out)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
