"""L34-1: the stock Python ``openai`` SDK (sync + async) against the genuine ``build_app``.

Cells (a)-(j) of R1.2, run for both clients via :data:`py_clients.facade_for`. Streaming and
cancel cells (d, f, j) run over a loopback uvicorn server (D8: ``httpx.ASGITransport`` buffers,
evidenced by ``test_asgi_transport_probe.py``); the rest use the in-process ASGI transport.
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterator

import pytest

openai = pytest.importorskip("openai")
httpx = pytest.importorskip("httpx")

from tests.openai_conformance.sdk_matrix.harness import (  # noqa: E402
    API_KEY,
    LoopbackServer,
    build_matrix_app,
    marker,
    matrix_contract,
)
from tests.openai_conformance.sdk_matrix.py_clients import facade_for  # noqa: E402
from tests.openai_conformance.sdk_matrix.scripts import scripted_provider  # noqa: E402

_CONTRACT = matrix_contract()
_MODEL = "gpt-4o-mini"
_CLIENTS = ("python-sync", "python-async")
_WEATHER_TOOL = {
    "type": "function",
    "function": {
        "name": "get_weather",
        "parameters": {"type": "object", "properties": {"city": {"type": "string"}}},
    },
}


def _content_chunks(chunks: list[object]) -> list[object]:
    """Assert every yielded chunk is a real content chunk (R1.6, GW34 V13).

    The gateway's ``amf.trace`` frame is an SSE COMMENT line (``: amf.trace {json}``) since the
    owner decision of 4 Nov 2026; a compliant SSE parser -- the stock SDK's ``Stream`` included --
    skips a comment line outright, so it never reaches the iterator as a bare
    ``ChatCompletionChunk``. This helper is kept as the R1.6 assertion point: every chunk the SDK
    actually yields must carry ``choices``.
    """
    for chunk in chunks:
        choices = chunk.choices  # type: ignore[attr-defined]
        assert choices, f"a non-content frame (amf.trace?) reached the SDK iterator: {chunk!r}"
    return list(chunks)


@pytest.fixture(scope="module")
def gateway() -> Iterator[tuple[LoopbackServer, object]]:
    provider = scripted_provider()
    with LoopbackServer(build_matrix_app(contract=_CONTRACT, provider=provider)) as server:
        yield server, provider


def _msg(name: str) -> list[dict[str, object]]:
    return [{"role": "user", "content": marker(name)}]


def _sdk(client: str, gateway: tuple[LoopbackServer, object], *, retries: int = 0) -> object:
    return facade_for(client, gateway[0].base_url, max_retries=retries)


@pytest.mark.parametrize("client", _CLIENTS)
@pytest.mark.matrix(client="python-sync", cell="a")
def test_cell_a_non_stream_chat_completion(client: str, gateway) -> None:  # type: ignore[no-untyped-def]
    sdk = _sdk(client, gateway)
    try:
        response = sdk.create(model=_MODEL, messages=_msg("plain"))  # type: ignore[attr-defined]
    finally:
        sdk.close()  # type: ignore[attr-defined]
    assert response.object == "chat.completion"
    assert response.choices[0].message.content == "Hello from the gateway."
    assert response.choices[0].finish_reason == "stop"


@pytest.mark.parametrize("client", _CLIENTS)
@pytest.mark.matrix(client="python-sync", cell="b")
def test_cell_b_streamed_completion_ends_at_done(client: str, gateway) -> None:  # type: ignore[no-untyped-def]
    sdk = _sdk(client, gateway)
    try:
        raw = sdk.stream(model=_MODEL, messages=_msg("plain"))  # type: ignore[attr-defined]
    finally:
        sdk.close()  # type: ignore[attr-defined]
    chunks = _content_chunks(raw)
    assert chunks, "no chunks received"
    assert chunks[0].choices[0].delta.role == "assistant"
    text = "".join(c.choices[0].delta.content or "" for c in chunks)
    assert text == "Hello from the gateway."
    assert chunks[-1].choices[0].finish_reason == "stop"
    assert all(c.model_extra in (None, {}) for c in chunks)  # typed objects only
    assert len({c.id for c in chunks}) == 1 and len({c.model for c in chunks}) == 1


@pytest.mark.parametrize("client", _CLIENTS)
@pytest.mark.matrix(client="python-sync", cell="c")
def test_cell_c_stream_helper_accumulates_tool_calls(client: str, gateway) -> None:  # type: ignore[no-untyped-def]
    sdk = _sdk(client, gateway)
    try:
        final = sdk.stream_helper(  # type: ignore[attr-defined]
            model=_MODEL, messages=_msg("tool"), tools=[_WEATHER_TOOL]
        )
    finally:
        sdk.close()  # type: ignore[attr-defined]
    [call] = final.choices[0].message.tool_calls
    assert call.id == "call_abc" and call.function.name == "get_weather"
    assert json.loads(call.function.arguments) == {"city": "Paris", "unit": "c"}
    assert final.choices[0].finish_reason == "tool_calls"


@pytest.mark.parametrize("client", _CLIENTS)
@pytest.mark.matrix(client="python-sync", cell="d")
def test_cell_d_streamed_tool_calls_accumulate_across_fragments(client: str, gateway) -> None:  # type: ignore[no-untyped-def]
    sdk = _sdk(client, gateway)
    try:
        raw = sdk.stream(  # type: ignore[attr-defined]
            model=_MODEL, messages=_msg("two_tools"), tools=[_WEATHER_TOOL]
        )
    finally:
        sdk.close()  # type: ignore[attr-defined]
    calls: dict[int, dict[str, str]] = {}
    for chunk in _content_chunks(raw):
        for fragment in chunk.choices[0].delta.tool_calls or ():
            entry = calls.setdefault(fragment.index, {"id": "", "name": "", "arguments": ""})
            entry["id"] += fragment.id or ""
            if fragment.function:
                entry["name"] += fragment.function.name or ""
                entry["arguments"] += fragment.function.arguments or ""
    assert sorted(calls) == [0, 1]  # dense ascending indexes: the SDK merges by list position
    assert calls[0] == {"id": "call_a", "name": "get_weather", "arguments": '{"city":"Paris"}'}
    assert calls[1] == {"id": "call_b", "name": "get_weather", "arguments": '{"city":"Rome"}'}


@pytest.mark.parametrize("client", _CLIENTS)
@pytest.mark.matrix(client="python-sync", cell="e")
def test_cell_e_non_stream_tool_call_id_name_arguments(client: str, gateway) -> None:  # type: ignore[no-untyped-def]
    sdk = _sdk(client, gateway)
    try:
        response = sdk.create(  # type: ignore[attr-defined]
            model=_MODEL, messages=_msg("tool"), tools=[_WEATHER_TOOL]
        )
    finally:
        sdk.close()  # type: ignore[attr-defined]
    [call] = response.choices[0].message.tool_calls
    assert call.id == "call_abc" and call.type == "function"
    assert call.function.name == "get_weather"
    assert json.loads(call.function.arguments) == {"city": "Paris", "unit": "c"}
    assert response.choices[0].finish_reason == "tool_calls"


@pytest.mark.parametrize("client", _CLIENTS)
@pytest.mark.matrix(client="python-sync", cell="f")
def test_cell_f_cancellation_closes_the_provider_iterator(client: str, gateway) -> None:  # type: ignore[no-untyped-def]
    server, provider = gateway
    before = len(provider.calls)  # type: ignore[attr-defined]
    sdk = _sdk(client, gateway)
    try:
        n = sdk.close_early(model=_MODEL, messages=_msg("slow"))  # type: ignore[attr-defined]
        assert n == 1
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not provider.calls[before:][0].closed:  # type: ignore[attr-defined]
            time.sleep(0.02)
        assert provider.calls[before].closed == 1  # type: ignore[attr-defined]  # R6.1
        # R6.2: a second request after the cancel still succeeds.
        again = sdk.stream(model=_MODEL, messages=_msg("plain"))  # type: ignore[attr-defined]
        text = "".join(c.choices[0].delta.content or "" for c in _content_chunks(again))
        assert text == "Hello from the gateway."
    finally:
        sdk.close()  # type: ignore[attr-defined]


@pytest.mark.parametrize("client", _CLIENTS)
@pytest.mark.matrix(client="python-sync", cell="g")
def test_cell_g_embeddings(client: str, gateway) -> None:  # type: ignore[no-untyped-def]
    sdk = _sdk(client, gateway)
    try:
        response = sdk.embeddings(model=_MODEL, input="one string")  # type: ignore[attr-defined]
    finally:
        sdk.close()  # type: ignore[attr-defined]
    assert response.object == "list" and len(response.data) == 1
    assert isinstance(response.data[0].embedding, list)


@pytest.mark.parametrize("client", _CLIENTS)
@pytest.mark.matrix(client="python-sync", cell="h")
def test_cell_h_responses_create_and_stream(client: str, gateway) -> None:  # type: ignore[no-untyped-def]
    sdk = _sdk(client, gateway)
    try:
        response = sdk.responses_create(model=_MODEL, input=marker("plain"))  # type: ignore[attr-defined]
        events = sdk.responses_stream(model=_MODEL, input=marker("plain"))  # type: ignore[attr-defined]
    finally:
        sdk.close()  # type: ignore[attr-defined]
    assert response.object == "response" and response.status == "completed"
    assert events and events[-1].type == "response.completed"


@pytest.mark.parametrize("client", _CLIENTS)
@pytest.mark.matrix(client="python-sync", cell="i")
def test_cell_i_error_classes(client: str, gateway) -> None:  # type: ignore[no-untyped-def]
    server, _provider = gateway
    bad_key = facade_for(client, server.base_url)
    bad_key.client.api_key = "sk-wrong"  # type: ignore[attr-defined]
    sdk = _sdk(client, gateway)
    try:
        with pytest.raises(openai.AuthenticationError):
            bad_key.create(model=_MODEL, messages=_msg("plain"))  # type: ignore[attr-defined]
        with pytest.raises(openai.BadRequestError) as blocked:
            sdk.create(  # type: ignore[attr-defined]
                model=_MODEL, messages=[{"role": "user", "content": "key AKIAIOSFODNN7EXAMPLE"}]
            )
        with pytest.raises(openai.NotFoundError):
            sdk.files_list()  # type: ignore[attr-defined]
        with pytest.raises(openai.BadRequestError):
            sdk.create(model=_MODEL, messages=_msg("plain"), logprobs=True)  # type: ignore[attr-defined]
    finally:
        sdk.close()  # type: ignore[attr-defined]
        bad_key.close()  # type: ignore[attr-defined]
    body = blocked.value.body
    assert isinstance(body, dict) and body.get("code") == "input_blocked"


@pytest.mark.parametrize("client", _CLIENTS)
@pytest.mark.matrix(client="python-sync", cell="j")
def test_cell_j_pre_byte_refusal_raises_from_create_stream_true(client: str, gateway) -> None:  # type: ignore[no-untyped-def]
    sdk = _sdk(client, gateway)
    try:
        with pytest.raises(openai.PermissionDeniedError) as blocked:  # output_blocked: HTTP 403
            sdk.stream(model=_MODEL, messages=_msg("blocked_output"))  # type: ignore[attr-defined]
        with pytest.raises(openai.APIStatusError) as timed_out:
            sdk.stream(model=_MODEL, messages=_msg("upstream_timeout"))  # type: ignore[attr-defined]
        with pytest.raises(openai.BadRequestError):
            sdk.stream(  # type: ignore[attr-defined]
                model=_MODEL,
                messages=[{"role": "user", "content": [{"type": "image_url", "image_url": {}}]}],
            )
    finally:
        sdk.close()  # type: ignore[attr-defined]
    assert blocked.value.status_code == 403 and timed_out.value.status_code == 504
    assert isinstance(blocked.value.body, dict) and blocked.value.body["code"] == "output_blocked"


@pytest.mark.parametrize("client", _CLIENTS)
def test_json_mode_and_done_last_once(client: str, gateway) -> None:  # type: ignore[no-untyped-def]
    server, _provider = gateway
    with httpx.Client(base_url=server.base_url) as raw:
        body = {"model": _MODEL, "stream": True, "messages": _msg("plain")}
        with raw.stream("POST", "/v1/chat/completions", json=body,
                        headers={"authorization": f"Bearer {API_KEY}"}) as response:
            assert response.headers["content-type"].startswith("text/event-stream")
            assert response.headers["x-request-id"].startswith("zs-")
            text = response.read().decode()
    assert text.count("data: [DONE]") == 1 and text.rstrip().endswith("data: [DONE]")
    # GW34 V13: the trace is a single-line SSE comment, before [DONE], never a named event.
    assert "event: amf.trace" not in text
    [trace_line] = [ln for ln in text.splitlines() if ln.startswith(": amf.trace ")]
    assert isinstance(json.loads(trace_line[len(": amf.trace "):]), dict)
    assert text.index(": amf.trace ") < text.index("[DONE]")
    frames = [json.loads(line[6:]) for line in text.splitlines()
              if line.startswith("data: ") and line != "data: [DONE]" and "choices" in line]
    assert len({f["id"] for f in frames}) == 1 and len({f["model"] for f in frames}) == 1
