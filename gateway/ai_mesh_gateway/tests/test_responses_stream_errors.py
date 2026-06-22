"""Responses stream translator must not mask chat-path output blocks as completed."""
from __future__ import annotations

import json

import pytest


async def _collect(stream):
    chunks = []
    async for c in stream:
        chunks.append(c)
    return chunks


async def _translate(chat_stream, response_id="resp_test", model="gpt-4o-mini", created_at=1700000000, store_ctx=None):
    from ai_mesh_gateway import main as gateway_main
    fn = gateway_main._translate_chat_stream_to_responses
    async for chunk in fn(chat_stream, response_id, model, created_at, store_ctx or {}):
        yield chunk


@pytest.mark.asyncio
async def test_mid_stream_output_block_emits_failed_not_completed():
    async def chat_stream():
        yield 'data: {"choices":[{"delta":{"content":"leaked "}}]}\n\n'
        yield (
            'data: {"error":{"message":"blocked","type":"output_blocked",'
            '"code":"output_blocked"}}\n\n'
        )
        yield "data: [DONE]\n\n"

    chunks = await _collect(_translate(chat_stream()))
    body = "".join(chunks)
    assert "response.failed" in body
    assert "response.completed" not in body
    failed_part = next(p for p in body.split("\n\n") if "response.failed" in p)
    data_line = next(l for l in failed_part.split("\n") if l.startswith("data:"))
    event = json.loads(data_line[5:])
    assert event["type"] == "response.failed"
    assert event["response"]["status"] == "failed"
    assert event["response"].get("output_text", "") == ""


@pytest.mark.asyncio
async def test_error_only_stream_emits_failed_not_completed():
    async def chat_stream():
        yield (
            'data: {"error":{"message":"blocked","type":"output_blocked",'
            '"code":"output_blocked"}}\n\n'
        )
        yield "data: [DONE]\n\n"

    chunks = await _collect(_translate(chat_stream()))
    body = "".join(chunks)
    assert "response.failed" in body
    assert "response.completed" not in body


@pytest.mark.asyncio
async def test_happy_path_still_emits_completed():
    async def chat_stream():
        yield 'data: {"choices":[{"delta":{"content":"ok"}}]}\n\n'
        yield 'data: {"choices":[{"finish_reason":"stop"}]}\n\n'
        yield "data: [DONE]\n\n"

    chunks = await _collect(_translate(chat_stream()))
    body = "".join(chunks)
    assert "response.completed" in body
    assert "response.failed" not in body
