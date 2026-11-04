"""GW34 Task 5: ToolCallAssembler (both modes), channel grammar, properties 1, 4 and 7."""
from __future__ import annotations

import asyncio
import dataclasses
import json
import random
from collections.abc import Callable
from typing import Any

import pytest

from gateway_v2.edge.wire.sse import SSEDecoder, SSEEncoder
from gateway_v2.edge.wire.sse_chunks import ChunkEvent, ToolFragment
from gateway_v2.edge.wire.tool_stream import (
    Terminate,
    ToolCallAssembler,
    base_channel,
    parse_tool_key,
    tool_key,
)
from gateway_v2.egress.stream import DownstreamFrame
from gateway_v2.runtime.resources import load_contract

_BOUNDS = load_contract()[0].tool_bounds()
_DECLARED = frozenset({"get_weather", "search"})


class _Meter:
    """Counts charged bytes (the request's coalescer stand-in) and records the peak."""

    def __init__(self, limit: int | None = None) -> None:
        self.held = 0
        self.peak = 0
        self.limit = limit

    def charge(self, n: int) -> bool:
        if self.limit is not None and self.held + n > self.limit:
            return False
        self.held += n
        self.peak = max(self.peak, self.held)
        return True

    def refund(self, n: int) -> None:
        self.held -= n


def _assembler(
    *, enforcing: bool, json_mode: bool = False, declared: frozenset[str] = _DECLARED,
    tools_declared: bool = True, clean: Callable[[str], bool] | None = None,
    meter: _Meter | None = None, bounds: Any = _BOUNDS,
) -> tuple[ToolCallAssembler, _Meter]:
    meter = meter or _Meter()

    async def decoded_clean(text: str) -> bool:
        if clean is None:
            return True
        return clean(text)

    return (
        ToolCallAssembler(
            declared=declared, tools_declared=tools_declared, enforcing=enforcing,
            json_mode=json_mode, bounds=bounds, decoded_clean=decoded_clean,
            charge=meter.charge, refund=meter.refund,
        ),
        meter,
    )


def _frame(*deltas: tuple[str, str]) -> DownstreamFrame:
    return DownstreamFrame(text_deltas=tuple(deltas))


def _call_frames(index: int, call_id: str, name: str, args: list[str]) -> list[DownstreamFrame]:
    out = [_frame((tool_key("output_tool_id", index), call_id),
                  (tool_key("output_tool_name", index), name))]
    out += [_frame((tool_key("output_tool_arguments", index), a)) for a in args]
    return out


def _feed_all(asm: ToolCallAssembler, frames: list[DownstreamFrame]) -> list[ChunkEvent]:
    events: list[ChunkEvent] = []
    for frame in frames:
        got = asm.feed(frame)
        assert not isinstance(got, Terminate), got
        events += got
    return events


def _finish(asm: ToolCallAssembler) -> Any:
    return asyncio.run(asm.finish())


def test_key_grammar() -> None:
    assert tool_key("output_tool_id", 3) == "output_tool_id#3"
    assert parse_tool_key("output_tool_arguments#12") == ("output_tool_arguments", 12)
    assert parse_tool_key("output_tool_arguments") == ("output_tool_arguments", 0)
    for bad in ("content", "output_tool_arguments#x", "output_tool_other#1", "x#1", "#1"):
        assert parse_tool_key(bad) is None
    assert base_channel("output_tool_name#4") == "output_tool_name"


def test_non_enforcing_emits_after_id_and_name_are_validated() -> None:
    asm, _ = _assembler(enforcing=False)
    early = asm.feed(_frame(("output_tool_arguments#0", '{"a"')))  # arguments first
    assert early == ()
    events = _feed_all(asm, [_frame(("output_tool_id#0", "call_1")),
                             _frame(("output_tool_name#0", "get_weather"))])
    [first] = events
    [fragment] = first.tool_calls
    assert (fragment.index, fragment.id, fragment.name, fragment.arguments) == (
        0, "call_1", "get_weather", '{"a"')
    later = _feed_all(asm, [_frame(("output_tool_arguments#0", ":1}"))])
    assert later[0].tool_calls[0].id is None and later[0].tool_calls[0].arguments == ":1}"
    assert _finish(asm) == ()
    assert asm.finish_reason == "tool_calls" and asm.calls_released == 1


