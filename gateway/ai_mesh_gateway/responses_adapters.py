"""OpenAI **Responses API** <-> Chat Completions format adapters.

ZeroShield exposes ``POST /v1/responses`` so the stock OpenAI SDK
(``client.responses.create``) works with a base-URL + key swap and NO custom
SDK. To guarantee the firewall is *inherited not forked*, the Responses handler
translates a Responses request into the proven chat-completions request, runs it
through the unchanged ``proxy_chat`` enforcement pipeline, then translates the
chat result back into a Responses object. Every function here is PURE (no
imports from ``main``) so it is unit-testable and free of circular imports.

References: OpenAI Responses API (input items, typed output items, usage with
input_tokens/output_tokens, typed streaming events response.*).
"""
from __future__ import annotations

import json
import secrets
import time
from typing import Any

# ── ID generation (OpenAI-shaped prefixes) ──────────────────────────────────
_ID_PREFIXES = {
    "response": "resp",
    "message": "msg",
    "function_call": "fc",
    "run": "run",
    "thread": "thread",
    "vector_store": "vs",
    "file": "file",
}


def generate_openai_id(object_type: str) -> str:
    """`resp_<48 hex>` style id. Internal ``zs-`` correlation id stays separate."""
    prefix = _ID_PREFIXES.get(object_type, object_type)
    return f"{prefix}_{secrets.token_hex(24)}"


# ── OpenAI error envelope ────────────────────────────────────────────────────
# ZeroShield internal codes -> OpenAI error `type` strings so the stock SDK
# raises the correct exception class (PermissionDeniedError, RateLimitError, ...).
_ZS_CODE_TO_OPENAI_TYPE = {
    # D-a: CONTENT-category blocks now surface error.code="content_filter" and
    # must resolve to invalid_request_error (HTTP 400 / BadRequestError) so the
    # stock SDK + LiteLLM/LangChain key on the content-filter signal.
    "content_filter": "invalid_request_error",
    "content_blocked": "permission_error",
    "blocked": "permission_error",
    "forbidden": "permission_error",
    "model_not_allowed": "permission_error",
    "rate_limited": "rate_limit_error",
    "rate_limit_exceeded": "rate_limit_error",
    "unauthorized": "authentication_error",
    "authentication_error": "authentication_error",
    "model_not_configured": "invalid_request_error",
    "invalid_request": "invalid_request_error",
    "invalid_sampling_param": "invalid_request_error",
}
_STATUS_TO_OPENAI_TYPE = {
    400: "invalid_request_error",
    401: "authentication_error",
    403: "permission_error",
    404: "invalid_request_error",
    422: "invalid_request_error",
    429: "rate_limit_error",
    500: "server_error",
    502: "server_error",
    503: "service_unavailable_error",
    504: "service_unavailable_error",
}


def build_openai_error(status: int, message: str, *, error_type: str | None = None,
                       code: str | None = None, param: str | None = None,
                       extra_top_level: dict | None = None) -> dict:
    """Nested OpenAI error object ``{error:{message,type,param,code}}`` that the
    stock SDK parses into the right exception class. ZeroShield diagnostic fields
    (request_id, category, pipeline_trace, ...) ride at the TOP level, not inside
    ``error`` (so they never confuse the SDK)."""
    etype = error_type or _ZS_CODE_TO_OPENAI_TYPE.get(str(code or ""), "") or \
        _STATUS_TO_OPENAI_TYPE.get(status, "invalid_request_error")
    body: dict[str, Any] = {
        "error": {"message": message, "type": etype, "param": param, "code": code}
    }
    if extra_top_level:
        for k, v in extra_top_level.items():
            if k != "error":
                body[k] = v
    return body


def coerce_chat_error_to_openai(status: int, chat_error_body: dict) -> dict:
    """A chat-path error body (mix of flat {error,message,code} and ZS fields)
    -> the nested OpenAI envelope, preserving ZS diagnostics at top level."""
    if not isinstance(chat_error_body, dict):
        return build_openai_error(status, "The request failed.")
    err = chat_error_body.get("error")
    if isinstance(err, dict) and "message" in err:
        # already nested-OpenAI; ensure a type is present
        if not err.get("type"):
            err["type"] = _ZS_CODE_TO_OPENAI_TYPE.get(str(err.get("code") or ""), "") or \
                _STATUS_TO_OPENAI_TYPE.get(status, "invalid_request_error")
        return chat_error_body
    message = (chat_error_body.get("message")
               or (err if isinstance(err, str) else None)
               or "The request failed.")
    code = chat_error_body.get("code") or (err if isinstance(err, str) else None)
    # Phase-4 (P2-Dx): propagate a flat-body ``param`` into error.param so the stock
    # SDK populates e.param on parameter-validation 400s (OpenAI parity).
    param = chat_error_body.get("param")
    diagnostics = {k: v for k, v in chat_error_body.items()
                   if k not in ("error", "message", "code", "param")}
    return build_openai_error(status, message, code=code, param=param, extra_top_level=diagnostics)


