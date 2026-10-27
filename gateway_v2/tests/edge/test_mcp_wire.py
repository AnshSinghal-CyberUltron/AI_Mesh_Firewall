# Feature: mcp-surface-gw17, Property 1: fold and rebuild round-trip
# Feature: mcp-surface-gw17, Property 2: no text leaf escapes the fold
# Feature: mcp-surface-gw17, Property 3: bounds stop at the first breach
# Validates: Requirements 4.1, 4.2, 4.3, 5.1, 5.5, 9.1, 9.2
"""The MCP leaf fold, rebuild and bounds.

House idiom: a seeded ``random.Random``, no ``hypothesis``, at least 10,000 iterations.
"""

from __future__ import annotations

import dataclasses
import json
import random

import pytest

from gateway_v2.edge.wire.mcp import (
    CHANNEL_ARGUMENTS,
    CHANNEL_COMPLETION,
    CHANNEL_ERROR,
    CHANNEL_RESULT,
    check_request_size,
    fold_reply,
    fold_request,
    rebuild,
    request_targets,
)
from gateway_v2.runtime.resources import McpBounds

_SEED = 0x6C17_0004
_ITERATIONS = 10_000
_BOUNDS = McpBounds()
_ALPHA = "abcdefghijklmnopqrstuvwxyz"


def _ctx(i: int, **extra: object) -> str:
    return f"seed={_SEED:#x} iter={i} " + " ".join(f"{k}={v!r}" for k, v in extra.items())


def _word(rng: random.Random) -> str:
    return "".join(rng.choice(_ALPHA) for _ in range(rng.randint(1, 6)))


def _json(rng: random.Random, depth: int = 0) -> object:
    roll = rng.random()
    if depth >= 4 or roll < 0.3:
        return rng.choice((_word(rng), rng.randint(0, 99), True, None, 1.5))
    if roll < 0.65:
        return {f"{_word(rng)}{i}": _json(rng, depth + 1) for i in range(rng.randint(0, 4))}
    return [_json(rng, depth + 1) for _ in range(rng.randint(0, 4))]


def test_property_fold_rebuild_round_trip() -> None:
    """Property 1: rebuilding with the folded texts is identity; with markers, no text survives."""
    rng = random.Random(_SEED)
    for i in range(_ITERATIONS):
        params = {"name": "t", "arguments": {"root": _json(rng)}}
        targets = request_targets("tools/call", params)
        assert not isinstance(targets, str), _ctx(i)
        fold = fold_request(targets, _BOUNDS)
        assert not isinstance(fold, str), _ctx(i)
        same = rebuild(params, fold, tuple(leaf.text for leaf in fold.leaves))
        assert same == params, _ctx(i)
        markers = tuple(f"M{n}" for n in range(len(fold.leaves)))
        masked = rebuild(params, fold, markers)
        assert isinstance(masked, dict)
        refold = fold_request(request_targets("tools/call", masked), _BOUNDS)  # type: ignore[arg-type]
        assert not isinstance(refold, str), _ctx(i)
        assert sorted(leaf.text for leaf in refold.leaves) == sorted(markers), _ctx(i)
        assert json.dumps(params["arguments"]) == json.dumps(same["arguments"]), _ctx(i)  # type: ignore[index]


def _place(value: object, rng: random.Random, canary: str, as_key: bool) -> object:
    if isinstance(value, dict) and value and rng.random() < 0.7:
        key = rng.choice(list(value))
        return {**value, key: _place(value[key], rng, canary, as_key)}
    if isinstance(value, list) and value and rng.random() < 0.7:
        index = rng.randrange(len(value))
        return [*value[:index], _place(value[index], rng, canary, as_key), *value[index + 1 :]]
    if as_key:
        return {canary: 1}
    return canary


def test_property_no_leaf_escapes_the_fold() -> None:
    """Property 2: a canary at a random value or key is a folded text."""
    rng = random.Random(_SEED + 1)
    for i in range(_ITERATIONS):
        canary = f"CANARY{i}"
        arguments = _place(_json(rng), rng, canary, as_key=rng.random() < 0.3)
        targets = request_targets("tools/call", {"name": "t", "arguments": {"x": arguments}})
        assert not isinstance(targets, str), _ctx(i)
        fold = fold_request(targets, _BOUNDS)
        assert not isinstance(fold, str), _ctx(i)
        assert canary in {leaf.text for leaf in fold.leaves}, _ctx(i, canary=canary)


def _nested(depth: int) -> object:
    value: object = "leaf"
    for _ in range(depth):
        value = {"k": value}
    return value