@pytest.mark.parametrize("enforcing", [False, True])
def test_undeclared_name_and_no_tools_terminate_output_blocked(enforcing: bool) -> None:
    asm, _ = _assembler(enforcing=enforcing)
    frames = _call_frames(0, "call_1", "rm_rf", ["{}"])
    results = [asm.feed(f) for f in frames]
    if not enforcing:
        assert Terminate("output_blocked") in results
    else:
        assert Terminate("output_blocked") == _finish(asm)
    asm2, _ = _assembler(enforcing=enforcing, tools_declared=False)
    results = [asm2.feed(f) for f in _call_frames(0, "call_1", "get_weather", ["{}"])]
    assert Terminate("output_blocked") in results or _finish(asm2) == Terminate("output_blocked")


@pytest.mark.parametrize("enforcing", [False, True])
@pytest.mark.parametrize(
    "deltas",
    [
        [("output_tool_arguments#0", "{}")],                                    # no name, no id
        [("output_tool_arguments#0", "{}"), ("output_tool_id#0", "c1")],        # args without name
        [("output_tool_arguments#0", "{}"), ("output_tool_name#0", "search")],  # args without id
        [("output_tool_name#0", "search")],                                     # name without id
        [("output_tool_id#0", "c1")],                                           # id without name
    ],
)
def test_incomplete_calls_are_malformed_at_finish_in_both_modes(
    enforcing: bool, deltas: list[tuple[str, str]]
) -> None:
    asm, _ = _assembler(enforcing=enforcing)
    for delta in deltas:
        got = asm.feed(_frame(delta))
        assert not isinstance(got, Terminate)
    assert _finish(asm) == Terminate("stream_malformed_upstream")


@pytest.mark.parametrize("enforcing", [False, True])
def test_finish_reason_tool_calls_with_no_call_is_malformed(enforcing: bool) -> None:
    asm, _ = _assembler(enforcing=enforcing)
    asm.feed(_frame(("finish_reason", "tool_calls")))
    assert _finish(asm) == Terminate("stream_malformed_upstream")


def test_enforcing_releases_only_at_finish_in_index_order_one_fragment_each() -> None:
    asm, _ = _assembler(enforcing=True)
    frames = _call_frames(1, "call_b", "search", ['{"q":', '"x"}']) + _call_frames(
        0, "call_a", "get_weather", ["{", "}"])
    assert _feed_all(asm, frames) == []
    asm.feed(_frame(("finish_reason", "tool_calls")))
    events = _finish(asm)
    assert [e.tool_calls[0].index for e in events] == [0, 1]
    assert [e.tool_calls[0].arguments for e in events] == ["{}", '{"q":"x"}']
    assert all(len(e.tool_calls) == 1 and e.tool_calls[0].id for e in events)
    assert asm.finish_reason == "tool_calls"


def test_a_held_final_tail_arriving_after_finish_reason_is_still_in_the_released_call() -> None:
    asm, _ = _assembler(enforcing=True)
    _feed_all(asm, _call_frames(0, "call_a", "get_weather", ['{"city":"Par']))
    asm.feed(_frame(("finish_reason", "tool_calls")))  # never a release trigger
    assert _feed_all(asm, [_frame(("output_tool_arguments#0", 'is"}'))]) == []
    [event] = _finish(asm)
    assert event.tool_calls[0].arguments == '{"city":"Paris"}'


def test_enforcing_invalid_json_residual_finding_and_scan_error_terminate() -> None:
    asm, _ = _assembler(enforcing=True)
    _feed_all(asm, _call_frames(0, "c", "search", ["{not json"]))
    assert _finish(asm) == Terminate("output_blocked")
    asm, _ = _assembler(enforcing=True, clean=lambda text: "AKIA" not in text)
    _feed_all(asm, _call_frames(0, "c", "search", ['{"k": "AK\\u0049A12345"}']))
    assert _finish(asm) == Terminate("output_blocked")

    def boom(_text: str) -> bool:
        raise RuntimeError("scanner down")

    asm, _ = _assembler(enforcing=True, clean=boom)
    _feed_all(asm, _call_frames(0, "c", "search", ["{}"]))
    assert _finish(asm) == Terminate("scan_failure")


def test_empty_arguments_are_normalised_to_an_empty_object_when_enforcing() -> None:
    asm, _ = _assembler(enforcing=True)
    _feed_all(asm, _call_frames(0, "c", "search", []))
    [event] = _finish(asm)
    assert event.tool_calls[0].arguments == "{}"


