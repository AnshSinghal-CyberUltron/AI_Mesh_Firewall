# ruff: noqa: E501  (GW33 test file: long assertion/docstring lines kept readable)
"""GW33 task 6 / Property 14: the holdback is unchanged except the `canary` class label."""
from __future__ import annotations

import random

from gateway_v2.detect import holdback
from gateway_v2.detect.holdback import TRADE_OFF, TokenIndex, classify_hold, scan
from gateway_v2.detect.windowing import max_pattern_length

_ITERATIONS = 3_000
_ALPHA = "abcdefghijklmnopqrstuvwxyz234567"


_PRODUCTION_CLASSIFIER = holdback._classify_word_suffix


def _reference_classify(suffix: str) -> str | None:
    """The pre-GW33 word-suffix classifier: the production one minus the canary arm."""
    if suffix.startswith("cnry_"):
        # Pre-change, a canary-shaped run fell through every arm and classified as api_token.
        return None
    return _PRODUCTION_CLASSIFIER(suffix)


def _canary() -> str:
    return "cnry_" + "".join(random.Random(7).choice(_ALPHA) for _ in range(26))


def test_cnry_suffix_classifies_as_canary_and_nothing_else_changes() -> None:
    assert classify_hold("see cnry_abcde", 4) == ("canary", False)
    assert classify_hold("see cnry_", 4) == ("canary", False)
    assert classify_hold("see cnry", 4) == ("api_token", False)  # shorter lead: unchanged
    assert TRADE_OFF["canary"] is holdback.TradeOffOutcome.TERMINATE


def test_property_14_scan_is_identical_to_the_pre_change_scan_except_held_class() -> None:
    """Feature: gw33-output-parity, Property 14: Holdback unchanged. Validates 8.1-8.3 as amended."""
    # Feature: gw33-output-parity, Property 14: Holdback unchanged
    rng = random.Random(33_014)
    window = max_pattern_length()
    token_index = TokenIndex()
    pieces = ["cnry_", "cnry_" + "ab2c3", " ", ".", "/", "=", "10.0.0.1", "hello", "AKIA", "ey",
              "4111 1111", "x@y.com", "-", "_", "&#x41;", "\u200b"]
    saw_canary = 0
    for _ in range(_ITERATIONS):
        buf = "".join(rng.choice(pieces) + rng.choice(_ALPHA) * rng.randint(0, 4)
                      for _ in range(rng.randint(1, 8)))
        for final in (False, True):
            got = scan(buf, final=final, hold_cap_tokens=3, token_index=token_index, window=window)
            # Reference: the same scan with the canary arm disabled -> class falls back to api_token.
            original = holdback._classify_word_suffix
            try:
                holdback._classify_word_suffix = _reference_classify  # type: ignore[assignment]
                ref = scan(buf, final=final, hold_cap_tokens=3, token_index=token_index,
                           window=window)
            finally:
                holdback._classify_word_suffix = original  # type: ignore[assignment]
            for name in ("hold_index", "forced_release", "forced_release_index", "overflow"):
                assert getattr(got, name) == getattr(ref, name), (buf, final, name)
            if got.held_class == "canary":
                saw_canary += 1
                assert ref.held_class == "api_token"
            else:
                assert got.held_class == ref.held_class
            held_tokens = token_index.tokens_in(buf[got.hold_index:])
            assert held_tokens <= 3 or got.forced_release or got.overflow or final
    assert saw_canary > 50


def test_a_canary_shaped_run_is_one_token_and_is_never_force_released() -> None:
    token_index = TokenIndex()
    buf = "answer: " + _canary()
    result = scan(buf, final=False, hold_cap_tokens=3, token_index=token_index,
                  window=max_pattern_length())
    assert result.hold_index == len("answer: ")
    assert result.held_class == "canary"
    assert result.forced_release is False and result.overflow is False
    assert token_index.tokens_in(_canary()) == 1


def test_neither_table_has_ip_leak_or_exfil_sink_and_the_mirror_agrees() -> None:
    from gateway_v2.egress.output_guard import _CLASS_DISPOSITION

    assert set(TRADE_OFF) == set(_CLASS_DISPOSITION)
    assert not {"ip_leak", "exfil_sink"} & set(TRADE_OFF)
