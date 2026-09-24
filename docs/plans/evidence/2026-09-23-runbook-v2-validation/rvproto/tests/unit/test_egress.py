"""Streaming output: holdback safety/minimality properties and the SSE codec."""

from __future__ import annotations

import orjson
from hypothesis import given, settings
from hypothesis import strategies as st

from rvproto.detect.deterministic import DeterministicDetectors
from rvproto.detect.holdback import hold_start
from rvproto.detect.matcher import Matcher
from rvproto.domain.text import redact_text
from rvproto.edge.inspect import PlanInspector
from rvproto.egress.sse import SseParser
from rvproto.egress.stream import StreamPipeline
from rvproto.plan.compiler import compile_plan
from rvproto.plan.fixtures import org_a
from rvproto.runtime.metrics import Registry

M = Matcher()
INS = PlanInspector(M, DeterministicDetectors(M), compile_plan(org_a()))
SECRETS = [
    "alice.smith@example.com", "AKIAIOSFODNN7EXAMPLE", "ghp_" + "Ab1" * 12 + "Z",
    "4111 1111 1111 1111", "(415) 555-2671", "123-45-6789", "10.0.0.254",
    "-----BEGIN RSA PRIVATE KEY-----", "api_key = 'Zx9Qw8Er7Ty6Ui5Op4As'",
    "sk_live_4eC39HqLyjWDarjtT1zdp7dc", "xoxb-1234567890-abcdefghij",
]
WORDS = ["the", "river", "stone", "cloud,", "maple.", "a", "quiet", "green", "orbit", "42", "x-y"]


def oneshot(text: str) -> str:
    d = INS.decide([INS.hits(text)])
    return redact_text(text, [(t.span.start, t.span.end, t.replacement) for t in d.transformations])


def stream(chunks: list[str]) -> tuple[str, list[str]]:
    pipe = StreamPipeline(INS, Registry(0), ceiling=1 << 20, inject_hold=None)
    released: list[str] = []
    for c in chunks:
        released.append(pipe._feed(pipe.stats, ("c", 0), c, 1, False, []))
    released.append(pipe._feed(pipe.stats, ("c", 0), "", 2, True, []))
    return "".join(released), released


@st.composite
def text_and_cuts(draw: st.DrawFn) -> tuple[str, list[str]]:
    parts = draw(st.lists(st.one_of(st.sampled_from(WORDS), st.sampled_from(SECRETS)), min_size=1,
                          max_size=14))
    text = " " + " ".join(parts) + draw(st.sampled_from(["", " ", ".", "\n"]))
    cuts = sorted(set(draw(st.lists(st.integers(1, max(1, len(text) - 1)), max_size=12))))
    pieces, prev = [], 0
    for c in cuts:
        pieces.append(text[prev:c])
        prev = c
    pieces.append(text[prev:])
    return text, [p for p in pieces if p]


@settings(max_examples=3000, deadline=None)
@given(text_and_cuts())
def test_stream_equals_oneshot_and_never_leaks(tc: tuple[str, list[str]]) -> None:
    text, chunks = tc
    out, released = stream(chunks)
    assert out == oneshot(text)
    for s in SECRETS:
        if s in text:
            assert s not in out
            for r in released:  # no partial prefix of a secret longer than 4 chars leaks
                for k in range(5, len(s)):
                    assert s[:k] not in r or s[:k] in oneshot(text)


def test_minimal_hold_is_the_trailing_word_only() -> None:
    assert hold_start(" the quick") == 5
    assert hold_start(" the quick ") == 11
    assert hold_start("call (415) 555-") == 5
    assert hold_start("x -----BEGIN RSA PRI") == 2
    assert hold_start("the api_key = ") == 4


def test_split_aws_across_three_chunks() -> None:
    out, released = stream([" key", " AKIAIOSF", "ODNN7EXA", "MPLE", " end"])
    assert out == " key [REDACTED:secret.aws] end"
    assert all("AKIA" not in r for r in released)


def test_sse_parser_random_splits() -> None:
    frames = [b"data: " + orjson.dumps({"i": i}) + b"\r\n\r\n" for i in range(20)] + [b"data: [DONE]\n\n"]
    raw = b"".join(frames)
    for step in (1, 2, 3, 7, 50):
        p = SseParser()
        got = []
        for k in range(0, len(raw), step):
            got.extend(p.feed(raw[k:k + step]))
        assert got[-1] == b"[DONE]" and [orjson.loads(g)["i"] for g in got[:-1]] == list(range(20))