def test_property_bounds_stop_at_first_breach() -> None:
    """Property 3: over-depth or over-node input is refused and no fold is returned."""
    rng = random.Random(_SEED + 2)
    small = dataclasses.replace(_BOUNDS, json_depth=8, argument_nodes=40, result_nodes=40)
    for i in range(_ITERATIONS):
        depth = rng.randint(0, 16)
        deep = fold_request(((("arguments",), _nested(depth), CHANNEL_ARGUMENTS),), small)
        if depth > small.json_depth:
            assert deep == "mcp_request_too_large", _ctx(i, depth=depth)
        else:
            assert not isinstance(deep, str), _ctx(i, depth=depth)
        width = rng.randint(1, 60)
        wide = fold_reply(({"result": {"items": ["x"] * width}},), small)
        # one node for the result object, one for the list, one per item
        if width + 2 > small.result_nodes:
            assert wide == "mcp_result_too_large", _ctx(i, width=width)
        else:
            assert not isinstance(wide, str), _ctx(i, width=width)


def test_split_secret_blocks_form_one_join_run() -> None:
    reply = {
        "result": {
            "content": [
                {"type": "text", "text": "AKIAIOSFOD"},
                {"type": "text", "text": "NN7EXAMPLE"},
            ]
        }
    }
    fold = fold_reply((reply,), _BOUNDS)
    assert not isinstance(fold, str)
    assert [leaf.text for leaf in fold.leaves[:2]] == ["AKIAIOSFOD", "NN7EXAMPLE"]
    assert fold.join_runs == ((0, 1),)
    assert all(leaf.channel == CHANNEL_RESULT for leaf in fold.leaves)


def test_error_and_server_request_are_folded() -> None:
    fold = fold_reply(
        (
            {"error": {"code": -1, "message": "boom SECRET"}},
            {"method": "sampling/createMessage", "params": {"messages": ["ASK"]}},
        ),
        _BOUNDS,
    )
    assert not isinstance(fold, str)
    texts = {leaf.text for leaf in fold.leaves}
    assert "boom SECRET" in texts and "ASK" in texts
    assert any(leaf.channel == CHANNEL_ERROR for leaf in fold.leaves)


def test_block_count_bound() -> None:
    small = dataclasses.replace(_BOUNDS, result_blocks=2)
    reply = {"result": {"content": [{"type": "text", "text": "x"}] * 3}}
    assert fold_reply((reply,), small) == "mcp_result_too_large"


@pytest.mark.parametrize(
    ("method", "params", "expect"),
    (
        ("tools/call", {"name": "t", "arguments": {"q": "x"}}, CHANNEL_ARGUMENTS),
        ("tools/call", {"name": "t", "arguments": "x"}, "mcp_invalid_params"),
        ("tools/call", {"arguments": {}}, "mcp_invalid_params"),
        ("prompts/get", {"name": "p", "arguments": {"a": "b"}}, CHANNEL_ARGUMENTS),
        ("completion/complete", {"argument": {"name": "n", "value": "v"}}, CHANNEL_COMPLETION),
        ("completion/complete", {"argument": {"value": 3}}, "mcp_invalid_params"),
        ("resources/read", {"uri": "https://x/?token=abc"}, None),
        ("resources/read", {}, "mcp_invalid_params"),
        ("tools/list", None, None),
        ("tools/call", "x", "mcp_invalid_params"),
    ),
)
def test_request_targets(method: str, params: object, expect: str | None) -> None:
    targets = request_targets(method, params)
    if expect is not None and expect.startswith("mcp_invalid"):
        assert targets == expect
        return
    assert not isinstance(targets, str)
    channels = {channel for _, _, channel in targets}
    assert channels == ({expect} if expect else set())


def test_resources_read_uri_is_not_scanned() -> None:
    targets = request_targets("resources/read", {"uri": "https://x/?token=SECRET"})
    assert targets == ()


def test_request_size_bound() -> None:
    small = dataclasses.replace(_BOUNDS, argument_bytes=16)
    assert check_request_size({"a": "x" * 40}, small) == "mcp_request_too_large"
    assert check_request_size({"a": "x"}, small) is None


def test_rebuild_refuses_a_colliding_key_rename() -> None:
    params = {"name": "t", "arguments": {"secretA": 1, "secretB": 2}}
    targets = request_targets("tools/call", params)
    assert not isinstance(targets, str)
    fold = fold_request(targets, _BOUNDS)
    assert not isinstance(fold, str)
    texts = tuple("[REDACTED]" if leaf.is_key else leaf.text for leaf in fold.leaves)
    with pytest.raises(ValueError):
        rebuild(params, fold, texts)