# ── Responses request -> Chat request ────────────────────────────────────────
# Responses params that map onto / pass through to a chat request.
_RESP_DIRECT_PASSTHROUGH = (
    "temperature", "top_p", "tools", "tool_choice", "parallel_tool_calls",
    "stop", "seed", "user", "metadata", "stream_options", "logprobs",
    # Phase-4 SEAM-B: chat params that were silently DROPPED on the responses->chat
    # translation (the direct chat path forwards them) — now survive so /v1/responses
    # has the same passthrough fidelity as /v1/chat/completions.
    "response_format", "frequency_penalty", "presence_penalty", "top_logprobs",
    "n", "logit_bias",
    # SEAM-B widen (2-layer fix; see OPENAI_TOP_LEVEL_KEYS): latency/billing tier,
    # output-modality selection, abuse/cache attribution, predicted-output, and the
    # cache-routing hint were all dropped by the narrow allowlist.
    "service_tier", "modalities", "safety_identifier", "prediction", "prompt_cache_key",
)

# Responses input-content part types this adapter knows how to translate into a
# chat content part. Anything else (input_file, input_audio, refusal, reasoning, ...)
# must NOT be silently dropped: _input_item_to_message carries it through, and
# proxy_responses returns a clear 400 (see SUPPORTED_INPUT_PART_TYPES usage there).
SUPPORTED_INPUT_PART_TYPES = frozenset({
    "input_text", "output_text", "text", "input_image", "image_url",
})


def find_unsupported_input_part(inp: Any) -> str | None:
    """Return the ``type`` of the first Responses input-content part this adapter
    cannot translate (e.g. ``input_file``, ``input_audio``), else ``None``. Used by
    proxy_responses to reject with a clean 400 instead of forwarding (or dropping) a
    part the chat pipeline does not understand."""
    if not isinstance(inp, list):
        return None
    for item in inp:
        if not isinstance(item, dict):
            continue
        content = item.get("content")
        if not isinstance(content, list):
            continue
        for c in content:
            if not isinstance(c, dict):
                continue
            ctype = c.get("type")
            if ctype is not None and ctype not in SUPPORTED_INPUT_PART_TYPES:
                return str(ctype)
    return None


