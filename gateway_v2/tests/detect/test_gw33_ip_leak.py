"""GW33 task 7.2 / Property 6: IP and infrastructure leak classification."""
from __future__ import annotations

import ipaddress
import random

from gateway_v2.detect.output.ip_leak import IpLeakDetector
from gateway_v2.domain import Category, FindingStatus
from gateway_v2.runtime.resources_output import DEFAULT_OUTPUT_BOUNDS, OutputBounds

_ITERATIONS = 3_000
_INTERNAL = [ipaddress.ip_network(n) for n in (
    "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "127.0.0.0/8", "169.254.0.0/16",
    "100.64.0.0/10",
)]
_PUBLIC = [ipaddress.ip_network(n) for n in ("8.0.0.0/8", "1.0.0.0/8", "52.0.0.0/8", "151.0.0.0/8")]
_DOC = [ipaddress.ip_network(n) for n in ("192.0.2.0/24", "198.51.100.0/24", "203.0.113.0/24")]


def _addr(rng: random.Random, net: ipaddress.IPv4Network) -> str:
    return str(ipaddress.IPv4Address(int(net.network_address) + rng.randrange(net.num_addresses)))


def _spans(text: str) -> list[tuple[int, int]]:
    out = []
    for f in IpLeakDetector().detect(text):
        assert f.status is FindingStatus.EXECUTED and f.category is Category.IP_LEAK
        assert f.evidence is None
        out.append((f.spans[0].start, f.spans[0].end))
    return out


def test_property_6_ip_classification() -> None:
    """Feature: gw33-output-parity, Property 6: IP classification. Validates 1.1-1.4."""
    # Feature: gw33-output-parity, Property 6: IP classification
    rng = random.Random(33_006)
    for _ in range(_ITERATIONS):
        internal = _addr(rng, rng.choice(_INTERNAL))
        public = _addr(rng, rng.choice(_PUBLIC))
        doc = _addr(rng, rng.choice(_DOC))
        text = f"a {public} b {internal}, c {doc} d"
        spans = _spans(text)
        start = text.index(internal)
        assert spans == [(start, start + len(internal))], text
        # Obfuscated forms are found at the ORIGINAL span.
        zw = internal.replace(".", ".\u200b", 1)
        t2 = f"x {zw} y"
        assert _spans(t2) == [(2, 2 + len(zw))]
        pct = internal.replace(".", "%2E")
        assert _spans(f"x {pct} y") == [(2, 2 + len(pct))]
        full = internal.translate({ord(c): ord(c) + 0xFEE0 for c in "0123456789."})
        assert _spans(f"x {full} y") == [(2, 2 + len(full))]


def test_ipv6_hostnames_and_negatives() -> None:
    assert _spans("::1") == [(0, 3)]
    assert _spans("go to fe80::1 now") == [(6, 13)]
    assert _spans("fd00:ec2::254") == [(0, 13)]
    assert _spans("map ::ffff:10.0.0.1 ok") == [(4, 19)]
    assert _spans("2001:db8::1 and 2606:4700::1111") == []  # documentation and public
    assert _spans("12:30:45 meeting at 10:30") == []
    assert _spans("db.internal and svc.cluster.local") == [(0, 11), (16, 33)]
    assert _spans("see http://intranet/x") == [(11, 19)]
    assert _spans("connect localhost:8080 ok") == [(8, 17)]
    assert _spans("example.com and docs.python.org") == []
    for clean in ("10.2.3", "v1.2.3.4-rc1", "version 1.2.3.4.5", "256.1.1.1", "8.8.8.8", "1.2.3.4"):
        assert _spans(clean) == [], clean


def test_leading_zero_octets_are_folded_not_trusted() -> None:
    assert _spans("010.000.000.001") == [(0, 15)]


def test_candidate_cap_yields_one_unavailable_for_the_remainder() -> None:
    bounds = OutputBounds(
        DEFAULT_OUTPUT_BOUNDS.exfil_min_payload_chars, DEFAULT_OUTPUT_BOUNDS.exfil_min_encoded_run,
        DEFAULT_OUTPUT_BOUNDS.exfil_min_label_chars, DEFAULT_OUTPUT_BOUNDS.exfil_max_host_chars,
        3, DEFAULT_OUTPUT_BOUNDS.decode_expansion_bound, DEFAULT_OUTPUT_BOUNDS.canary_token_chars,
    )
    text = " ".join(f"10.0.0.{n}" for n in range(1, 8))
    findings = IpLeakDetector(bounds=bounds).detect(text)
    assert [f.status for f in findings] == [FindingStatus.EXECUTED] * 3 + [FindingStatus.UNAVAILABLE]
    assert findings[-1].spans == ()


def test_decode_budget_miss_is_unavailable_and_rule_id_is_stamped() -> None:
    detector = IpLeakDetector(rule_id="out_ip_leak")
    findings = detector.detect("%41" * 5000)  # exceeds the decode-part budget
    assert [f.status for f in findings] == [FindingStatus.UNAVAILABLE]
    assert findings[0].detector == "out_ip_leak"
    assert detector.required_capabilities == frozenset()
    assert len(detector.detector_version) == 64
