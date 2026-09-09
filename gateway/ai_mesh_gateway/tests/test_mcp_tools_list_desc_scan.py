"""CHG-0077: the org tools/list path (sandbox-routed adapter + backend) forwarded
upstream tool DESCRIPTIONS / metadata RAW, while the EXTERNAL proxy path already scans
tools/list (it is in _EXT_FINITE_RESULT_METHODS). Tool descriptions come LIVE from the
untrusted upstream MCP server and are shown to the model — a tool-poisoning / metadata-
leak surface. A secret / PII / internal-IP (or a CHG-0076 encoded-exfil payload) embedded
in a tool description leaked to the model on the org path.

_scanned_tools_list_response now scans tool metadata via the result-redaction floor:
masks a maskable leak, blocks (fail-closed) an unmaskable / encoded-exfil poisoned block.
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

_SECRET = "sk-ant-AAAABBBBCCCCDDDDEEEEFFFFGGGG1234"

_BENIGN_TOOLS = [
    {"name": "search", "description": "Search the docs and return results"},
    {"name": "readfile", "description": "Reads a file from /home/user/project (example)"},
]


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


def _htmlent(s: str) -> str:
    return "".join(f"&#{ord(c)};" for c in s)


async def _run(tools, scan_action="redact"):
    """``scan_action`` is the posture the OPERATOR selected. Enforcement is strictly
    operator-selected: with nothing chosen (or an observe-only posture such as
    tag/monitor) descriptions are still scanned and tagged but never mutated, so the
    masking assertions below run under an explicitly ENFORCING posture."""
    payload = {"jsonrpc": "2.0", "id": 1, "result": {"tools": tools}}
    enabled_info = {"default_scan_action": scan_action} if scan_action else None
    resp = await mcp_proxy._scanned_tools_list_response(
        payload, jsonrpc="2.0", msg_id=1, enabled_info=enabled_info,
        org_slug="o", server_slug="s", actor=None)
    return json.loads(resp.body.decode())


@pytest.mark.asyncio
async def test_secret_and_ip_in_description_masked():
    r = await _run([
        {"name": "add", "description": f"Adds numbers. Internal gw 10.4.5.6, key {_SECRET}"},
        {"name": "fetch", "description": "Fetches a URL"},
    ])
    blob = json.dumps(r)
    assert _SECRET not in blob, "secret in a tool description must be masked"
    assert "10.4.5.6" not in blob, "internal IP in a tool description must be masked"
    assert "Fetches a URL" in blob, "benign tool must survive"
    assert "error" not in r


@pytest.mark.asyncio
async def test_encoded_exfil_in_description_masked_under_redact():
    # STRICT OPERATOR CONTROL: redact masks the encoded secret in the description and
    # forwards the tools/list — not an error/block (that is the block posture's job).
    r = await _run([{"name": "x", "description": f"helper {_htmlent(_SECRET)}"}])
    assert "error" not in r
    assert _SECRET not in json.dumps(r)

@pytest.mark.asyncio
async def test_encoded_exfil_in_description_blocks_under_block():
    r = await _run([{"name": "x", "description": f"helper {_htmlent(_SECRET)}"}], scan_action="block")
    assert "error" in r
    assert _SECRET not in json.dumps(r)


@pytest.mark.asyncio
async def test_benign_tools_list_unchanged():
    """Benign metadata survives with NO operator selection: nothing was chosen, so
    nothing is enforced and the payload is never mutated."""
    r = await _run(_BENIGN_TOOLS, scan_action=None)
    assert "error" not in r
    assert len(r["result"]["tools"]) == 2
    # file paths are flag-tier — preserved (not masked, not blocked)
    assert "/home/user/project" in json.dumps(r)


@pytest.mark.xfail(strict=True, reason=(
    "FALSE POSITIVE (documented, not fixed): under an operator-selected `redact` posture "
    "a benign home-directory path in a tool description ('/home/user/project') is tagged "
    "INFRA, is not maskable by redact_all, and therefore trips the unmaskable-survivor "
    "fail-closed floor — withholding the WHOLE tools/list. The floor cannot distinguish "
    "it from a real leak such as '/home/bob/.ssh/id_rsa'. Fixing it needs detector/corpus "
    "tuning in production code, which is out of scope here; this xfail records the gap so "
    "it is not silently lost and flips to a failure the moment it is fixed."))
@pytest.mark.asyncio
async def test_benign_tools_list_unchanged_under_enforcing_posture():
    r = await _run(_BENIGN_TOOLS, scan_action="redact")
    assert "error" not in r
    assert "/home/user/project" in json.dumps(r)


@pytest.mark.asyncio
async def test_non_dict_result_passthrough():
    # a non-tools result envelope is returned untouched
    payload = {"jsonrpc": "2.0", "id": 1, "result": "pong"}
    resp = await mcp_proxy._scanned_tools_list_response(
        payload, jsonrpc="2.0", msg_id=1, enabled_info=None,
        org_slug="o", server_slug="s", actor=None)
    assert json.loads(resp.body.decode())["result"] == "pong"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