def _input_item_to_message(item: Any) -> dict | None:
    """Map one Responses ``input`` item to a chat message. Handles: plain string,
    {role, content:str|list}, function_call / function_call_output tool items."""
    if isinstance(item, str):
        return {"role": "user", "content": item}
    if not isinstance(item, dict):
        return None
    itype = item.get("type")
    # tool result fed back by the SDK
    if itype == "function_call_output":
        return {"role": "tool",
                "tool_call_id": item.get("call_id") or item.get("id") or "",
                "content": _stringify(item.get("output"))}
    if itype == "function_call":
        # assistant emitting a tool call (rare on input; preserve for replay)
        return {"role": "assistant", "content": None,
                "tool_calls": [{"id": item.get("call_id") or item.get("id") or "",
                                "type": "function",
                                "function": {"name": item.get("name", ""),
                                             "arguments": _stringify(item.get("arguments"))}}]}
    # I-14: MCP / custom tool items are the MCP-flow equivalents of
    # function_call_output and must be treated the same way. They previously fell
    # through to the ``{"role": role, "content": ""}`` fallback at the bottom, so an
    # ``mcp_call`` collapsed to an EMPTY user message: HTTP 200, no diagnostic, the
    # payload silently gone — violating this module's own B12 no-silent-drop rule.
    # ``mcp_approval_request``/``mcp_approval_response``/``mcp_list_tools``/
    # ``custom_tool_call_output`` collapsed identically.
    #
    # Mapping them to ``role=tool`` (rather than dropping them) is deliberate: the
    # drop was fail-CLOSED for injection but lost real conversation state on replay.
    # role=tool content IS scanned — an injection here blocks exactly as it does in
    # function_call_output — so intent is preserved without opening a new vector.
    if itype in ("mcp_call", "mcp_approval_request", "mcp_approval_response",
                 "mcp_list_tools", "custom_tool_call_output"):
        _payload = item.get("output")
        if _payload is None:
            _payload = item.get("arguments")
        if _payload is None:
            # No explicit payload: carry the whole item minus structural noise so the
            # content is still visible to the scanner rather than vanishing.
            _payload = {k: v for k, v in item.items()
                        if k not in ("type", "id", "call_id", "status")}
        return {"role": "tool",
                "tool_call_id": item.get("call_id") or item.get("id") or "",
                "content": _stringify(_payload)}
    role = item.get("role") or "user"
    content = item.get("content")
    if isinstance(content, str):
        return {"role": role, "content": content}
    if isinstance(content, list):
        # collapse typed content parts -> OpenAI chat content parts (text + image)
        parts: list[dict] = []
        for c in content:
            if not isinstance(c, dict):
                continue
            ctype = c.get("type")
            if ctype in ("input_text", "output_text", "text") and isinstance(c.get("text"), str):
                parts.append({"type": "text", "text": c["text"]})
            elif ctype in ("input_image", "image_url"):
                url = c.get("image_url") or c.get("url")
                if isinstance(url, dict):
                    url = url.get("url")
                if url:
                    parts.append({"type": "image_url", "image_url": {"url": url}})
            else:
                # B12: an UNKNOWN content part (input_file, input_audio, refusal, ...)
                # must NOT vanish silently. Carry it verbatim so the data is visible;
                # proxy_responses rejects unsupported parts up front with a clean 400.
                parts.append(dict(c))
        if parts:
            # single text part -> string content (maximal SDK compatibility)
            if len(parts) == 1 and parts[0].get("type") == "text":
                return {"role": role, "content": parts[0]["text"]}
            return {"role": role, "content": parts}
    # bare text on the item
    if isinstance(item.get("text"), str):
        return {"role": role, "content": item["text"]}
    return {"role": role, "content": ""}


def _stringify(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, str):
        return v
    try:
        return json.dumps(v)
    except (TypeError, ValueError):
        return str(v)


def responses_to_chat(body: dict, prior_messages: list[dict] | None = None) -> dict:
    """Translate a Responses request into a chat-completions request. ``instructions``
    -> system message; ``input`` -> user/typed messages; ``max_output_tokens`` ->
    ``max_tokens``; ``previous_response_id`` history (resolved by the caller) is
    prepended via ``prior_messages``."""
    chat: dict[str, Any] = {"model": body.get("model")}
    messages: list[dict] = []
    instr = body.get("instructions")
    if isinstance(instr, str) and instr.strip():
        messages.append({"role": "system", "content": instr})
    if prior_messages:
        messages.extend(prior_messages)
    inp = body.get("input")
    if isinstance(inp, str):
        messages.append({"role": "user", "content": inp})
    elif isinstance(inp, list):
        for item in inp:
            m = _input_item_to_message(item)
            if m:
                messages.append(m)
    chat["messages"] = messages
    # token cap: Responses uses max_output_tokens -> chat max_tokens.
    if body.get("max_output_tokens") is not None:
        chat["max_tokens"] = body["max_output_tokens"]
    elif body.get("max_completion_tokens") is not None:
        # SEAM-A: forward max_completion_tokens VERBATIM. Reasoning models
        # (o1/o3/gpt-5) require this field and reject the deprecated max_tokens;
        # silently renaming it to max_tokens broke the responses path for them.
        chat["max_completion_tokens"] = body["max_completion_tokens"]
    for k in _RESP_DIRECT_PASSTHROUGH:
        if body.get(k) is not None:
            chat[k] = body[k]
    # SEAM-B: the NATIVE Responses structured-output key text:{format:{...}} (what
    # client.responses.parse() emits) has no chat equivalent — translate it to the
    # chat response_format so structured output is enforced, not silently dropped.
    # An explicit chat-style response_format (already passed through above) wins.
    text_obj = body.get("text")
    if isinstance(text_obj, dict) and isinstance(text_obj.get("format"), dict) \
            and "response_format" not in chat:
        chat["response_format"] = text_obj["format"]
    # truncation is Responses-native (not a chat param) but the inner pipeline
    # tolerates/forwards it; honor it so long-context auto-truncation isn't lost.
    if body.get("truncation") is not None:
        chat["truncation"] = body["truncation"]
    # ZeroShield gateway fields (extra_body from stock SDK) must survive the
    # adapter so MCP context, routing prefs, and agent_data reach proxy_chat.
    for k in ("mcp_context", "agent_data", "routing_preferences"):
        if body.get(k) is not None:
            chat[k] = body[k]
    if body.get("stream"):
        chat["stream"] = True
    # reasoning.effort -> chat reasoning_effort (best-effort; provider may ignore)
    reasoning = body.get("reasoning")
    if isinstance(reasoning, dict) and reasoning.get("effort"):
        chat["reasoning_effort"] = reasoning["effort"]
    return chat


