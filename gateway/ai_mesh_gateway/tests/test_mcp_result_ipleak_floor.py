"""CHG-0074: internal NETWORK IP leakage in a tool RESULT was DETECTED + TAGGED
(INFRA) but, under the default ``tag`` scan action, egressed RAW — the E12
result-redaction floor only fired for secret/PII (``_findings_have_secret_or_pii``),
never for the ``ip_leakage`` class. That is a fail-OPEN asymmetric with PII/secret:
an internal / cloud-metadata IP in a tool result is the same infra-disclosure class.

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


async def _floor(text):
    """Run the real result-redaction floor under the DEFAULT (tag) action."""
    return await mcp_proxy._scan_tool_result_floor(
        text, tool_name="fetch", enabled_info=None,
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


@pytest.mark.asyncio
@pytest.mark.parametrize(("name", "text", "raw"), _NETWORK_LEAKS)
async def test_internal_network_leak_is_floored_not_raw(name, text, raw):
    scanned, blocked, tags, findings, meta = await _floor(text)
    # masked (floor) — never forwarded raw
    assert not (raw in str(scanned) and not blocked), f"{name}: {raw!r} egressed RAW"
    assert meta.get("result_redaction_floor") or blocked
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
    assert meta.get("result_redaction_floor") or blocked


@pytest.mark.asyncio
async def test_file_path_only_stays_raw_not_blocked():
    # File paths are deliberately flag-tier (FP-prone in legit code answers): they
    # must NOT trigger the floor (which would force-block a benign file/code result).
    text = "read /home/alice/project/notes.txt and it worked"
    scanned, blocked, tags, findings, meta = await _floor(text)
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
async def test_mixed_unmaskable_fails_closed(text):
    # redact_all masks the IP/PII but CANNOT mask the private file path; the floor
    # re-scan blocks on the survivor and that block must PROPAGATE (fail-closed),
    # not be swallowed into a raw forward.
    scanned, blocked, tags, findings, meta = await _floor(text)
    assert blocked, "mixed maskable+unmaskable leak must fail closed (block)"


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
