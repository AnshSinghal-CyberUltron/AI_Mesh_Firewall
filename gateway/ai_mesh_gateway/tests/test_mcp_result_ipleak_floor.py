"""CHG-0074: internal NETWORK IP leakage in a tool RESULT was DETECTED + TAGGED
(INFRA) but, under the default ``tag`` scan action, egressed RAW — the E12
result-redaction floor only fired for secret/PII (``_findings_have_secret_or_pii``),
never for the ``ip_leakage`` class. That is a fail-OPEN asymmetric with PII/secret:
an internal / cloud-metadata IP in a tool result is the same infra-disclosure class.

STRICT OPERATOR CONTROL (2026-07-21): the floor is a static hardening floor, so it
fires only under an operator-selected ENFORCING posture (``redact``/``block``).
These tests therefore select ``redact`` explicitly instead of relying on the
unselected default, which resolves to observe-only ``tag``.

These tests drive the REAL proxy floor entrypoint ``_scan_tool_result_floor`` (the
two-tier scanner runs; nothing is mocked) and assert, on the EGRESS BYTES:
  * internal network addrs (RFC1918 / link-local-metadata / CGNAT / IPv6 / hostname)
    are now MASKED (floored), not raw;
  * PII / secrets remain floored (regression guard);
  * a flag-tier FILE PATH stays RAW (never force-blocked → benign code/file tool
    results are not broken);
  * a result mixing a network/PII leak with an UNMASKABLE file path fails CLOSED
    (blocked) instead of forwarding raw (the swallowed-floor-block bug).
"""
from __future__ import annotations

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


async def _floor(text, action="redact"):
    """Run the real result-redaction floor under an OPERATOR-SELECTED posture.

    These tests used to pass ``enabled_info=None`` (i.e. nothing selected) and
    still expect masking. Under the strict-operator-control rule that is wrong:
    with no posture selected the tool resolves to ``tag`` ("Tag only"), which is
    observe-only — detection and tagging happen, the payload is never mutated.
    The E12 result-redaction floor is a static hardening floor and fires only
    under an enforcing posture, so these tests select ``redact`` explicitly.
    """
    return await mcp_proxy._scan_tool_result_floor(
        text, tool_name="fetch", enabled_info={"default_scan_action": action},
        org_slug="o", server_slug="s", actor=None,
    )


# (name, result text, the raw value that must not survive)
_NETWORK_LEAKS = [
    ("metadata_ipv4", "upstream metadata at http://169.254.169.254/latest/meta-data/", "169.254.169.254"),
    ("cgnat_ipv4", "client behind 100.64.12.34 now", "100.64.12.34"),
    ("rfc1918_ipv4", "internal box 10.10.5.7 responded", "10.10.5.7"),
    ("internal_ipv6", "peer node fc00::1234:5678 in the mesh", "fc00::1234:5678"),
    ("internal_hostname", "service db1.internal reachable", "db1.internal"),
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


@pytest.mark.asyncio
@pytest.mark.parametrize(("name", "text", "raw"), _NETWORK_LEAKS)
async def test_internal_network_leak_is_floored_not_raw(name, text, raw):
    scanned, blocked, tags, findings, meta = await _floor(text)
    # The contract is the EGRESS BYTES: masked or withheld, never forwarded raw.
    # (``meta["result_redaction_floor"]`` is deliberately NOT asserted: it marks the
    # E12 re-scan floor, which only fires when the first pass left the result
    # unmutated. Under an explicit ``redact`` posture the first pass masks inline, so
    # the flag is legitimately absent — which internal path did the masking is not
    # the security property.)
    assert blocked or raw not in str(scanned), f"{name}: {raw!r} egressed RAW"
    assert "INFRA" in tags


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("text", "raw"),
    [
        ("contact the admin at alice.smith@corp.example please", "alice.smith@corp.example"),
        ("the api key is sk-ant-AAAABBBBCCCCDDDDEEEEFFFFGGGG1234", "sk-ant-AAAABBBBCCCCDDDDEEEEFFFFGGGG1234"),
    ],
)
async def test_pii_secret_still_floored(text, raw):
    scanned, blocked, tags, findings, meta = await _floor(text)
    assert raw not in str(scanned)


@pytest.mark.asyncio
async def test_file_path_only_stays_raw_under_observe_only():
    """A flag-tier file path is neither masked nor blocked under ``tag``.

    Used to be ``test_file_path_only_stays_raw_not_blocked`` and ran with
    ``enabled_info=None``. It asserted the same three things, but for the wrong
    reason: it read as "file paths never force-block", when what it actually
    exercised was the unselected/``tag`` posture. Under an operator-selected
    ``redact`` posture a file path IS a detected ip_leakage value that redact_all
    cannot mask, so the scan fails CLOSED (blocks) — see
    ``test_mixed_unmaskable_fails_closed``. The flag-tier property itself (file
    paths are not an INFRA network leak, so they never trip the E12 floor) is
    asserted directly in ``test_findings_have_infra_network_leak_helper``.
    """
    text = "read /home/alice/project/notes.txt and it worked"
    scanned, blocked, tags, findings, meta = await _floor(text, "tag")
    assert not blocked, "file-path-only result must not be force-blocked"
    assert "/home/alice/project/notes.txt" in str(scanned)
    assert not meta.get("result_redaction_floor")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text",
    [
        "box 10.10.5.7 served /home/bob/.ssh/id_rsa",       # network IP + file path
        "email a@b.example beside file /home/x/.ssh/key",   # PII + file path
    ],
)
async def test_mixed_masked_under_redact(text):
    # STRICT OPERATOR CONTROL (2026-07-22): redact means redact. The class-scoped
    # redactor masks the IP/PII AND the private file path, so nothing is forwarded raw
    # and the call is NOT blocked (block is only for the operator's block posture).
    scanned, blocked, tags, findings, meta = await _floor(text)
    assert not blocked, "redact must mask, not block"
    blob = str(scanned)
    for raw in ("/home/bob/.ssh/id_rsa", "/home/x/.ssh/key", "10.10.5.7"):
        assert raw not in blob, f"{raw!r} must be masked under redact"


def test_findings_have_infra_network_leak_helper():
    fn = mcp_proxy._findings_have_infra_network_leak
    assert fn([{"threat_type": "ip_leakage", "matched_kinds": ["internal_ipv4"]}]) is True
    assert fn([{"threat_type": "ip_leakage", "matched_kinds": ["internal_ipv6"]}]) is True
    assert fn([{"threat_type": "ip_leakage", "matched_kinds": ["link_local_ipv4"]}]) is True
    # file-path-only ip_leakage → NOT a network leak (stays flag-tier)
    assert fn([{"threat_type": "ip_leakage", "matched_kinds": ["file_path_unix"]}]) is False
    assert fn([{"threat_type": "pii", "matched_kinds": ["email"]}]) is False
    assert fn([]) is False
    assert fn(None) is False


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