# ── Chat completion -> Responses object ──────────────────────────────────────
def _finish_to_status(finish_reason: str | None) -> tuple[str, str | None]:
    """(response.status, incomplete_details.reason|None)."""
    if finish_reason == "length":
        return "incomplete", "max_output_tokens"
    if finish_reason == "content_filter":
        return "incomplete", "content_filter"
    return "completed", None


def chat_completion_to_responses(completion: dict, *, response_id: str, model: str,
                                 store: bool = False, metadata: dict | None = None,
                                 previous_response_id: str | None = None) -> dict:
    """Translate an OpenAI chat.completion into an OpenAI ``response`` object."""
    choice = (completion.get("choices") or [{}])[0]
    msg = choice.get("message") or {}
    finish = choice.get("finish_reason")
    status, incomplete_reason = _finish_to_status(finish)

    output: list[dict] = []
    text_content = msg.get("content")
    if isinstance(text_content, str) and text_content:
        output.append({
            "type": "message", "id": generate_openai_id("message"),
            "status": "completed", "role": "assistant",
            "content": [{"type": "output_text", "text": text_content, "annotations": []}],
        })
    for tc in (msg.get("tool_calls") or []):
        fn = (tc or {}).get("function") or {}
        output.append({
            "type": "function_call", "id": generate_openai_id("function_call"),
            "call_id": tc.get("id") or generate_openai_id("function_call"),
            "name": fn.get("name", ""), "arguments": fn.get("arguments", ""),
            "status": "completed",
        })

    usage_in = completion.get("usage") or {}
    usage = {
        "input_tokens": usage_in.get("prompt_tokens", 0),
        "output_tokens": usage_in.get("completion_tokens", 0),
        "total_tokens": usage_in.get("total_tokens", 0),
        "input_tokens_details": {"cached_tokens": (usage_in.get("prompt_tokens_details") or {}).get("cached_tokens", 0)},
        "output_tokens_details": {"reasoning_tokens": (usage_in.get("completion_tokens_details") or {}).get("reasoning_tokens", 0)},
    }

    resp: dict[str, Any] = {
        "id": response_id,
        "object": "response",
        "created_at": completion.get("created") or int(time.time()),
        "status": status,
        # MODEL-ID LEAK FIX: prefer the client-requested `model` over
        # completion["model"], which can be the raw upstream provider id
        # (e.g. "anthropic/claude-3-5-haiku"). Never echo the upstream id back.
        "model": model or completion.get("model"),
        "output": output,
        "output_text": text_content if isinstance(text_content, str) else "",
        "usage": usage,
        "metadata": metadata or {},
        "previous_response_id": previous_response_id,
        "parallel_tool_calls": True,
        "store": bool(store),
        "incomplete_details": ({"reason": incomplete_reason} if incomplete_reason else None),
        "error": None,
    }
    # carry the ZeroShield trace through (top-level extra field, SDK-tolerant)
    if isinstance(completion.get("zeroshield"), dict):
        resp["zeroshield"] = completion["zeroshield"]
    return resp


def extract_assistant_messages_for_replay(completion_or_response: dict) -> list[dict]:
    """Derive the assistant turn(s) to persist for ``previous_response_id`` replay,
    as chat messages. Stores REDACTED output (the firewall already redacted it)."""
    out: list[dict] = []
    # accept either a chat.completion or a responses object
    if completion_or_response.get("object") == "response":
        text = completion_or_response.get("output_text") or ""
        if text:
            out.append({"role": "assistant", "content": text})
    else:
        choice = (completion_or_response.get("choices") or [{}])[0]
        msg = choice.get("message") or {}
        if isinstance(msg.get("content"), str) and msg["content"]:
            out.append({"role": "assistant", "content": msg["content"]})
    return out
