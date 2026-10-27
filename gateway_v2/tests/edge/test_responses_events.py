# Feature: gw15-responses-close, Property 3: Typed events stay in order, and a withhold ends failed
# Validates: Requirements 5.2, 5.3, 5.4, 6.3, 7.1, 7.3, 7.5
"""Chat SSE to Responses event translator.

House idiom: a seeded ``random.Random``, no ``hypothesis``, at least 10,000 iterations.
The chat bytes come from the shipped ``SSEEncoder``, so the translator is tested against
the frames the chat route writes, split at random byte offsets.
"""

from __future__ import annotations

import json
import random

from gateway_v2.edge.wire.responses_events import (
    COMPLETED,
    FAILED,
    ResponsesEventTranslator,
)
from gateway_v2.edge.wire.sse import SSEEncoder
from gateway_v2.egress.stream import DownstreamFrame

_SEED = 0x6C15_0003
_ITERATIONS = 10_000
_ALPHABET = "abcdefghij klmnop"
_REQUIRED = (
    "response.created",
    "response.output_item.added",
    "response.content_part.added",
    "response.output_text.delta",
    "response.output_text.done",
    "response.output_item.done",
    "response.completed",
)
_CONSOLE = {
    "produced_mode": "incremental",
    "zeroshield": {"action": "allow", "evidence": "RAW-SECRET"},
    "pipeline_trace": {"stages": [{"name": "output_guardrail", "action": "allow"}]},
}


def _ctx(seed: int, i: int, **extra: object) -> str:
    body = " ".join(f"{key}={value!r}" for key, value in extra.items())
    return f"seed={seed:#x} iter={i} {body}"


def _deltas(rng: random.Random) -> list[str]:
    return [
        "".join(rng.choice(_ALPHABET) for _ in range(rng.randint(1, 8)))
        for _ in range(rng.randint(1, 6))
    ]


def _chat_bytes(deltas: list[str], *, block_after: int | None) -> bytes:
    """What the chat route writes: content, then [DONE] or an output_blocked error, with a trace."""
    encoder = SSEEncoder()
    out = bytearray()
    released = deltas if block_after is None else deltas[:block_after]
    for text in released:
        out.extend(encoder.content(DownstreamFrame(text_deltas=(("content", text),))))
    if block_after is None:
        out.extend(encoder.trace(_CONSOLE))
        out.extend(encoder.done())
    else:
        out.extend(encoder.error("output_blocked"))
        out.extend(encoder.trace(_CONSOLE))
    return bytes(out)


def _split(rng: random.Random, raw: bytes) -> list[bytes]:
    cuts = sorted(rng.sample(range(1, len(raw)), k=min(len(raw) - 1, rng.randint(0, 6))))
    pieces: list[bytes] = []
    start = 0
    for cut in cuts:
        pieces.append(raw[start:cut])
        start = cut
    pieces.append(raw[start:])
    return pieces


def _events(raw: bytes) -> list[tuple[str, dict[str, object]]]:
    events: list[tuple[str, dict[str, object]]] = []
    for frame in raw.split(b"\n\n"):
        if not frame:
            continue
        lines = frame.decode("ascii").split("\n")
        assert lines[0].startswith("event: "), lines
        assert lines[1].startswith("data: "), lines
        name = lines[0][len("event: "):]
        payload = json.loads(lines[1][len("data: "):])
        assert payload["type"] == name, payload
        events.append((name, payload))
    return events


def _run(rng: random.Random, raw: bytes) -> list[tuple[str, dict[str, object]]]:
    translator = ResponsesEventTranslator(
        response_id="resp_test", item_id="msg_test", model="gpt-4o-mini", created_at=1
    )
    out = bytearray(translator.start())
    for piece in _split(rng, raw):
        out.extend(translator.feed(piece))
    out.extend(translator.finish())
    assert translator.finish() == b""
    return _events(bytes(out))


