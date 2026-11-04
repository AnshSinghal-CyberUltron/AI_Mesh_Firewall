"""GW34 V13: the ``amf.trace`` frame is an SSE COMMENT line, never a named event or a chunk.

Owner decision of 4 Nov 2026: ``: amf.trace {json}\\n\\n``. Every compliant SSE parser skips a
comment, so the stock Python and Node ``openai`` SDK iterators never see the trace (R1.6).
Feature: gw34-sdk-compat, Property 9: the trace comment is one line, round-trips, and is inert.
"""
from __future__ import annotations

import json
import random

from gateway_v2.edge.wire.responses_events import ResponsesEventTranslator
from gateway_v2.edge.wire.sse import TRACE_COMMENT_PREFIX, SSEDecoder, SSEEncoder
from gateway_v2.egress.stream import DownstreamFrame

_SEED = 0x6A34_0013
_ITERATIONS = 200
_ALPHABET = "abc \u00e9\u4e2d\U0001f680\"\\\n\r\t:"


def _fields(rng: random.Random) -> dict[str, object]:
    text = "".join(rng.choice(_ALPHABET) for _ in range(rng.randint(0, 30)))
    return {
        "produced_mode": rng.choice(["incremental", "strict_withhold"]),
        "degenerated_to_strict": rng.random() < 0.5,
        "zeroshield": {"action": "allow", "note": text},
    }


def test_trace_is_one_compact_comment_line() -> None:
    rng = random.Random(_SEED)
    for _ in range(_ITERATIONS):
        fields = _fields(rng)
        frame = SSEEncoder().trace(fields)
        assert frame.startswith(TRACE_COMMENT_PREFIX) and frame.endswith(b"\n\n")
        line = frame[:-2]
        assert b"\n" not in line and b"\r" not in line  # single line, newlines JSON-escaped
        assert b"event:" not in frame and b"\ndata:" not in frame  # not a named event
        assert json.loads(line[len(TRACE_COMMENT_PREFIX):]) == fields


def test_trace_is_ascii_compact_and_does_not_terminate_the_encoder() -> None:
    encoder = SSEEncoder()
    frame = encoder.trace({"k": "\u00e9 \U0001f680"})
    assert frame.isascii() and b", " not in frame and b": " not in frame[len(TRACE_COMMENT_PREFIX):]
    assert not encoder.terminated


def test_decoder_skips_the_trace_for_content_and_exposes_it() -> None:
    rng = random.Random(_SEED + 1)
    for _ in range(_ITERATIONS):
        fields = _fields(rng)
        encoder = SSEEncoder()
        raw = encoder.content(DownstreamFrame(text_deltas=(("content", "hi"),)))
        raw += encoder.trace(fields) + encoder.done()
        decoder = SSEDecoder()
        frames = []
        pos = 0
        while pos < len(raw):  # random split points: confluence with the comment frame
            step = rng.randint(1, 9)
            frames.extend(decoder.feed(raw[pos:pos + step]))
            pos += step
        assert [f.text_deltas for f in frames] == [(("content", "hi"),)]
        assert decoder.trace == fields
        assert not decoder.malformed() and decoder.finished()


def test_an_undecodable_trace_comment_is_malformed_not_content() -> None:
    decoder = SSEDecoder()
    assert decoder.feed(b": amf.trace {not json\n\n") == ()
    assert decoder.malformed() and decoder.trace is None


def test_an_ordinary_comment_is_not_taken_as_the_trace() -> None:
    decoder = SSEDecoder()
    decoder.feed(b": amf.tracer {}\n\n")
    assert decoder.trace is None


def test_responses_translator_reads_the_comment_and_does_not_forward_it() -> None:
    console = {"zeroshield": {"action": "allow"}, "pipeline_trace": {"final_action": "allow"}}
    encoder = SSEEncoder()
    raw = encoder.content(DownstreamFrame(text_deltas=(("content", "hi"),)))
    raw += encoder.trace(console) + encoder.done()
    translator = ResponsesEventTranslator(response_id="r", item_id="m", model="x", created_at=1)
    out = translator.start() + translator.feed(raw) + translator.finish()
    assert b"amf.trace" not in out
    completed = [b for b in out.split(b"\n\n") if b.startswith(b"event: response.completed")]
    [frame] = completed
    response = json.loads(frame.split(b"data: ", 1)[1])["response"]
    assert response["zeroshield"] == console["zeroshield"]
    assert response["pipeline_trace"] == console["pipeline_trace"]
    assert translator.error_code is None


def test_responses_translator_fails_closed_on_an_undecodable_trace() -> None:
    translator = ResponsesEventTranslator(response_id="r", item_id="m", model="x", created_at=1)
    translator.feed(b": amf.trace [1]\n\ndata: [DONE]\n\n")
    assert translator.error_code == "stream_malformed_upstream"
