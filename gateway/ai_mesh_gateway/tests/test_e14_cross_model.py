"""E14: CROSS-MODEL output-guard parity (no model/provider can skip the guard).

ADVERSARIAL HUNT
================
Claim under attack: "a verbose model leaks what a terse one redacts" — i.e. some
model / provider / response shape lets a secret reach the client UNSCANNED while
the same secret in a different shape is caught.

The output guard has four entry points (per recon):
  * non-stream main path        main.py ~7125  (_extract_scannable_output_text)
  * streaming path              secure_streaming.py ~233 (_extract_content_delta)
  * responses-API non-stream    proxy_responses -> proxy_chat (re-uses chat body)
  * responses-API STREAM        proxy_responses -> chat SecureStreamingResponse iterator
  * sync_pre_llm tier-2 path     main.py ~6452 (_apply_output_guard_nonstream)

ALL of them funnel the model response through TWO model-agnostic choke points:
  1. SCAN extraction:  ``_extract_scannable_output_text`` (non-stream) /
     ``SecureStreamingResponse._extract_content_delta`` (stream).
  2. ENFORCEMENT mutation: ``_set_completion_response_text`` (non-stream) /
     ``_rebuild_sse_content`` + ``_blank_streaming_secondary_channels`` (stream).

The bypass surface a "verbose vs terse" model differential could exploit is a
SECONDARY text-bearing channel (tool_calls / function_call args / reasoning_content
/ refusal / audio transcript) or a NON-FIRST choice (n>1) that one model populates
and another does not. If the choke points only looked at ``choices[0].message.content``,
a verbose model that put the secret in ``tool_calls[0].function.arguments`` or in
``choices[1]`` would ship it unscanned.

These tests assert the choke points are COMPLETE and model-agnostic:
  * SCAN folds content + reasoning_content + refusal + audio.transcript +
    tool_calls(name/args) + legacy function_call(name/args) across EVERY choice.
  * ENFORCEMENT (redact/rewrite/block mutation) overwrites EVERY choice's content
    AND blanks EVERY secondary channel in EVERY choice — so a redacted response
    cannot ship a residual secret in a tool-call / reasoning / later choice.
  * The behavior does NOT depend on the ``model`` / provider id in the completion
    (Bedrock-style ``anthropic/claude-*`` vs OpenRouter-style ``openrouter/...``
    vs OpenAI ``gpt-*`` all yield the IDENTICAL scan input & enforcement result).

Pure-helper tests (no litellm / no live model): the helpers ARE the per-request
behavior every path shares, so proving the invariant here proves it for every
model on every path.
"""
from __future__ import annotations

import json

import pytest

import ai_mesh_gateway.main as gm
from ai_mesh_gateway.secure_streaming import SecureStreamingResponse


# A leaked secret + PII string. If ANY channel ships it unscanned, that channel is
# a bypass.
SECRET = "sk-live-DEADBEEF1234567890SECRETKEY"
PII = "alex.victim@example.com"

# A representative spread of provider / model ids. The guard MUST treat them
# identically — no provider/model branch is allowed in the scan/enforce path.
MODEL_IDS = [
    "gpt-4o-mini",                                   # OpenAI direct
    "anthropic/claude-3-5-haiku",                    # Bedrock-style upstream id
    "openrouter/meta-llama/llama-3.1-8b-instruct",   # OpenRouter-style id
    "zeroshield-model",                              # scrubbed/aliased id
    "",                                              # absent model field
]


