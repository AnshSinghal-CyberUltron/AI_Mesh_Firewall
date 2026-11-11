"""GW34 Task 4: the fold is total both ways; joined pairs are consistent with the template."""
from __future__ import annotations

import json
import random
from typing import Any

import pytest

from gateway_v2.domain import Surface
from gateway_v2.domain.channels import TEXT_CHANNELS
from gateway_v2.edge.wire import openai_fold
from gateway_v2.edge.wire.openai import OpenAIRequestParser, prompt_chars
from gateway_v2.edge.wire.openai_fold import (
    INPUT_SDK_PATH_PREFIXES,
    ChannelFoldError,
    TextSlot,
    fold_request,
)
from gateway_v2.edge.wire.openai_template import JOIN_SEPARATOR, JOINED, SchemaObject
from gateway_v2.runtime.resources import load_contract

_CONTRACT = load_contract()[0]
_BOUNDS = _CONTRACT.tool_bounds()
_ALPHABET = "abcdefghijklmnopqrstuvwxyz0123456789 _-"


def _text(rng: random.Random) -> str:
    return "".join(rng.choice(_ALPHABET) for _ in range(rng.randint(1, 20)))


def _schema(rng: random.Random, depth: int = 0) -> dict[str, Any]:
    out: dict[str, Any] = {"type": "object", "title": _text(rng), "description": _text(rng)}
    props: dict[str, Any] = {}
    for _ in range(rng.randint(1, 3)):
        props[_text(rng)] = (
            _schema(rng, depth + 1)
            if depth < 2 and rng.random() < 0.3
            else {"type": "string", "description": _text(rng), "default": _text(rng)}
        )
    out["properties"] = props
    out["required"] = list(props)[:1]
    return out


def _request(rng: random.Random) -> dict[str, Any]:
    names = {f"fn_{rng.randint(0, 10**6)}" for _ in range(rng.randint(1, 6))}
    tools = [
        {"type": "function",
         "function": {"name": n, "description": _text(rng), "parameters": _schema(rng)}}
        for n in sorted(names)
    ]
    first = sorted(names)[0]
    messages: list[dict[str, Any]] = [{"role": "user", "content": _text(rng)}]
    for i in range(rng.randint(0, 3)):
        messages.append({"role": "assistant", "content": None, "tool_calls": [
            {"id": f"call_{i}", "type": "function",
             "function": {"name": first, "arguments": json.dumps({"q": _text(rng)})}}]})
        messages.append({"role": "tool", "tool_call_id": f"call_{i}", "content": _text(rng)})
    return {"model": "m", "messages": messages, "tools": tools,
            "response_format": {"type": "json_schema", "json_schema": {
                "name": "r", "description": _text(rng), "schema": _schema(rng)}}}


def _slots(node: object) -> list[TextSlot]:
    if isinstance(node, TextSlot):
        return [node]
    if isinstance(node, tuple):
        return [s for item in node for s in _slots(item)]
    if isinstance(node, SchemaObject):
        return [s for k, v in node.items for s in (*_slots(k), *_slots(v))]
    return []


def test_property_2_fold_totality_and_joined_consistency() -> None:
    # Feature: gw34-sdk-compat, Property 2: the input fold is total over the input channels
    rng = random.Random(0x6A34_0002)
    names = {c.name for c in TEXT_CHANNELS if c.sdk_path.startswith(INPUT_SDK_PATH_PREFIXES)}
    seen: set[str] = set()
    for _ in range(150):
        body = _request(rng)
        fold, template = fold_request(body, _BOUNDS)
        assert len(fold.pairs) == len(fold.forwarded) == len(fold.message_index)
        assert template.slot_count == len(fold.pairs) == template.pair_count
        seen |= {channel for channel, _ in fold.pairs}
        joined = dict(template.joined)
        slots = [
            s for t in template.tools.tools
            for s in (*_slots(t.description), *_slots(t.parameters))
        ]
        slots += [s for m in template.messages for c in m.tool_calls for s in [c.arguments]]
        for slot in slots:
            assert slot.index == JOINED
            pair_index = joined[slot.channel]
            channel, text = fold.pairs[pair_index]
            assert channel == slot.channel
            assert len(text.split(JOIN_SEPARATOR)) == template.pair_parts[pair_index]
            assert slot.part < template.pair_parts[pair_index]
        for channel, idx in joined.items():
            assert fold.forwarded[idx] == (channel not in openai_fold_scan_only())
        assert len(joined) <= _BOUNDS.max_scanned_slots
    # the fold can emit every input channel except the legacy function_call one (refused at parse)
    assert seen <= names
    assert names - seen <= {"completion_prompt", "stop_sequence", "function_arguments",
                            "content_part_text"}


def openai_fold_scan_only() -> frozenset[str]:
    from gateway_v2.edge.wire.openai_template import SCAN_ONLY_CHANNELS

    return SCAN_ONLY_CHANNELS


def test_fold_totality_fails_the_build_on_divergence(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(openai_fold, "_FOLDED_INPUT_CHANNELS", frozenset({"message_content"}))
    with pytest.raises(ChannelFoldError):
        openai_fold._assert_fold_total()
    monkeypatch.setattr(
        openai_fold, "_FOLDED_INPUT_CHANNELS", openai_fold._FOLDED_INPUT_CHANNELS | {"bogus"}
    )
    with pytest.raises(ChannelFoldError):
        openai_fold._assert_fold_total()


def test_scan_only_pairs_are_excluded_from_prompt_chars() -> None:
    body = {"model": "m", "messages": [
        {"role": "user", "content": "abcd"},
        {"role": "assistant", "tool_calls": [{"id": "c1", "type": "function",
                                              "function": {"name": "f", "arguments": '{"x": 1}'}}]},
    ]}
    req = OpenAIRequestParser().parse(
        json.dumps(body).encode(), surface=Surface.CHAT, contract=_CONTRACT
    )
    forwarded_chars = sum(
        len(text) for (_, text), keep in zip(req.input_channels, req.input_forwarded, strict=True)
        if keep
    )
    assert prompt_chars(req) == forwarded_chars
    assert prompt_chars(req) < sum(len(t) for _, t in req.input_channels)


def test_a_twenty_tool_pydantic_request_folds_to_few_scan_slots() -> None:
    rng = random.Random(1)
    tools = [
        {"type": "function", "function": {"name": f"tool_{i}", "description": "d",
         "parameters": {**_schema(rng), "$defs": {"U": {"type": "string"}},
                        "additionalProperties": False}}}
        for i in range(20)
    ]
    fold, template = fold_request({"model": "m", "messages": [], "tools": tools}, _BOUNDS)
    assert len(template.joined) <= 0.7 * _BOUNDS.max_scanned_slots
    assert len(fold.pairs) == len(template.joined)
