"""CHG-0073: internal IP-leakage coverage was IPv4-RFC1918-only. A tool RESULT
containing an internal IPv6 (ULA fc00::/7, link-local fe80::/10), a cloud-metadata /
link-local IPv4 (169.254.169.254 IMDS), or a CGNAT IPv4 (100.64.0.0/10) egressed RAW.

These are now (a) DETECTED by detect_ip_leakage (drives the tier-1 block/redact
decision), (b) TAGGED ["INFRA"], and (c) MASKED by redact_all. Critically, the
redact_all infra loop iterates a HARDCODED key tuple (not the dict), so this suite
also guards against the detect/redact DIVERGENCE (flag-yet-forward-raw fail-open) —
the byte-level assertions below are the authoritative proof.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

from patterns import (  # noqa: E402
    detect_ip_leakage,
    get_compliance_tags,
    redact_all,
)

# (key, egress-text, the raw address that MUST NOT survive redaction)
_LEAK_CASES = [
    ("internal_ipv6", "server at fd12:3456:789a:0001:0000:0000:0000:0001 up",
     "fd12:3456:789a:0001:0000:0000:0000:0001"),
    ("internal_ipv6", "peer fc00::1234:5678 mesh node", "fc00::1234:5678"),
    ("internal_ipv6", "iface fe80::1ff:fe23:4567:890a up", "fe80::1ff:fe23:4567:890a"),
    ("internal_ipv6", "our ULA prefix is fd00:: today", "fd00::"),
    ("link_local_ipv4", "curl http://169.254.169.254/latest/meta-data/iam/",
     "169.254.169.254"),
    ("link_local_ipv4", "client NATed behind 100.64.12.34 now", "100.64.12.34"),
    ("link_local_ipv4", "edge 100.127.255.1 seen", "100.127.255.1"),
]


@pytest.mark.parametrize(("key", "text", "raw"), _LEAK_CASES)
def test_internal_addr_detected_tagged_masked(key, text, raw):
    found = detect_ip_leakage(text)
    assert key in found, f"{key} not detected in {text!r}: {found}"
    assert "INFRA" in get_compliance_tags(list(found.keys()))
    # byte-level: the raw internal address must be ABSENT from the egress
    assert raw not in redact_all(text), f"{raw!r} survived redaction (fail-open)"


def test_rfc1918_control_still_masked():
    assert "10.0.5.7" not in redact_all("internal box 10.0.5.7 up")


@pytest.mark.parametrize("loopback", ["connect 127.0.0.1:8080", "bind ::1 only"])
def test_loopback_intentionally_not_flagged(loopback):
    # loopback is as benign as localhost — deliberately unflagged (FP-avoidance)
    assert not detect_ip_leakage(loopback)
    assert redact_all(loopback) == loopback


@pytest.mark.parametrize(
    "benign",
    [
        "mac fc:00:11:22:33:44 device",       # MAC (2-hex groups) -> not internal_ipv6
        "commit fdb9753104ac merged",          # contiguous hex blob, no ':'
        "backup at 14:30:56 UTC",              # HH:MM:SS timestamp
        "error code fc00: retry later",        # 'fc00:' then space
        "version 100.5.2.1 shipped",           # 2nd octet <64 -> not CGNAT
        "route 169.1.254.9 external",          # 169.x but not 169.254.x
        "id fd00 and fe80 tokens",             # bare prefixes, no ':' group
        "uuid fd12-3456-789a-bcde-000011112222",  # hyphenated UUID
    ],
)
def test_no_false_positive_internal_ip(benign):
    assert "internal_ipv6" not in detect_ip_leakage(benign)
    assert "link_local_ipv4" not in detect_ip_leakage(benign)


@pytest.mark.parametrize(
    "pathological",
    ["fd00" + ":a" * 4000, "fe80" + ":" * 4000, "169.254." + "9" * 4000],
)
def test_linear_time_no_redos(pathological):
    # must not catastrophically backtrack; a bounded body returns immediately
    redact_all(pathological)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