def test_json_mode_content_is_withheld_to_finish_and_must_be_valid_json() -> None:
    asm, _ = _assembler(enforcing=True, json_mode=True)
    assert _feed_all(asm, [_frame(("content", '{"a":')), _frame(("content", "1}"))]) == []
    [event] = _finish(asm)
    assert event.content == '{"a":1}'
    asm, _ = _assembler(enforcing=True, json_mode=True)
    _feed_all(asm, [_frame(("content", "[REDACTED"))])
    assert _finish(asm) == Terminate("output_blocked")
    asm, _ = _assembler(enforcing=False, json_mode=True)  # no enforcing rule: pass through
    assert _feed_all(asm, [_frame(("content", "x"))])[0].content == "x"


def test_unexpected_hash_keys_and_index_bounds_are_malformed() -> None:
    asm, _ = _assembler(enforcing=False)
    assert asm.feed(_frame(("weird#1", "x"))) == Terminate("stream_malformed_upstream")
    asm, _ = _assembler(enforcing=False)
    key = tool_key("output_tool_id", _BOUNDS.max_tool_calls)
    assert asm.feed(_frame((key, "c"))) == Terminate("stream_malformed_upstream")


def test_withheld_bytes_are_charged_and_bounded() -> None:
    small = dataclasses.replace(_BOUNDS, max_withheld_bytes=4096)
    meter = _Meter()
    asm, _ = _assembler(enforcing=True, bounds=small, meter=meter)
    chunk = "x" * 1000
    verdict: Any = ()
    for index in range(_BOUNDS.max_tool_calls):  # 128 calls, each far over a fair share
        for frame in _call_frames(index, f"call_{index}", "search", [chunk, chunk]):
            verdict = asm.feed(frame)
            if isinstance(verdict, Terminate):
                break
        if isinstance(verdict, Terminate):
            break
    assert verdict == Terminate("stream_malformed_upstream")
    assert meter.peak <= small.max_withheld_bytes
    json_asm, _ = _assembler(enforcing=True, json_mode=True, bounds=small)
    got: Any = ()
    for _ in range(10):
        got = json_asm.feed(_frame(("content", chunk)))
        if isinstance(got, Terminate):
            break
    assert got == Terminate("output_blocked")


def test_the_coalescer_charge_failing_terminates() -> None:
    asm, _ = _assembler(enforcing=True, meter=_Meter(limit=10))
    assert asm.feed(_frame(("output_tool_arguments#0", "x" * 100))) == Terminate(
        "stream_malformed_upstream")


# ---------------------------------------------------------------- properties


def _reference_accumulate(chunks: list[dict[str, Any]]) -> dict[int, dict[str, str]]:
    """Mirrors the SDK's ``accumulate_delta`` for ``delta.tool_calls`` (strings concatenate)."""
    acc: dict[int, dict[str, str]] = {}
    for delta in chunks:
        for fragment in delta.get("tool_calls", []):
            call = acc.setdefault(fragment["index"], {"id": "", "name": "", "arguments": ""})
            call["id"] += fragment.get("id") or ""
            fn = fragment.get("function") or {}
            call["name"] += fn.get("name") or ""
            call["arguments"] += fn.get("arguments") or ""
    return acc


def _random_calls(rng: random.Random) -> dict[int, tuple[str, str, str]]:
    calls = {}
    for index in range(rng.randint(1, 4)):
        text = "".join(rng.choice("ab é\"\\{}🙂") for _ in range(rng.randint(0, 30)))
        calls[index] = (
            f"call_{index}_{rng.randint(0, 999)}",
            rng.choice(sorted(_DECLARED)),
            json.dumps({"q": text}),
        )
    return calls


def _split(rng: random.Random, text: str) -> list[str]:
    cuts = sorted(rng.randint(0, len(text)) for _ in range(rng.randint(0, 6)))
    bounds = [0, *cuts, len(text)]
    return [text[a:b] for a, b in zip(bounds, bounds[1:], strict=False) if text[a:b]]


