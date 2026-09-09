"""CHG-0093 — SSE multi-line ``data:`` split evasion of the MCP tool-result scanner.

Per the WHATWG SSE spec, a single event's data is the concatenation of ALL its
``data:`` field values joined by "\\n". ``_scan_reframe_sse_tool_result`` used to
parse each ``data:`` line as standalone JSON, so an untrusted upstream could split a
JSON-RPC result across several ``data:`` lines at a STRUCTURAL point (JSON whitespace
between tokens): each fragment is invalid JSON alone → the per-line scan fell through
to "not JSON → pass verbatim", yet a spec-compliant client REASSEMBLES the fragments
into the complete result → the secret egressed raw.

These tests drive the REAL reframer and assert on (a) the reframed EGRESS BYTES and
(b) what a spec-compliant SSE client reconstructs from them — the true client view.
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

_SECRET = "AKIAIOSFODNN7EXAMPLE"     # aws_access_key → SECRET
_IP = "10.9.8.7"                     # RFC1918 → INFRA
_SSN = "123-45-6789"


# STRICT OPERATOR CONTROL (2026-07-21): static hardening floors (incl. the E12
# result-redaction floor these tests exercise) fire ONLY under an operator-selected
# ENFORCING posture. These tests used to pass ``enabled_info=None``, which resolves to
# the observe-only "tag" posture — under which detection/tagging still happens but the
# payload is never mutated, so asserting masking there contradicted the product rule.
# The posture is now stated explicitly: the operator selected ``redact``.
_ENFORCING = {"default_scan_action": "redact"}


async def _reframe(sse: str, enabled_info: dict | None = _ENFORCING):
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


def _client_reassemble(sse: str) -> list[str]:
    """Emulate a spec-compliant SSE client: per event, concat data: values with \\n."""
    events, buf = [], []
    for line in sse.split("\n"):
        if line.startswith("data:"):
            buf.append(line[5:].lstrip())
        elif line.strip() == "":
            if buf:
                events.append("\n".join(buf))
                buf = []
    if buf:
        events.append("\n".join(buf))
    return events


def _client_sees(sse: str, needle: str) -> bool:
    """True if a spec-compliant client reconstructs ``needle`` from the SSE (in a
    parseable JSON event or the raw reassembled data)."""
    for ev in _client_reassemble(sse):
        if needle in ev:
            return True
    return False


@pytest.mark.asyncio
async def test_single_line_result_masked_regression():
    sse = (f'data: {{"jsonrpc":"2.0","id":1,"result":{{"content":'
           f'[{{"type":"text","text":"leak {_SECRET} at {_IP}"}}]}}}}\n\n')
    reframed, block = await _reframe(sse)
    assert block is None
    assert _SECRET not in reframed and _IP not in reframed
    assert not _client_sees(reframed, _SECRET)


@pytest.mark.asyncio
async def test_multiline_split_two_data_lines_masked():
    """The core bug: result split across 2 data: lines at a JSON structural point."""
    sse = ('data: {"jsonrpc":"2.0","id":1,"result":{"content":[{"type":"text",\n'
           f'data: "text":"leak {_SECRET} at {_IP}"}}]}}}}\n\n')
    reframed, block = await _reframe(sse)
    assert block is None
    assert _SECRET not in reframed, "secret in split SSE result egressed raw"
    assert _IP not in reframed
    # the authoritative check: a spec-compliant client cannot reconstruct the secret
    assert not _client_sees(reframed, _SECRET)
    # and what the client DOES reconstruct is still valid JSON
    for ev in _client_reassemble(reframed):
        json.loads(ev)


@pytest.mark.asyncio
async def test_multiline_split_three_lines_with_event_field_masked():
    sse = ('event: message\n'
           'data: {"jsonrpc":"2.0",\n'
           'data: "id":1,"result":{"content":[{"type":"text",\n'
           f'data: "text":"key {_SECRET} host {_IP}"}}]}}}}\n\n')
    reframed, block = await _reframe(sse)
    assert block is None
    assert _SECRET not in reframed and _IP not in reframed
    assert not _client_sees(reframed, _SECRET)
    assert "event: message" in reframed  # non-data field preserved


@pytest.mark.asyncio
async def test_multiline_split_error_frame_masked():
    """An ERROR frame split across data: lines is also reassembled + masked."""
    sse = ('data: {"jsonrpc":"2.0","id":1,"error":{"code":-32000,\n'
           f'data: "message":"auth failed key {_SECRET} at {_IP}"}}}}\n\n')
    reframed, block = await _reframe(sse)
    assert block is None
    assert _SECRET not in reframed and _IP not in reframed
    assert not _client_sees(reframed, _SECRET)


@pytest.mark.asyncio
async def test_multiline_split_masked_under_redact():
    """STRICT OPERATOR CONTROL (2026-07-22): redact means redact. A split result mixing
    a maskable IP with a private file path is MASKED in place (the class-scoped redactor
    masks both) and forwarded — never the old redact->block escalation."""
    sse = ('data: {"jsonrpc":"2.0","id":1,"result":{"content":[{"type":"text",\n'
           f'data: "text":"box {_IP} served /home/bob/.ssh/id_rsa"}}]}}}}\n\n')
    reframed, block = await _reframe(sse)
    assert block is None, "redact must mask, not block"
    assert _IP not in reframed and "id_rsa" not in reframed


@pytest.mark.asyncio
async def test_multiline_split_blocked_under_block_posture():
    sse = ('data: {"jsonrpc":"2.0","id":1,"result":{"content":[{"type":"text",\n'
           f'data: "text":"box {_IP} served /home/bob/.ssh/id_rsa"}}]}}}}\n\n')
    _reframed, block = await _reframe(sse, {"default_scan_action": "block"})
    assert block is not None, "block posture must withhold the result"


@pytest.mark.asyncio
async def test_benign_multiline_split_preserved_unchanged():
    """A benign multi-line-split result is reassembled, found clean, and its content
    survives (no false redaction / corruption)."""
    sse = ('data: {"jsonrpc":"2.0","id":2,"result":{"content":[{"type":"text",\n'
           'data: "text":"the weather in Paris is sunny"}]}}\n\n')
    reframed, block = await _reframe(sse)
    assert block is None
    assert "the weather in Paris is sunny" in reframed
    assert "***" not in reframed
    # client reconstructs valid JSON carrying the benign content
    assert any("sunny" in ev for ev in _client_reassemble(reframed))


@pytest.mark.asyncio
async def test_keepalive_and_nonjson_frames_pass_through():
    sse = (': keep-alive\n\n'
           'data: not-json-here\n\n'
           f'data: {{"jsonrpc":"2.0","id":3,"result":{{"content":'
           f'[{{"type":"text","text":"ssn {_SSN}"}}]}}}}\n\n')
    reframed, block = await _reframe(sse)
    assert block is None
    assert ": keep-alive" in reframed          # comment preserved
    assert "data: not-json-here" in reframed   # non-JSON frame verbatim
    assert _SSN not in reframed                # the real result still masked


@pytest.mark.asyncio
async def test_multiline_split_observe_only_posture_does_not_mutate():
    """Twin of ``test_multiline_split_two_data_lines_masked`` under the OBSERVE-ONLY
    posture: the reassembly + scan still runs, but ``tag`` is an operator-selected
    "Tag only" action, so the frame is neither mutated nor withheld."""
    sse = ('data: {"jsonrpc":"2.0","id":1,"result":{"content":[{"type":"text",\n'
           f'data: "text":"leak {_SECRET} at {_IP}"}}]}}}}\n\n')
    reframed, block = await _reframe(sse, enabled_info={"default_scan_action": "tag"})
    assert block is None
    assert _client_sees(reframed, _SECRET)  # observe-only: detected + tagged, never masked


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
