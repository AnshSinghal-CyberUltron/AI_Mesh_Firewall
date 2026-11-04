"""GW34 Task 5: encoder framing (one merged delta, stable identity, role once) and Property 6."""
from __future__ import annotations

import json
import random
from typing import Any

import pytest

from gateway_v2.edge.wire.sse import SSEDecoder, SSEEncoder, SSEEncoderTerminated
from gateway_v2.edge.wire.sse_chunks import ChunkEvent, StreamIdentity, ToolFragment

_IDENTITY = StreamIdentity(stream_id="chatcmpl-req1", model="gpt-x", created=1_700_000_000)


def _obj(frame: bytes) -> dict[str, Any]:
    assert frame.startswith(b"data: ") and frame.endswith(b"\n\n")
    return json.loads(frame[len(b"data: "):])


def test_identity_is_stable_role_is_first_chunk_only_and_one_choice_per_chunk() -> None:
    enc = SSEEncoder(identity=_IDENTITY)
    first = _obj(enc.chunk(ChunkEvent(content="a", reasoning="r", refusal="n")))
    second = _obj(enc.chunk(ChunkEvent(content="b")))
    last = _obj(enc.finish("stop"))
    for obj in (first, second, last):
        identity = (obj["id"], obj["model"], obj["created"])
        assert identity == ("chatcmpl-req1", "gpt-x", 1_700_000_000)
        assert obj["object"] == "chat.completion.chunk" and len(obj["choices"]) == 1
        assert "usage" not in obj
    assert first["choices"][0]["delta"] == {
        "role": "assistant", "content": "a", "reasoning_content": "r", "refusal": "n"}
    assert second["choices"][0]["delta"] == {"content": "b"}
    assert first["choices"][0]["finish_reason"] is None
    assert last["choices"][0] == {"index": 0, "delta": {}, "finish_reason": "stop"}


def test_tool_fragments_render_first_with_id_type_name_then_arguments_only() -> None:
    enc = SSEEncoder(identity=_IDENTITY)
    one = _obj(enc.chunk(ChunkEvent(tool_calls=(
        ToolFragment(index=0, id="c1", name="f", arguments=""),))))
    two = _obj(enc.chunk(ChunkEvent(tool_calls=(ToolFragment(index=0, arguments='{"a":1}'),))))
    assert one["choices"][0]["delta"]["tool_calls"] == [
        {"index": 0, "id": "c1", "type": "function", "function": {"name": "f", "arguments": ""}}]
    assert two["choices"][0]["delta"] == {"tool_calls": [
        {"index": 0, "function": {"arguments": '{"a":1}'}}]}


def test_legacy_defaults_apply_without_an_identity() -> None:
    obj = _obj(SSEEncoder().chunk(ChunkEvent(content="x")))
    assert obj["id"] == "chatcmpl-stream" and obj["model"] == "gateway"


def test_no_chunk_or_finish_after_a_terminal_frame() -> None:
    enc = SSEEncoder(identity=_IDENTITY)
    enc.error("output_blocked")
    with pytest.raises(SSEEncoderTerminated):
        enc.chunk(ChunkEvent(content="x"))
    with pytest.raises(SSEEncoderTerminated):
        enc.finish("stop")


def test_property_6_encode_decode_round_trip_with_tool_fragments_and_finish_reason() -> None:
    # Feature: gw34-sdk-compat, Property 6: SSE encode -> decode round trip
    rng = random.Random(0x6A34_0006)
    alphabet = 'abc é🙂"\\{}\n\t'
    for _ in range(300):
        enc = SSEEncoder(identity=_IDENTITY)
        decoder = SSEDecoder(max_tool_calls=8)
        expected: list[tuple[str, str]] = []
        wire = b""
        for step in range(rng.randint(1, 6)):
            text = "".join(rng.choice(alphabet) for _ in range(rng.randint(1, 12)))
            if rng.random() < 0.5:
                event = ChunkEvent(content=text)
                expected.append(("content", text))
            else:
                index = rng.randint(0, 3)
                first = step == 0 or rng.random() < 0.3
                fragment = ToolFragment(
                    index=index, id=f"c{index}" if first else None,
                    name=f"fn{index}" if first else None, arguments=text)
                event = ChunkEvent(tool_calls=(fragment,))
                # the decoder keeps the first id / name per index and drops an equal repeat
                expected.append((f"output_tool_arguments#{index}", text))
            wire += enc.chunk(event)
        reason = rng.choice(["stop", "length", "tool_calls", "content_filter"])
        wire += enc.finish(reason) + enc.done()
        frames = decoder.feed(wire)
        assert not decoder.malformed() and decoder.finished()
        got = [d for f in frames for d in f.text_deltas if d[0] not in ("role",)
               and not d[0].startswith(("output_tool_id", "output_tool_name"))]
        assert got == [*expected, ("finish_reason", reason)]