def _completion(model: str, *, secret_channel: str, n_choices: int = 1) -> dict:
    """Build an OpenAI chat completion that hides ``SECRET``/``PII`` in a chosen
    secondary channel of the (LAST) choice, with benign primary content. A guard
    that only reads choices[0].message.content would miss it."""
    choices = []
    for i in range(n_choices):
        is_target = i == n_choices - 1  # hide the secret in the LAST choice
        msg: dict = {"role": "assistant", "content": "Here is the answer."}
        if is_target:
            if secret_channel == "content":
                msg["content"] = f"The key is {SECRET} for {PII}."
            elif secret_channel == "reasoning_content":
                msg["reasoning_content"] = f"Internally the key is {SECRET}."
            elif secret_channel == "refusal":
                msg["refusal"] = f"I refuse but the key was {SECRET}."
            elif secret_channel == "tool_calls":
                msg["tool_calls"] = [{
                    "id": "call_1", "type": "function",
                    "function": {"name": "exfiltrate",
                                 "arguments": json.dumps({"key": SECRET, "to": PII})},
                }]
            elif secret_channel == "function_call":
                msg["function_call"] = {"name": "send",
                                        "arguments": json.dumps({"key": SECRET})}
            elif secret_channel == "audio":
                msg["audio"] = {"id": "aud_1", "transcript": f"the key is {SECRET}"}
            elif secret_channel == "list_content":
                # G57: multimodal / content-block shape — content is a LIST of
                # content-part dicts, not a str. The non-stream scanner previously
                # only read str content, so an all-list answer yielded EMPTY scan
                # text and the output guard was skipped entirely (raw egress).
                msg["content"] = [
                    {"type": "text", "text": "Here is the answer. "},
                    {"type": "text", "text": f"The key is {SECRET} for {PII}."},
                ]
            elif secret_channel == "list_reasoning":
                # G61: structured reasoning_content (a LIST of blocks) — the str-only
                # scan/enforce skipped it, leaking PII in a reasoning channel.
                msg["reasoning_content"] = [
                    {"type": "text", "text": f"internally the key is {SECRET} for {PII}"}]
            elif secret_channel == "list_refusal":
                msg["refusal"] = [{"type": "text", "text": f"I refuse but the key was {SECRET}"}]
            elif secret_channel == "dict_tool_args":
                # G58: tool-call ``arguments`` as a PARSED DICT (some providers do
                # this) rather than the spec's JSON string. The str-only scan +
                # enforce paths skipped it, so a dict-shaped argument egressed
                # unscanned AND un-neutralized.
                msg["tool_calls"] = [{
                    "id": "call_2", "type": "function",
                    "function": {"name": "exfiltrate",
                                 "arguments": {"key": SECRET, "to": PII}},
                }]
        choices.append({"index": i, "message": msg, "finish_reason": "stop"})
    return {"id": "chatcmpl-x", "object": "chat.completion", "model": model,
            "choices": choices}


SECONDARY_CHANNELS = ["content", "reasoning_content", "refusal",
                      "tool_calls", "function_call", "audio", "list_content",
                      "dict_tool_args", "list_reasoning", "list_refusal"]


# ──────────────────────────────────────────────────────────────────────────────
# 1. SCAN EXTRACTION folds every secondary channel — regardless of model id.
# ──────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("model", MODEL_IDS)
@pytest.mark.parametrize("channel", SECONDARY_CHANNELS)
def test_nonstream_scan_sees_secret_in_any_channel_any_model(model, channel):
    """The non-stream scan input (_extract_scannable_output_text) must contain the
    secret no matter WHICH channel it hides in or WHICH model produced it. If it
    does not, the guard never sees it -> silent leak (the verbose-model bypass)."""
    completion = _completion(model, secret_channel=channel)
    scan_text = gm._extract_scannable_output_text(completion)
    assert SECRET in scan_text, (
        f"BYPASS: secret in channel={channel!r} model={model!r} is NOT in the "
        f"output-guard scan input. scan_text={scan_text!r}"
    )


@pytest.mark.parametrize("model", MODEL_IDS)
@pytest.mark.parametrize("channel", SECONDARY_CHANNELS)
def test_nonstream_scan_sees_secret_in_late_choice_any_model(model, channel):
    """n>1: hide the secret in choices[1] (a parallel completion a verbose model
    can return). choices[0] is benign. A choices[0]-only scan misses it."""
    completion = _completion(model, secret_channel=channel, n_choices=3)
    scan_text = gm._extract_scannable_output_text(completion)
    assert SECRET in scan_text, (
        f"BYPASS: secret in late choice channel={channel!r} model={model!r} "
        f"escaped the scan. scan_text={scan_text!r}"
    )


