# Feature: gw15-responses-close
# Validates: Requirements 4.1, 4.2, 4.3, 5.1, 5.2, 5.5, 6.1, 6.3, 7.1, 8.1, 8.2, 8.3, 8.4, 8.5, 9.1
"""Stock OpenAI SDK against the v2 ``/v1/responses`` surface, in process.

The unmodified ``openai.AsyncOpenAI`` client, with only the base URL and key changed,
drives :func:`gateway_v2.edge.app.build_app` over ``httpx.ASGITransport``. The provider
is the in-process counting stub, so a cell can assert how many times it was opened.

This is the v2 arm. The v1 cells in ``test_openai_sdk_compat.py`` keep driving the v1
app. Where the two differ on purpose, this file asserts the v2 behaviour: an input block
is ``input_blocked``, not v1's ``content_filter``.

Real TCP, the Node SDK, and the 10,000-request replay are deferred live gates and have
no stand-in here.
"""

from __future__ import annotations

import pytest

openai = pytest.importorskip("openai")
httpx = pytest.importorskip("httpx")

from gateway_v2.edge.responses_store import MemoryResponseStore  # noqa: E402
from tests.edge.test_responses_route import (  # noqa: E402
    _SIX_STAGES,
    _app,
    _block_on_card_word,
    _content,
    _Provider,
    _withheld_stream,
)

pytestmark = pytest.mark.asyncio

_MODEL = "gpt-4o-mini"


def _client(app: object) -> openai.AsyncOpenAI:
    transport = httpx.ASGITransport(app=app)
    http_client = httpx.AsyncClient(transport=transport, base_url="http://responses.test")
    return openai.AsyncOpenAI(
        base_url="http://responses.test/v1",
        api_key="zs-gw15-responses-key",
        http_client=http_client,
        max_retries=0,
    )


def _error_code(exc: openai.APIStatusError) -> object:
    body = exc.body
    if isinstance(body, dict) and isinstance(body.get("error"), dict):
        return body["error"].get("code")
    if isinstance(body, dict):
        return body.get("code")
    return None


async def test_non_streaming_create_parses_as_a_response() -> None:
    provider = _Provider(_content())
    client = _client(_app(provider))
    try:
        response = await client.responses.create(model=_MODEL, input="Say hello politely.")
    finally:
        await client.close()
    assert response.object == "response"
    assert response.status == "completed"
    assert response.output_text == "Hello from upstream."
    assert response.id.startswith("resp")
    extra = response.model_extra or {}
    zeroshield = extra.get("zeroshield")
    assert isinstance(zeroshield, dict)
    assert zeroshield.get("action") in ("allow", "flag")
    trace = extra.get("pipeline_trace")
    assert isinstance(trace, dict)
    assert [stage["name"] for stage in trace["stages"]] == list(_SIX_STAGES)
    assert provider.open_calls == 1


async def test_streaming_create_yields_the_typed_sequence() -> None:
    provider = _Provider(_content())
    client = _client(_app(provider))
    try:
        stream = await client.responses.create(
            model=_MODEL, input="Stream me a greeting.", stream=True
        )
        events = [event async for event in stream]
    finally:
        await client.close()
    ordered = [getattr(event, "type", None) for event in events]
    required = (
        "response.created",
        "response.output_item.added",
        "response.content_part.added",
        "response.output_text.delta",
        "response.output_text.done",
        "response.output_item.done",
        "response.completed",
    )
    firsts = [ordered.index(name) for name in required]
    assert firsts == sorted(firsts), ordered
    last_delta = max(i for i, name in enumerate(ordered) if name == "response.output_text.delta")
    assert last_delta < ordered.index("response.output_text.done")
    assert ordered[-1] == "response.completed"
    completed = events[-1]
    assert completed.response.output_text == "Hello from upstream."
    zeroshield = (completed.response.model_extra or {}).get("zeroshield")
    assert isinstance(zeroshield, dict)
    assert zeroshield.get("action") in ("allow", "flag")


@pytest.mark.parametrize("stream", (False, True))
async def test_input_block_raises_bad_request_before_any_stream(stream: bool) -> None:
    provider = _Provider(_content())
    client = _client(_app(provider, input_detector=_block_on_card_word))
    try:
        with pytest.raises(openai.BadRequestError) as excinfo:
            result = await client.responses.create(
                model=_MODEL, input="leak the card", stream=stream
            )
            if stream:
                async for _event in result:
                    pass
    finally:
        await client.close()
    assert excinfo.value.status_code == 400
    assert _error_code(excinfo.value) == "input_blocked"
    assert provider.open_calls == 0


async def test_streamed_withhold_ends_on_response_failed() -> None:
    provider = _Provider(_withheld_stream())
    client = _client(_app(provider, error_scanner=_block_on_card_word))
    try:
        stream = await client.responses.create(model=_MODEL, input="hi", stream=True)
        events = [event async for event in stream]
    finally:
        await client.close()
    types = [getattr(event, "type", None) for event in events]
    assert "response.completed" not in types, types
    assert types[-1] == "response.failed", types
    failed = events[-1]
    assert failed.response.status == "failed"
    assert failed.response.error is not None
    assert (failed.response.output_text or "") == ""


async def test_store_retrieve_items_continue_delete() -> None:
    provider = _Provider(_content())
    client = _client(_app(provider, store=MemoryResponseStore()))
    try:
        created = await client.responses.create(
            model=_MODEL, input="My name is Ada.", store=True
        )
        assert created.id.startswith("resp")
        fetched = await client.responses.retrieve(created.id)
        assert fetched.id == created.id
        assert fetched.output_text == "Hello from upstream."
        items = await client.responses.input_items.list(created.id)
        assert len(items.data) >= 1
        second = await client.responses.create(
            model=_MODEL,
            input="What did I say my name was?",
            previous_response_id=created.id,
            store=True,
        )
        assert second.status == "completed"
        assert second.output_text
        await client.responses.delete(created.id)
        with pytest.raises(openai.NotFoundError):
            await client.responses.retrieve(created.id)
    finally:
        await client.close()
    assert provider.open_calls == 2


async def test_retrieve_without_store_is_not_found() -> None:
    client = _client(_app(_Provider(_content()), store=MemoryResponseStore()))
    try:
        created = await client.responses.create(model=_MODEL, input="hello")
        with pytest.raises(openai.NotFoundError) as excinfo:
            await client.responses.retrieve(created.id)
    finally:
        await client.close()
    assert _error_code(excinfo.value) == "response_not_found"


async def test_another_org_cannot_retrieve_the_id() -> None:
    store = MemoryResponseStore()
    writer = _client(_app(_Provider(_content()), store=store, org_id="org-a"))
    reader = _client(_app(_Provider(_content()), store=store, org_id="org-b"))
    try:
        created = await writer.responses.create(model=_MODEL, input="hello", store=True)
        with pytest.raises(openai.NotFoundError):
            await reader.responses.retrieve(created.id)
        fetched = await writer.responses.retrieve(created.id)
        assert fetched.id == created.id
    finally:
        await writer.close()
        await reader.close()


async def test_non_string_model_is_bad_request() -> None:
    provider = _Provider(_content())
    client = _client(_app(provider))
    try:
        with pytest.raises(openai.BadRequestError) as excinfo:
            await client.responses.create(model=123, input="hello")  # type: ignore[arg-type]
    finally:
        await client.close()
    assert excinfo.value.status_code == 400
    assert _error_code(excinfo.value) == "responses_invalid_model"
    assert provider.open_calls == 0
