# Feature: gw29-killswitch-isolation, Property 8: a kill cuts live streams in bound (L29-1 local)
# Validates: Requirements 5.1, 5.2, AC1, AC2 (G-17: a 2 000-token stream cut after engage at +2 s)
"""Local equivalent of L29-1: engage a kill while streams run, injected clock, fake provider."""

from __future__ import annotations

import asyncio

from tests.edge._lgw29_app import Harness, chat, words

TOKENS = 2_000
TICK_S = 0.002  # injected seconds per upstream chunk: the stream would last 4 s
ENGAGE_AT_S = 2.0


def _stream_with_engage(scope: str) -> tuple[Harness, str, dict[str, float]]:
    seen: dict[str, float] = {}

    def between(h: Harness, index: int) -> None:
        h.rig.mono.now += TICK_S
        h.rig.wall.now += TICK_S
        elapsed = (index + 1) * TICK_S
        if "engaged" not in seen and elapsed >= ENGAGE_AT_S:
            seen["engaged"] = elapsed
            seen["index"] = float(index)
            h.engage(scope)

    h = Harness(words(" ".join(f"t{n}" for n in range(TOKENS))), between=between)
    result = asyncio.run(h.call("/v1/chat/completions", chat(stream=True)))
    return h, result.text, seen


def test_g17_a_2000_token_stream_is_cut_after_the_engage_at_plus_2s() -> None:
    for scope in ("global", "org:gw15-conformance-org", "model:gpt-test"):
        h, text, seen = _stream_with_engage(scope)
        assert "engaged" in seen, scope
        delivered = text.count('"content"')
        engage_index = int(seen["index"])
        assert "stream_killed" in text and "[DONE]" not in text, scope
        # the stream stops within a few chunks of the engage, nowhere near its 2 000 tokens
        assert engage_index < delivered <= engage_index + 3, (scope, delivered, engage_index)
        cut_s = (delivered - engage_index - 1) * TICK_S
        assert cut_s <= 3 * TICK_S, (scope, cut_s)
        terminated = [r for r in h.records if r.outcome == "terminated"]
        assert len(terminated) == 1, scope


def test_a_kill_under_load_leaves_other_models_serving() -> None:
    h, _text, _seen = _stream_with_engage("model:gpt-test")
    other = asyncio.run(h.call("/v1/chat/completions", chat("other-model")))
    assert other.status == 200
    refused = asyncio.run(h.call("/v1/chat/completions", chat("gpt-test")))
    assert refused.status == 503
    assert refused.headers["x-should-retry"] == "false"
    assert int(refused.headers["retry-after"]) >= 1


def test_disengage_readmits_new_requests() -> None:
    h = Harness()
    h.engage("global")
    assert asyncio.run(h.call("/v1/chat/completions", chat())).status == 503
    from tests.admit._lgw29_support import ks

    h.rig.engage(ks("global", on=False, feed_seq=2))
    assert asyncio.run(h.call("/v1/chat/completions", chat())).status == 200