def test_nonstream_scan_is_model_invariant():
    """Identical response shape under different model ids must produce the SAME
    scan input — no provider/model branch may change what is scanned."""
    shape = dict(secret_channel="tool_calls", n_choices=2)
    baseline = gm._extract_scannable_output_text(_completion(MODEL_IDS[0], **shape))
    for model in MODEL_IDS[1:]:
        other = gm._extract_scannable_output_text(_completion(model, **shape))
        assert other == baseline, (
            f"MODEL-DIFFERENTIAL: scan input differs for model={model!r} "
            f"vs {MODEL_IDS[0]!r}. A provider branch in the scan path is a bypass."
        )


# ──────────────────────────────────────────────────────────────────────────────
# G57: LIST-shaped (multimodal content-block) content must be scanned + redacted.
# The non-stream extractor read only str content, so an all-list answer yielded
# empty scan text -> the output guard was SKIPPED (_og_scan_text falsy) and PII/
# secrets egressed raw. The streaming path already coerced it (FIX-C); this pins
# the non-stream parity so a refactor cannot silently reopen the asymmetry.
# ──────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("model", MODEL_IDS)
def test_g57_list_content_scanned_not_skipped(model):
    completion = _completion(model, secret_channel="list_content")
    scan_text = gm._extract_scannable_output_text(completion)
    assert scan_text, (
        f"G57: list-shaped content produced EMPTY scan text (model={model!r}) — "
        f"the output guard would be SKIPPED and the answer egress unscanned."
    )
    assert SECRET in scan_text and PII in scan_text, (
        f"G57: secret/PII in list-shaped content is not in the scan input "
        f"(model={model!r}); scan_text={scan_text!r}"
    )


def test_g57_response_extractor_coerces_list_to_str():
    """_extract_response_from_completion (the redaction input) must return a str for
    list-shaped content — else _sanitize_output_for_verdict gets a list and the
    redact path breaks (or forwards raw)."""
    completion = _completion("gpt-4o-mini", secret_channel="list_content")
    rt = gm._extract_response_from_completion(completion)
    assert isinstance(rt, str), f"response text is {type(rt).__name__}, not str"
    assert SECRET in rt and PII in rt, "coerced response text lost the content"


def test_g57_list_content_stream_nonstream_parity():
    """The stream and non-stream extractors must AGREE that list content carries the
    secret — the asymmetry was the leak (stream saw it, non-stream did not)."""
    from ai_mesh_gateway.secure_streaming import SecureStreamingResponse as _S
    completion = _completion("gpt-4o-mini", secret_channel="list_content")
    nonstream = gm._extract_scannable_output_text(completion)
    delta = {"choices": [{"delta": {"content": [
        {"type": "text", "text": f"The key is {SECRET} for {PII}."}]}}]}
    stream = _S._extract_content_delta(_S.__new__(_S), delta)
    assert SECRET in nonstream and SECRET in stream, (
        f"stream/non-stream disagree on list content: nonstream={nonstream!r} stream={stream!r}"
    )


# ──────────────────────────────────────────────────────────────────────────────
# G58: tool-call ``arguments`` as a PARSED DICT (not the spec JSON string). The
# str-only scan + enforce paths skipped it, so a dict-shaped argument egressed
# unscanned AND survived neutralization. The input side already coerced it
# (_extract_prompt_from_messages json.dumps); this pins output-side parity.
# ──────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("model", MODEL_IDS)
def test_g58_dict_tool_args_scanned(model):
    completion = _completion(model, secret_channel="dict_tool_args")
    scan_text = gm._extract_scannable_output_text(completion)
    assert SECRET in scan_text and PII in scan_text, (
        f"G58: dict-shaped tool-call arguments not in scan input (model={model!r}); "
        f"scan_text={scan_text!r}"
    )


def test_g58_dict_tool_args_neutralized_on_enforcement():
    """After enforcement, a dict-shaped tool-call argument must NOT still carry the
    secret — the str-only neutralizer left the dict verbatim (post-redact leak)."""
    completion = _completion("gpt-4o-mini", secret_channel="dict_tool_args")
    gm._set_completion_response_text(completion, "[REDACTED]")
    blob = json.dumps(completion)
    assert SECRET not in blob and PII not in blob, (
        f"G58: dict-shaped tool-call argument survived enforcement. blob={blob[:400]}"
    )