def test_property_event_order_and_failed_terminal() -> None:
    """Property 3: clean streams keep the seven-event order. A withhold ends on failed.

    Validates: Requirements 5.2, 5.3, 5.4, 6.3
    """
    rng = random.Random(_SEED)
    for i in range(_ITERATIONS):
        deltas = _deltas(rng)
        blocked = i % 2 == 1
        block_after = rng.randint(0, len(deltas)) if blocked else None
        events = _run(rng, _chat_bytes(deltas, block_after=block_after))
        names = [name for name, _ in events]
        sequence = [payload["sequence_number"] for _, payload in events]
        assert sequence == list(range(len(events))), _ctx(_SEED, i, sequence=sequence)
        if not blocked:
            firsts = [names.index(name) for name in _REQUIRED]
            assert firsts == sorted(firsts), _ctx(_SEED, i, names=names)
            delta_positions = [
                n for n, name in enumerate(names) if name == "response.output_text.delta"
            ]
            last_delta = max(delta_positions)
            assert last_delta < names.index("response.output_text.done"), _ctx(_SEED, i)
            assert names[-1] == COMPLETED, _ctx(_SEED, i, names=names)
            response = events[-1][1]["response"]
            assert isinstance(response, dict)
            assert response["output_text"] == "".join(deltas), _ctx(_SEED, i)
            assert response["status"] == "completed"
            assert response["zeroshield"] == {"action": "allow"}, _ctx(_SEED, i)
            assert "pipeline_trace" in response
            continue
        assert block_after is not None
        assert names[-1] == FAILED, _ctx(_SEED, i, names=names)
        assert COMPLETED not in names, _ctx(_SEED, i, names=names)
        assert names.count("response.output_text.delta") == block_after, _ctx(_SEED, i)
        response = events[-1][1]["response"]
        assert isinstance(response, dict)
        assert response["status"] == "failed", _ctx(_SEED, i)
        assert response["output_text"] == "", _ctx(_SEED, i)
        assert response["error"] == {
            "code": "output_blocked",
            "message": "The response stream did not complete.",
        }, _ctx(_SEED, i)
        assert "RAW-SECRET" not in json.dumps(response), _ctx(_SEED, i)


def test_trace_frame_is_not_forwarded() -> None:
    """``amf.trace`` is folded into the terminal object, not emitted as an event."""
    events = _run(random.Random(_SEED), _chat_bytes(["hi"], block_after=None))
    assert all(name != "amf.trace" for name, _ in events)


def test_stream_without_done_fails_closed() -> None:
    """A chat stream that stops without ``[DONE]`` or an error is not reported complete."""
    encoder = SSEEncoder()
    raw = encoder.content(DownstreamFrame(text_deltas=(("content", "partial"),)))
    translator = ResponsesEventTranslator(
        response_id="resp_test", item_id="msg_test", model="m", created_at=1
    )
    out = translator.start() + translator.feed(raw) + translator.finish()
    names = [name for name, _ in _events(out)]
    assert names[-1] == FAILED
    assert COMPLETED not in names


def test_finish_failure_code_fails_a_clean_stream() -> None:
    """The route can fail a clean stream, for example when the store write fails."""
    translator = ResponsesEventTranslator(
        response_id="resp_test", item_id="msg_test", model="m", created_at=1
    )
    out = translator.start() + translator.feed(_chat_bytes(["ok"], block_after=None))
    assert translator.final_response()["status"] == "completed"
    out += translator.finish(failure_code="response_store_unavailable")
    events = _events(out)
    assert events[-1][0] == FAILED
    response = events[-1][1]["response"]
    assert isinstance(response, dict)
    assert response["error"] == {
        "code": "response_store_unavailable",
        "message": "The response stream did not complete.",
    }


def test_malformed_chat_frame_fails_closed() -> None:
    """A chat frame that is not JSON ends the stream failed."""
    translator = ResponsesEventTranslator(
        response_id="resp_test", item_id="msg_test", model="m", created_at=1
    )
    out = translator.start() + translator.feed(b"data: {not json\n\ndata: [DONE]\n\n")
    out += translator.finish()
    events = _events(out)
    assert events[-1][0] == FAILED
    response = events[-1][1]["response"]
    assert isinstance(response, dict)
    assert response["error"] == {
        "code": "stream_malformed_upstream",
        "message": "The response stream did not complete.",
    }
