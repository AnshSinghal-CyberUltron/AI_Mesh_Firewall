"""PHASE 2 hidden-failure-hunter — NEW param defects + adversarial CHALLENGES.

Two jobs:
  1) CHALLENGE the parallel session's framing that several drops are
     "responses-only asymmetries vs a chat path that forwards them" — prove the
     CHAT path ALSO drops them (so the real defect is bigger / mis-seamed).
  2) HUNT new param defects through chat AND responses->chat via a capturing
     upstream: confirm the in-allowlist params (parallel_tool_calls, tool_choice,
     stream_options) actually survive, and find the ones that silently vanish.

xfail(strict=True) = confirmed defect (XFAIL today).  Unmarked test that PASSes
= a claim is wrong / behavior is correct.

Run: cd gateway && .venv/bin/python -m pytest \
  ai_mesh_gateway/tests/_p2_hidden-failure-hunter.py -q -rxX
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import httpx
import openai
import pytest
import pytest_asyncio

import ai_mesh_gateway.main as gm
from ai_mesh_gateway.responses_adapters import responses_to_chat
from ai_mesh_gateway.tests import test_openai_sdk_compat as T


async def _capf(cap):
    async def _cap(body, redacted_prompt=None, **_kw):
        cap.clear()
        cap.update(body)
        return 200, {
            "id": "chatcmpl-x", "object": "chat.completion", "created": 1700000000,
            "model": "gpt-4o-mini",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }
    return _cap


@pytest_asyncio.fixture()
async def appctx(monkeypatch):
    app, auth_redis = await T._make_sdk_app(monkeypatch, redis_client=None)
    cap = {}
    gm.LLM_ROUTER.acompletion = AsyncMock(side_effect=await _capf(cap))
    yield app, cap
    await auth_redis.aclose()


def _client(app):
    return openai.AsyncOpenAI(
        base_url="http://testserver/v1", api_key=T.API_KEY,
        http_client=httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver"),
        max_retries=0,
    )


async def _chat_capture(app, cap, **kw):
    """Drive a stock-SDK chat.completions.create; return the forwarded chat body."""
    c = _client(app)
    try:
        await c.chat.completions.create(
            model="gpt-4o-mini", messages=[{"role": "user", "content": "hi"}], **kw)
    finally:
        await c.close()
    return dict(cap)


async def _resp_capture(app, cap, **extra):
    """Drive a stock-SDK responses.create; return the forwarded chat body."""
    c = _client(app)
    try:
        await c.responses.create(model="gpt-4o-mini", input="hi", extra_body=extra)
    finally:
        await c.close()
    return dict(cap)


# ════════════════════════════════════════════════════════════════════════════
# PART 1 — CHALLENGES: prove these are NOT "responses-only" asymmetries.
# The chat-path normalizer (OPENAI_TOP_LEVEL_KEYS) strips these too, so the
# parallel session's "chat path forwards it" premise is false.
# ════════════════════════════════════════════════════════════════════════════

@pytest.mark.xfail(strict=True, reason="CHALLENGE P2-RESP-drops-logit_bias premise: the parallel test says the CHAT path forwards logit_bias. It does NOT — logit_bias is absent from OPENAI_TOP_LEVEL_KEYS (normalizer.py:15) so the chat normalizer strips it before forwarding. The defect is a TWO-LAYER drop (normalizer + adapter allowlist), not a responses-only asymmetry.")
@pytest.mark.asyncio
async def test_CHAT_path_also_drops_logit_bias(appctx):
    app, cap = appctx
    body = await _chat_capture(app, cap, logit_bias={"123": -100})
    assert body.get("logit_bias") == {"123": -100}, \
        f"chat path forwarded logit_bias? -> {body.get('logit_bias')}"


@pytest.mark.xfail(strict=True, reason="CHALLENGE P2-RESP-drops-service_tier premise: service_tier is dropped on the CHAT path too (not in OPENAI_TOP_LEVEL_KEYS). Adding it only to _RESP_DIRECT_PASSTHROUGH is INSUFFICIENT — the inner chat normalizer re-strips it on the responses path. Root cause is the normalizer allowlist, not just the adapter.")
@pytest.mark.asyncio
async def test_CHAT_path_also_drops_service_tier(appctx):
    app, cap = appctx
    body = await _chat_capture(app, cap, service_tier="flex")
    assert body.get("service_tier") == "flex", \
        f"chat path forwarded service_tier? -> {body.get('service_tier')}"


@pytest.mark.xfail(strict=True, reason="CHALLENGE P2-RESP-drops-prediction premise: predicted outputs are dropped on the CHAT path too (not in OPENAI_TOP_LEVEL_KEYS). Same two-layer root cause.")
@pytest.mark.asyncio
async def test_CHAT_path_also_drops_prediction(appctx):
    app, cap = appctx
    body = await _chat_capture(app, cap, prediction={"type": "content", "content": "draft"})
    assert "prediction" in body, f"chat path body keys -> {sorted(body)}"


# Does fixing _RESP_DIRECT_PASSTHROUGH alone fix the responses path? NO — the
# inner chat normalizer strips it. Prove the two-layer trap with response_format
# (which IS in OPENAI_TOP_LEVEL_KEYS, so it would survive) vs service_tier (which
# is NOT). This documents WHY the parallel fix-recipe is incomplete.
@pytest.mark.xfail(strict=True, reason="ROOT-CAUSE PROOF P2-RESP-allowlist-fix-insufficient: even simulating the proposed fix (service_tier added to _RESP_DIRECT_PASSTHROUGH so responses_to_chat emits it), the value still won't reach upstream on the responses path because _dispatch_chat_internally re-enters proxy_chat which re-runs normalize_openai_chat_request(strip_unknown_top_level=True) and OPENAI_TOP_LEVEL_KEYS has no service_tier. End-to-end the param is dropped. Captured here via the live responses endpoint.")
@pytest.mark.asyncio
async def test_responses_service_tier_dropped_end_to_end(appctx):
    app, cap = appctx
    body = await _resp_capture(app, cap, service_tier="flex")
    assert body.get("service_tier") == "flex", \
        f"responses->chat forwarded service_tier end-to-end? -> {body.get('service_tier')}"


# ════════════════════════════════════════════════════════════════════════════
# PART 2 — CONFIRM the allowlisted params actually survive (parallel claim).
# These SHOULD pass. If a "survives" param secretly dies, the test XFAILs and we
# have a new defect.
# ════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_responses_parallel_tool_calls_survives(appctx):
    """parallel_tool_calls IS in _RESP_DIRECT_PASSTHROUGH and IS in
    OPENAI_TOP_LEVEL_KEYS — confirm it actually reaches upstream. Expect PASS."""
    app, cap = appctx
    body = await _resp_capture(app, cap, tools=[{"type": "function", "function": {"name": "f", "parameters": {"type": "object", "properties": {}}}}],
                               parallel_tool_calls=False)
    assert body.get("parallel_tool_calls") is False, f"body keys -> {sorted(body)}"


@pytest.mark.asyncio
async def test_responses_tool_choice_survives(appctx):
    """tool_choice IS allowlisted in both layers — confirm forwarded. Expect PASS."""
    app, cap = appctx
    body = await _resp_capture(app, cap,
                               tools=[{"type": "function", "function": {"name": "f", "parameters": {"type": "object", "properties": {}}}}],
                               tool_choice="auto")
    assert body.get("tool_choice") == "auto", f"body keys -> {sorted(body)}"


@pytest.mark.asyncio
async def test_responses_stream_options_survives(appctx):
    """stream_options IS allowlisted in both layers. Non-streaming carry is fine to
    check (we only assert the adapter+normalizer keep the key). Expect PASS."""
    app, cap = appctx
    # pure unit-level (avoid streaming machinery): confirm responses_to_chat keeps it
    chat = responses_to_chat({"model": "gpt-4o-mini", "input": "hi",
                              "stream_options": {"include_usage": True}})
    assert chat.get("stream_options") == {"include_usage": True}


@pytest.mark.asyncio
async def test_responses_reasoning_effort_survives_end_to_end(appctx):
    """reasoning.effort -> reasoning_effort is special-cased in responses_to_chat AND
    reasoning_effort IS in OPENAI_TOP_LEVEL_KEYS, so it must reach upstream. Expect PASS."""
    app, cap = appctx
    body = await _resp_capture(app, cap, reasoning={"effort": "high"})
    assert body.get("reasoning_effort") == "high", f"body keys -> {sorted(body)}"


# ════════════════════════════════════════════════════════════════════════════
# PART 3 — NEW defect hunt: max_completion_tokens vs max_tokens on the RESPONSES
# path, and input-content collapse correctness.
# ════════════════════════════════════════════════════════════════════════════

@pytest.mark.xfail(strict=True, reason="NEW P2-RESP-MCT-double-injection (MED): on the responses path, a client sending max_output_tokens (-> chat max_tokens) then the inner proxy_chat's max_response_tokens enforcement is fine, BUT if a client sends max_completion_tokens to /v1/responses, responses_to_chat maps it to max_tokens AND the inner proxy_chat else-branch (main.py:5278) can still inject. Here we assert the simpler/真 defect: responses_to_chat maps max_completion_tokens to max_tokens, so on the responses path max_completion_tokens NEVER reaches upstream as max_completion_tokens — a reasoning model behind /v1/responses gets max_tokens (deprecated for o-series) instead. Asymmetry vs chat path which forwards max_completion_tokens verbatim.")
@pytest.mark.asyncio
async def test_responses_max_completion_tokens_not_silently_renamed(appctx):
    app, cap = appctx
    body = await _resp_capture(app, cap, max_completion_tokens=64)
    # OpenAI Responses semantics: max_completion_tokens should reach upstream as
    # max_completion_tokens for reasoning models, not be renamed to max_tokens.
    assert "max_completion_tokens" in body and body.get("max_completion_tokens") == 64, \
        f"renamed away: keys={sorted(body)} max_tokens={body.get('max_tokens')}"


@pytest.mark.asyncio
async def test_responses_list_input_text_collapse_correct(appctx):
    """list/typed input with a single input_text part collapses to a string content.
    Confirm the collapsed message preserves the text exactly. Expect PASS (correctness)."""
    chat = responses_to_chat({"model": "gpt-4o-mini",
                              "input": [{"role": "user", "content": [{"type": "input_text", "text": "alpha"}]}]})
    msgs = chat["messages"]
    assert msgs and msgs[-1] == {"role": "user", "content": "alpha"}, f"msgs={msgs}"


@pytest.mark.xfail(strict=True, reason="NEW P2-RESP-multi-text-parts-merge-gap (LOW-MED): when an input item has MULTIPLE input_text parts, OpenAI concatenates them into the single user turn's text. responses_to_chat instead emits a chat content-PARTS list [{text},{text}] (no collapse since len>1), which is still semantically fine for chat — BUT a part with an UNKNOWN ctype (e.g. 'input_file', 'refusal', 'reasoning') is silently DROPPED with no error, so a multimodal/file Responses input loses content invisibly. Here: an input_file part is dropped, leaving only the text -> data loss with no signal.")
@pytest.mark.asyncio
async def test_responses_unknown_input_part_not_silently_dropped(appctx):
    chat = responses_to_chat({"model": "gpt-4o-mini", "input": [
        {"role": "user", "content": [
            {"type": "input_text", "text": "see file"},
            {"type": "input_file", "file_id": "file-abc"},
        ]}]})
    msg = chat["messages"][-1]
    # The file reference must not vanish silently. Either an error, or the part is
    # carried. We assert it is carried (a list with 2 parts).
    content = msg.get("content")
    assert isinstance(content, list) and len(content) == 2, \
        f"input_file part dropped silently; content={content!r}"


# ════════════════════════════════════════════════════════════════════════════
# PART 4 — function_call_output tool-result mapping correctness.
# ════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_function_call_output_maps_to_tool_message(appctx):
    """function_call_output -> {role:tool, tool_call_id, content}. Confirm the
    call_id is preserved (replay correctness). Expect PASS."""
    chat = responses_to_chat({"model": "gpt-4o-mini", "input": [
        {"type": "function_call_output", "call_id": "call_123", "output": "42"}]})
    m = chat["messages"][-1]
    assert m["role"] == "tool" and m["tool_call_id"] == "call_123" and m["content"] == "42", f"m={m}"


@pytest.mark.xfail(strict=True, reason="NEW P2-RESP-function_call_output-id-fallback-collision (LOW): when a function_call_output item omits BOTH call_id and id, _input_item_to_message sets tool_call_id='' (empty string). An empty tool_call_id is INVALID per OpenAI (tool messages MUST reference a prior tool call id) and upstream rejects it with a 400 that surfaces as an opaque error rather than a clean gateway 400. The adapter should reject/validate, not forward an empty id.")
@pytest.mark.asyncio
async def test_function_call_output_missing_id_rejected(appctx):
    app, cap = appctx
    c = _client(app)
    try:
        # raw responses call with a malformed tool result (no call_id/id)
        r = await c.responses.create(model="gpt-4o-mini",
                                     input=[{"type": "function_call_output", "output": "x"}])
        # If we got here the gateway forwarded an empty tool_call_id; the captured
        # chat body proves the malformed tool message reached upstream.
    finally:
        await c.close()
    m = cap.get("messages", [])[-1] if cap.get("messages") else {}
    assert m.get("role") != "tool" or m.get("tool_call_id"), \
        f"forwarded tool message with empty tool_call_id: {m}"


# ════════════════════════════════════════════════════════════════════════════
# PART 5 — CHAT-path asymmetry: top_logprobs, response_format survive on chat.
# ════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_chat_top_logprobs_survives(appctx):
    """top_logprobs IS in OPENAI_TOP_LEVEL_KEYS -> survives on the chat path.
    (Contrast: responses path drops it — that's the parallel P2-RESP-drops-top_logprobs.)
    Expect PASS — proving the chat path is the correct reference."""
    app, cap = appctx
    body = await _chat_capture(app, cap, logprobs=True, top_logprobs=3)
    assert body.get("top_logprobs") == 3, f"body keys -> {sorted(body)}"


@pytest.mark.asyncio
async def test_chat_response_format_survives(appctx):
    """response_format IS in OPENAI_TOP_LEVEL_KEYS -> survives on chat. Contrast with
    responses path which drops it (parallel P2-RESP-drops-response_format). Expect PASS."""
    app, cap = appctx
    body = await _chat_capture(app, cap, response_format={"type": "json_object"})
    assert body.get("response_format") == {"type": "json_object"}, f"body keys -> {sorted(body)}"