def test_g58_tool_arg_coercion_helper():
    """_tool_arg_to_text coerces dict/None to scannable text; str is identity."""
    assert gm._tool_arg_to_text('{"a":1}') == '{"a":1}'
    assert SECRET in gm._tool_arg_to_text({"key": SECRET})
    assert gm._tool_arg_to_text(None) == ""


# ──────────────────────────────────────────────────────────────────────────────
# G61: structured (list/dict) reasoning_content / refusal — the str-only scan +
# enforce skipped them, leaking PII in a reasoning/refusal channel. The
# list_reasoning / list_refusal matrix rows above cover the list shape; this pins
# the DICT shape incl. the Anthropic-style `thinking` key (not `text`).
# ──────────────────────────────────────────────────────────────────────────────
def test_g61_dict_reasoning_thinking_key_scanned():
    comp = {"choices": [{"message": {"role": "assistant", "content": "ok",
            "reasoning_content": {"thinking": f"the key is {SECRET}"}}}]}
    assert SECRET in gm._extract_scannable_output_text(comp), (
        "G61: dict reasoning_content (thinking key) not scanned"
    )


def test_g61_structured_reasoning_refusal_blanked_on_enforcement():
    comp = {"choices": [{"message": {"role": "assistant", "content": "ok",
            "reasoning_content": [{"type": "text", "text": SECRET}],
            "refusal": {"text": SECRET}}}]}
    gm._set_completion_response_text(comp, "[REDACTED]")
    assert SECRET not in json.dumps(comp), (
        "G61: structured reasoning/refusal survived enforcement"
    )


# ──────────────────────────────────────────────────────────────────────────────
# 2. ENFORCEMENT mutation sanitizes every choice + every secondary channel.
# ──────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("model", MODEL_IDS)
@pytest.mark.parametrize("channel", SECONDARY_CHANNELS)
def test_enforcement_blanks_secret_in_any_channel_any_model(model, channel):
    """After the guard decides to redact/rewrite/block, _set_completion_response_text
    rewrites content AND must neutralize every secondary channel — else a redacted
    response still ships the secret in a tool-call / reasoning / refusal / audio
    channel. Verified across models and across n>1 choices."""
    completion = _completion(model, secret_channel=channel, n_choices=2)
    gm._set_completion_response_text(completion, "[REDACTED BY OUTPUT GUARD]")
    blob = json.dumps(completion)
    assert SECRET not in blob, (
        f"BYPASS: after enforcement the secret survives in channel={channel!r} "
        f"model={model!r}. Post-enforcement response still leaks. blob={blob[:400]}"
    )
    # every primary content is the sanitized text (no original primary content)
    for ch in completion["choices"]:
        assert ch["message"]["content"] == "[REDACTED BY OUTPUT GUARD]"


def test_enforcement_neutralizes_pii_in_secondary_channels():
    """PII (not just the API key) hidden in a tool-call must also be gone after
    enforcement — the neutralizer blanks the WHOLE channel, not a regex subset."""
    completion = _completion("gpt-4o-mini", secret_channel="tool_calls")
    gm._set_completion_response_text(completion, "[REDACTED]")
    assert PII not in json.dumps(completion)


# ──────────────────────────────────────────────────────────────────────────────
# 3. STREAMING scan extraction folds the same channels across all choices.
# ──────────────────────────────────────────────────────────────────────────────
def _delta_chunk(channel: str, n_choices: int = 1) -> dict:
    """A streaming chunk whose LAST choice's delta hides the secret in ``channel``."""
    choices = []
    for i in range(n_choices):
        is_target = i == n_choices - 1
        delta: dict = {"content": "ok"}
        if is_target:
            if channel == "content":
                delta = {"content": f"key {SECRET}"}
            elif channel == "reasoning_content":
                delta = {"reasoning_content": f"key {SECRET}"}
            elif channel == "refusal":
                delta = {"refusal": f"key {SECRET}"}
            elif channel == "tool_calls":
                delta = {"tool_calls": [{"index": 0, "function":
                         {"name": "x", "arguments": SECRET}}]}
            elif channel == "function_call":
                delta = {"function_call": {"name": "x", "arguments": SECRET}}
            elif channel == "audio":
                delta = {"audio": {"transcript": f"key {SECRET}"}}
            elif channel == "list_content":
                delta = {"content": [{"type": "text", "text": f"key {SECRET}"}]}
            elif channel == "dict_tool_args":
                delta = {"tool_calls": [{"index": 0, "function":
                         {"name": "x", "arguments": {"key": SECRET}}}]}
            elif channel == "list_reasoning":
                delta = {"reasoning_content": [{"type": "text", "text": f"key {SECRET}"}]}
            elif channel == "list_refusal":
                delta = {"refusal": [{"type": "text", "text": f"key {SECRET}"}]}
        choices.append({"index": i, "delta": delta})
    return {"choices": choices}


