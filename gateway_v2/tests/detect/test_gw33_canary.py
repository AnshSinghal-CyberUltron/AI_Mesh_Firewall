"""GW33 task 7.4: the canary detector (secrecy, obfuscation, absolute offsets)."""
from __future__ import annotations

import random

from gateway_v2.detect.output.canary import CanaryDetector
from gateway_v2.domain import Category, FindingStatus

_TOKEN = "cnry_abcdefghijklmnopqrstuvwxyz"[:5] + "q7w2e3r4t5y6u7i2o3p4a5s6d7"
_ITERATIONS = 2_000


def _spans(detector: CanaryDetector, text: str) -> list[tuple[int, int]]:
    out = []
    for f in detector.detect(text):
        assert f.status is FindingStatus.EXECUTED and f.category is Category.CANARY
        assert f.evidence is None
        out.append((f.spans[0].start, f.spans[0].end))
    return out


def test_plain_and_obfuscated_forms_are_found_at_original_offsets() -> None:
    d = CanaryDetector(_TOKEN)
    assert _spans(d, f"x {_TOKEN} y") == [(2, 2 + len(_TOKEN))]
    upper = _TOKEN.upper()
    assert _spans(d, f"x {upper} y") == [(2, 2 + len(upper))]
    zw = "\u200b".join(_TOKEN)  # zero-width interleaved
    assert _spans(d, f"x {zw} y") == [(2, 2 + len(zw))]
    pct = "".join(f"%{ord(c):02x}" for c in _TOKEN)
    assert _spans(d, f"x {pct} y") == [(2, 2 + len(pct))]
    ent = "".join(f"&#{ord(c)};" for c in _TOKEN)
    assert _spans(d, f"x {ent} y") == [(2, 2 + len(ent))]
    assert _spans(d, "nothing here") == []
    assert len(_spans(d, f"{_TOKEN} and {_TOKEN}")) == 2


def test_no_token_in_repr_str_or_finding() -> None:
    d = CanaryDetector(_TOKEN)
    for rendered in (repr(d), str(d), f"{d}", repr(d.detect(_TOKEN)), d.detector_version):
        assert _TOKEN not in rendered and _TOKEN.upper() not in rendered
    assert d.required_capabilities == frozenset()
    assert d.detect(_TOKEN)[0].evidence is None


def test_detect_from_returns_absolute_offsets_and_shares_one_core() -> None:
    d = CanaryDetector(_TOKEN)
    text = "pad " * 50 + _TOKEN + " tail"
    start = text.index(_TOKEN)
    assert [(f.spans[0].start, f.spans[0].end) for f in d.detect_from(text, start - 5)] == [
        (start, start + len(_TOKEN)),
    ]
    assert d.detect_from(text, start + 1) == ()  # the token begins before the window
    assert d.detect(text) == d.detect_from(text, 0)


def test_rule_id_is_stamped_and_budget_miss_is_unavailable() -> None:
    d = CanaryDetector(_TOKEN, rule_id="out_canary")
    assert d.detect(_TOKEN)[0].detector == "out_canary"
    findings = d.detect("%41" * 6000)
    assert [f.status for f in findings] == [FindingStatus.UNAVAILABLE]
    assert [f.status for f in CanaryDetector("").detect("x")] == [FindingStatus.UNAVAILABLE]


def test_random_placements_are_always_found_exactly() -> None:
    rng = random.Random(33_010)
    d = CanaryDetector(_TOKEN)
    for _ in range(_ITERATIONS):
        pre = "".join(rng.choice("abc .,\n") for _ in range(rng.randint(0, 400)))
        post = "".join(rng.choice("abc .,\n") for _ in range(rng.randint(0, 40)))
        assert _spans(d, pre + _TOKEN + post) == [(len(pre), len(pre) + len(_TOKEN))]
