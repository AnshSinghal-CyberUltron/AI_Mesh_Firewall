# Feature: gw29-killswitch-isolation, Property 8: a kill cuts a live stream within one more chunk
# Validates: Requirements 5.1, 5.2 (cut bound = visibility in RAM + at most one chunk interval)
"""P8a over random stream lengths, engage points, endpoints and scopes; P8b split visibility."""

from __future__ import annotations

import asyncio
import random

from tests.edge._lgw29_app import ENDPOINTS, Harness, words

SEED = 29008
ITERATIONS = 150
SCOPES = (
    "global",
    "org:gw15-conformance-org",
    "model:gpt-test",
    "om:gw15-conformance-org:gpt-test",
)


def _count(path: str, text: str) -> int:
    marker = '"type":"response.output_text.delta"' if path == "/v1/responses" else '"content"'
    return text.count(marker)


def test_property_8a_a_stream_is_cut_after_at_most_one_more_chunk() -> None:
    rng = random.Random(SEED)
    for i in range(ITERATIONS):
        length = rng.randint(4, 14)
        engage_at = rng.randint(0, length - 4)
        path, build = rng.choice(ENDPOINTS)
        scope = rng.choice(SCOPES)
        tag = f"seed={SEED} i={i} len={length} at={engage_at} path={path} scope={scope}"

        def kill(h: Harness, index: int, at: int = engage_at, sc: str = scope) -> None:
            if index == at:
                h.engage(sc)

        h = Harness(words(" ".join(f"w{n}" for n in range(length))), between=kill)
        result = asyncio.run(h.call(path, build(stream=True)))
        assert "stream_killed" in result.text, tag
        assert "[DONE]" not in result.text, tag
        assert _count(path, result.text) <= engage_at + 3, (tag, _count(path, result.text))
        assert len([r for r in h.records if r.outcome == "terminated"]) == 1, tag
        assert h.budget.net > 0, tag  # a dispatched request keeps its spend (C29)


def test_property_8b_visibility_is_immediate_and_freshness_is_a_separate_bound() -> None:
    rng = random.Random(SEED + 1)
    for i in range(ITERATIONS):
        h = Harness()
        scope = rng.choice(SCOPES)
        h.engage(scope)  # the snapshot applied the record: from here the gate refuses at once
        path, build = rng.choice(ENDPOINTS)
        result = asyncio.run(h.call(path, build(stream=bool(rng.getrandbits(1)))))
        assert result.status == 503 and h.provider.open_calls == 0, (SEED, i, scope, path)
