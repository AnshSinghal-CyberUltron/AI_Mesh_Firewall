"""L34-1 ``wire`` mode: the real ``OpenAIUpstreamProvider`` over a loopback fake upstream.

Cells (d) streamed tool-call deltas, (e) the non-stream tool call and (j) the pre-byte refusal,
this time through the REAL ``SSEDecoder`` tool parsing, ``_drain`` one-frame-behind ``final`` and
the edge assembler, with the fake upstream splitting the SSE bytes at seeded random offsets
(mid-JSON-string, mid-escape, mid-UTF-8, mid-SSE-line).
"""

from __future__ import annotations

import json
import random
from collections.abc import Iterator

import pytest

openai = pytest.importorskip("openai")
pytest.importorskip("httpx")
pytest.importorskip("uvicorn")

from tests.openai_conformance.sdk_matrix.fake_upstream import (  # noqa: E402
    chat_completion_chunk,
    fake_upstream_app,
    split_sse_bytes,
)
from tests.openai_conformance.sdk_matrix.harness import (  # noqa: E402
    LoopbackServer,
    build_matrix_app,
    dev_env,
    marker,
    matrix_contract,
)
from tests.openai_conformance.sdk_matrix.py_clients import facade_for  # noqa: E402

_CLIENTS = ("python-sync", "python-async")
_MODEL = "gpt-4o-mini"
_TOOL = {"type": "function", "function": {"name": "search", "parameters": {"type": "object"}}}
_VARIANTS = 12
_ALPHABET = 'ab c"\\é🙂{}\n'


def _calls(rng: random.Random) -> list[tuple[str, str, str]]:
    out = []
    for index in range(rng.randint(1, 3)):
        text = "".join(rng.choice(_ALPHABET) for _ in range(rng.randint(0, 24)))
        out.append((f"call_{index}_{rng.randint(0, 999)}", "search", json.dumps({"q": text})))
    return out


def _wire_script(calls: list[tuple[str, str, str]], rng: random.Random) -> list[bytes]:
    frames = [chat_completion_chunk({"role": "assistant", "content": None})]
    for index, (call_id, name, arguments) in enumerate(calls):
        cuts = sorted(rng.randint(0, len(arguments)) for _ in range(rng.randint(0, 4)))
        parts = [arguments[a:b] for a, b in zip([0, *cuts], [*cuts, len(arguments)], strict=True)]
        frames.append(chat_completion_chunk({"tool_calls": [{
            "index": index, "id": call_id, "type": "function",
            "function": {"name": name, "arguments": parts[0]}}]}))
        frames += [chat_completion_chunk({"tool_calls": [{
            "index": index, "function": {"arguments": part}}]}) for part in parts[1:]]
    frames.append(chat_completion_chunk({}, "tool_calls"))
    frames.append(b"data: [DONE]\n\n")
    wire = b"".join(frames)
    return split_sse_bytes(wire, rng, pieces=rng.randint(3, 25))


@pytest.fixture(scope="module")
def wire_gateway() -> Iterator[tuple[LoopbackServer, dict[str, list[tuple[str, str, str]]]]]:
    rng = random.Random(0x6A34_0D)
    expected: dict[str, list[tuple[str, str, str]]] = {}
    scripts: dict[str, list[bytes]] = {
        "plain": [chat_completion_chunk({"role": "assistant", "content": "hi"}),
                  chat_completion_chunk({}, "stop"), b"data: [DONE]\n\n"],
        "malformed": [b"data: {not json}\n\n"],
    }
    for i in range(_VARIANTS):
        expected[f"wire_tool_{i}"] = calls = _calls(rng)
        scripts[f"wire_tool_{i}"] = _wire_script(calls, rng)
    with LoopbackServer(fake_upstream_app(scripts, default="plain")) as upstream:  # type: ignore[arg-type]
        env = dev_env(provider_url=f"{upstream.base_url}/v1")
        app = build_matrix_app(contract=matrix_contract(), provider=None, env=env)
        with LoopbackServer(app) as gateway:
            yield gateway, expected


@pytest.mark.parametrize("client", _CLIENTS)
@pytest.mark.matrix(client="python-sync", cell="d")
def test_cell_d_wire_fragmented_tool_calls_accumulate_to_the_exact_call(  # type: ignore[no-untyped-def]
    client: str, wire_gateway
) -> None:
    server, expected = wire_gateway
    sdk = facade_for(client, server.base_url)
    try:
        for name, calls in expected.items():
            final = sdk.stream_helper(  # type: ignore[attr-defined]
                model=_MODEL, messages=[{"role": "user", "content": marker(name)}],
                tools=[_TOOL],
            )
            got = sorted(
                (c.id, c.function.name, c.function.arguments)
                for c in final.choices[0].message.tool_calls
            )
            assert got == sorted(calls), name
            assert final.choices[0].finish_reason == "tool_calls"
    finally:
        sdk.close()  # type: ignore[attr-defined]


@pytest.mark.parametrize("client", _CLIENTS)
@pytest.mark.matrix(client="python-sync", cell="e")
def test_cell_e_wire_non_stream_tool_call(client: str, wire_gateway) -> None:  # type: ignore[no-untyped-def]
    server, expected = wire_gateway
    sdk = facade_for(client, server.base_url)
    try:
        name, calls = next(iter(expected.items()))
        response = sdk.create(  # type: ignore[attr-defined]
            model=_MODEL, messages=[{"role": "user", "content": marker(name)}], tools=[_TOOL]
        )
    finally:
        sdk.close()  # type: ignore[attr-defined]
    calls_out = response.choices[0].message.tool_calls
    got = sorted((c.id, c.function.name, c.function.arguments) for c in calls_out)
    assert got == sorted(calls)


@pytest.mark.parametrize("client", _CLIENTS)
@pytest.mark.matrix(client="python-sync", cell="j")
def test_cell_j_wire_malformed_upstream_is_a_status_error_not_a_200_stream(  # type: ignore[no-untyped-def]
    client: str, wire_gateway
) -> None:
    server, _expected = wire_gateway
    sdk = facade_for(client, server.base_url)
    try:
        with pytest.raises(openai.APIStatusError) as raised:
            sdk.stream(  # type: ignore[attr-defined]
                model=_MODEL, messages=[{"role": "user", "content": marker("malformed")}]
            )
    finally:
        sdk.close()  # type: ignore[attr-defined]
    assert raised.value.status_code == 502