def _run_stream(
    rng: random.Random, calls: dict[int, tuple[str, str, str]], *, enforcing: bool
) -> tuple[list[dict[str, Any]], ToolCallAssembler]:
    """Encode upstream fragments, decode with random wire partitions, assemble, re-encode."""
    queues: dict[int, list[ToolFragment]] = {}
    for index, (call_id, name, args) in calls.items():
        parts = _split(rng, args) or [""]
        queues[index] = [ToolFragment(index=index, id=call_id, name=name, arguments=parts[0])]
        queues[index] += [ToolFragment(index=index, arguments=p) for p in parts[1:]]
    fragments: list[ToolFragment] = []
    while any(queues.values()):  # interleave indexes; each index keeps its own order
        fragments.append(queues[rng.choice([k for k, v in queues.items() if v])].pop(0))
    encoder = SSEEncoder()
    wire = b"".join(encoder.chunk(ChunkEvent(tool_calls=(f,))) for f in fragments)
    decoder = SSEDecoder(max_tool_calls=_BOUNDS.max_tool_calls)
    frames: list[DownstreamFrame] = []
    offset = 0
    while offset < len(wire):  # random wire partitions, anywhere (mid-escape, mid-UTF-8, ...)
        step = rng.randint(1, len(wire) - offset)
        frames += decoder.feed(wire[offset: offset + step])
        offset += step
    assert not decoder.malformed()
    asm, _ = _assembler(enforcing=enforcing)
    out: list[ChunkEvent] = []
    for frame in frames:
        got = asm.feed(frame)
        assert not isinstance(got, Terminate), got
        out += got
    fin = asyncio.run(asm.finish())
    assert not isinstance(fin, Terminate), fin
    out += fin
    enc2 = SSEEncoder()
    chunks = [
        json.loads(line[len(b"data: "):])["choices"][0]["delta"]
        for event in out
        for line in enc2.chunk(event).split(b"\n\n")
        if line
    ]
    return chunks, asm


@pytest.mark.parametrize("enforcing", [False, True])
def test_property_1_partition_and_interleave_accumulation_identity(enforcing: bool) -> None:
    # Feature: gw34-sdk-compat, Property 1: fragment accumulation is partition/interleave invariant
    rng = random.Random(0x6A34_0001 + int(enforcing))
    sdk_accumulate = None
    try:
        from openai.lib.streaming._deltas import accumulate_delta as sdk_accumulate
    except ImportError:  # pragma: no cover - the stock SDK is optional
        pass
    for _ in range(150):
        calls = _random_calls(rng)
        chunks, _asm = _run_stream(rng, calls, enforcing=enforcing)
        got = _reference_accumulate(chunks)
        want = sorted((c[0], c[1], c[2]) for c in calls.values())
        # output indexes are dense and ascending in first-emission order (the SDK merges by
        # list position), so compare the SET of calls, and check density explicitly
        assert sorted((c["id"], c["name"], c["arguments"]) for c in got.values()) == want
        assert sorted(got) == list(range(len(calls)))
        if sdk_accumulate is not None:
            acc: dict[object, object] = {}
            for delta in chunks:
                acc = sdk_accumulate(acc, dict(delta))
            real = acc["tool_calls"]  # type: ignore[index]
            assert sorted(  # type: ignore[union-attr]
                (c["id"], c["function"]["name"], c["function"]["arguments"]) for c in real
            ) == want


def test_property_4_the_assembler_never_releases_invalid_json_arguments() -> None:
    # Feature: gw34-sdk-compat, Property 4: enforcing assembler never releases invalid JSON
    rng = random.Random(0x6A34_0004)
    alphabet = '{}[]":,0123456789abc \\'
    for _ in range(300):
        args = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 24)))
        asm, _ = _assembler(enforcing=True)
        released: list[str] = []
        for frame in _call_frames(0, "call_1", "search", _split(rng, args) or [""]):
            got = asm.feed(frame)
            assert not isinstance(got, Terminate)
            released += [f.arguments or "" for e in got for f in e.tool_calls]
        result = _finish(asm)
        if isinstance(result, Terminate):
            assert result.code == "output_blocked" and released == []
        else:
            for event in result:
                json.loads(event.tool_calls[0].arguments or "")


def test_property_7_per_call_isolation_in_the_assembler() -> None:
    # Feature: gw34-sdk-compat, Property 7: changing call A leaves call B byte-identical
    rng = random.Random(0x6A34_0007)
    for _ in range(150):
        b_args = json.dumps({"k": "".join(rng.choice("xyz é") for _ in range(10))})
        results = []
        for a_args in ('{"a": 1}', '{"a": "[REDACTED]", "more": [1, 2, 3]}'):
            asm, _ = _assembler(enforcing=True)
            frames = _call_frames(0, "call_a", "search", _split(rng, a_args) or [""])
            frames += _call_frames(1, "call_b", "get_weather", [b_args])
            _feed_all(asm, frames)
            events = _finish(asm)
            results.append(events[1].tool_calls[0])
        assert results[0] == results[1]