def _new_secure_stream() -> SecureStreamingResponse:
    async def _empty():
        if False:
            yield ""  # pragma: no cover
    return SecureStreamingResponse(_empty(), scanner=None)


@pytest.mark.parametrize("channel", SECONDARY_CHANNELS)
def test_stream_scan_sees_secret_in_any_channel(channel):
    """The streaming guard's per-delta scan text must include the secret no matter
    which channel carries it (FIX-A/FIX-B/R12/R13 parity with the non-stream path)."""
    sse = _new_secure_stream()
    scan_text = sse._extract_content_delta(_delta_chunk(channel))
    assert SECRET in scan_text, (
        f"STREAM BYPASS: secret in delta channel={channel!r} not scanned. "
        f"scan_text={scan_text!r}"
    )


@pytest.mark.parametrize("channel", SECONDARY_CHANNELS)
def test_stream_scan_sees_secret_in_late_choice(channel):
    """n>1 streaming: a secret in choices[1].delta must still be scanned."""
    sse = _new_secure_stream()
    scan_text = sse._extract_content_delta(_delta_chunk(channel, n_choices=3))
    assert SECRET in scan_text, (
        f"STREAM BYPASS: secret in late-choice delta channel={channel!r} not scanned."
    )


@pytest.mark.parametrize("channel", SECONDARY_CHANNELS)
def test_stream_rebuild_blanks_secondary_channels(channel):
    """On redact, _rebuild_sse_content writes the redacted content to choices[0]
    and must BLANK every secondary channel across every choice — otherwise a
    redacted SSE frame still streams the secret in a tool-call/reasoning channel."""
    sse = _new_secure_stream()
    raw = f"data: {json.dumps(_delta_chunk(channel, n_choices=2))}\n\n"
    rebuilt = sse._rebuild_sse_content(raw, "[REDACTED]")
    assert SECRET not in rebuilt, (
        f"STREAM BYPASS: redacted SSE frame still ships the secret in "
        f"channel={channel!r}. frame={rebuilt[:400]}"
    )


# ──────────────────────────────────────────────────────────────────────────────
# 4. End-to-end through the REAL OutputGuard: a credential in a tool-call (the
#    classic "verbose model" exfil shape) must produce a non-allow verdict on the
#    SAME scan input every path uses, for every model.
# ──────────────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
@pytest.mark.parametrize("model", MODEL_IDS)
async def test_real_guard_flags_credential_in_toolcall_any_model(model):
    """Wire the model-agnostic scan input into the REAL OutputGuard. A live
    credential hidden in a tool-call argument must NOT yield action='allow' for
    ANY model — proving no model can pass a tool-call secret through the guard."""
    from output_guard import OutputGuard
    from scanner import InputScanner

    guard = OutputGuard(
        scanner=InputScanner(thread_pool_size=2),
        config={"output_credential_enabled": True,
                "output_credential_action": "redact",
                "output_pii_enabled": True},
    )
    scan_text = gm._extract_scannable_output_text(
        _completion(model, secret_channel="tool_calls"))
    verdict = await guard.inspect(scan_text, context_chunks=[])
    assert verdict.action != "allow", (
        f"BYPASS: real guard ALLOWED a credential hidden in a tool-call for "
        f"model={model!r} (verdict={verdict.action}/{verdict.threat_type}). A "
        f"verbose model could exfiltrate via tool args unscanned."
    )
