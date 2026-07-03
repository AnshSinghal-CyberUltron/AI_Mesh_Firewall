"""CHG-0138 regression-lock: the sandbox agent's SSRF IP classifier
(_resolved_ip_blocked, CHG-0067) must block EVERY internal / reserved / cloud-metadata
address class — including IPv6 and the classic IPv4-MAPPED-IPv6 bypass
(``::ffff:169.254.169.254``) — and allow public addresses.

This is verified-sound today (Python's ``ipaddress`` classifies the mapped form as
private), but the coverage was untested. A refactor of the classifier (or a Python
downgrade whose ``ipaddress`` mis-handles mapped IPv6) that broke IPv6/mapped coverage
would silently re-open SSRF egress to cloud metadata / internal services — this locks it.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

SANDBOX_IMAGE = Path(__file__).resolve().parents[2]
if str(SANDBOX_IMAGE) not in sys.path:
    sys.path.insert(0, str(SANDBOX_IMAGE))

from agent.upstream_manager import (  # noqa: E402
    UpstreamError,
    _assert_upstream_not_ssrf,
    _resolved_ip_blocked,
    _validate_upstream,
)

_MUST_BLOCK = [
    "169.254.169.254",          # AWS/GCP/Azure IPv4 metadata
    "fd00:ec2::254",            # AWS IPv6 metadata
    "::ffff:169.254.169.254",   # IPv4-mapped IPv6 metadata (classic SSRF bypass)
    "::ffff:a9fe:a9fe",         # same, hex form
    "::ffff:10.0.0.1",          # IPv4-mapped IPv6 private
    "10.0.0.5", "172.16.0.1", "192.168.1.1",  # IPv4 private
    "127.0.0.1",                # IPv4 loopback
    "169.254.1.1",              # IPv4 link-local
    "::1",                      # IPv6 loopback
    "fe80::1",                  # IPv6 link-local
    "fc00::1", "fd12:3456::1",  # IPv6 ULA (private)
    "0.0.0.0", "::",            # unspecified
    "not-an-ip",                # unparseable → fail-closed (blocked)
]

_MUST_ALLOW = [
    "8.8.8.8", "1.1.1.1",              # public IPv4
    "2606:4700:4700::1111",           # public IPv6 (Cloudflare)
    "93.184.216.34",                  # public IPv4 (example.com)
]


@pytest.mark.parametrize("ip", _MUST_BLOCK)
def test_ssrf_classifier_blocks_internal(ip):
    assert _resolved_ip_blocked(ip) is not None, f"{ip} must be blocked by the SSRF guard"


@pytest.mark.parametrize("ip", _MUST_ALLOW)
def test_ssrf_classifier_allows_public(ip):
    assert _resolved_ip_blocked(ip) is None, f"{ip} is public and must be allowed"


@pytest.mark.asyncio
async def test_assert_ssrf_blocks_literal_mapped_ipv6_metadata():
    # End-to-end: a literal IPv4-mapped-IPv6 metadata host in the upstream URL must be
    # rejected by _assert_upstream_not_ssrf (egress denied), not just the classifier.
    with pytest.raises(UpstreamError, match="egress denied"):
        await _assert_upstream_not_ssrf("::ffff:169.254.169.254")


@pytest.mark.asyncio
async def test_assert_ssrf_allows_public_literal_ip():
    await _assert_upstream_not_ssrf("8.8.8.8")  # must not raise


# ── CHG-0147: egress ALLOWLIST exact-match lock. _validate_upstream matches the URL host
# against allowed_hosts via NORMALIZED (lowercased, trailing-dot-stripped, IDNA) EXACT set
# membership — never a substring/suffix/subdomain match. A regression to endswith()/`in`
# would open the classic allowlist bypass (exfil to attacker host that merely *contains* an
# allowed host as a suffix). These lock the fail-closed exact-match invariant. ────────────

def _up(url, allowed):
    return {"url": url, "allowed_hosts": allowed}


# (label, url_host_in_url, allowed) that MUST be DENIED (-32002 egress denied)
_DENY = [
    ("subdomain_of_allowed", "https://evil.mcp.example.com/mcp", ["mcp.example.com"]),
    ("suffix_string_attack", "https://notmcp.example.com/mcp", ["mcp.example.com"]),   # ends-with, not label-aligned
    ("allowed_as_left_label", "https://mcp.example.com.evil.com/mcp", ["mcp.example.com"]),
    ("wholly_different_host", "https://attacker.test/mcp", ["mcp.example.com"]),
    ("ip_not_in_allowlist", "https://93.184.216.34/mcp", ["203.0.113.10"]),
]


@pytest.mark.parametrize("label,url,allowed", _DENY, ids=[d[0] for d in _DENY])
def test_egress_allowlist_denies_non_exact(label, url, allowed):
    with pytest.raises(UpstreamError, match="egress denied"):
        _validate_upstream(_up(url, allowed))


# (label, url, allowed) that MUST be ALLOWED (no raise) — normalized exact match.
_ALLOW = [
    ("exact", "https://mcp.example.com/mcp", ["mcp.example.com"]),
    ("case_insensitive_url", "https://MCP.Example.COM/mcp", ["mcp.example.com"]),
    ("case_insensitive_allow", "https://mcp.example.com/mcp", ["MCP.EXAMPLE.COM"]),
    ("trailing_dot_in_url", "https://mcp.example.com./mcp", ["mcp.example.com"]),
    ("trailing_dot_in_allow", "https://mcp.example.com/mcp", ["mcp.example.com."]),
    ("ip_exact", "https://93.184.216.34/mcp", ["93.184.216.34"]),
    ("second_of_multiple", "https://b.example.com/mcp", ["a.example.com", "b.example.com"]),
]


@pytest.mark.parametrize("label,url,allowed", _ALLOW, ids=[a[0] for a in _ALLOW])
def test_egress_allowlist_allows_normalized_exact(label, url, allowed):
    # _validate_upstream returns None (does not raise) when the host is allowlisted.
    assert _validate_upstream(_up(url, allowed)) is None


def test_egress_allowlist_required_fail_closed():
    # Empty/missing allowed_hosts must fail closed (allowlist is mandatory).
    with pytest.raises(UpstreamError, match="allowed_hosts is required"):
        _validate_upstream(_up("https://mcp.example.com/mcp", []))
