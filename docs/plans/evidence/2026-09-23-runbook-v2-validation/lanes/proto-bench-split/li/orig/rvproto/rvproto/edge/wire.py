"""OpenAI chat request -> frozen ChatRequest (every scannable text field as a Segment)."""

from __future__ import annotations

from typing import Any

import orjson

from rvproto.domain.request import ChatRequest, ErrorSpec, Segment
from rvproto.edge.errors import bad_request


def _content_segments(i: int, role: str, content: Any, out: list[Segment]) -> ErrorSpec | None:
    if content is None:
        return None
    if isinstance(content, str):
        out.append(Segment(("messages", i, "content"), role, content))
        return None
    if isinstance(content, list):
        for k, part in enumerate(content):
            if not isinstance(part, dict) or "type" not in part:
                return bad_request("Invalid content part.", f"messages[{i}].content[{k}]")
            if part["type"] == "text":
                text = part.get("text")
                if not isinstance(text, str):
                    return bad_request("Text part needs a string.", f"messages[{i}].content[{k}].text")
                out.append(Segment(("messages", i, "content", k, "text"), role, text))
        return None
    return bad_request("Invalid message content.", f"messages[{i}].content")


def parse_chat(body: bytes) -> ChatRequest | ErrorSpec:
    try:
        doc = orjson.loads(body)
    except orjson.JSONDecodeError:
        return bad_request("We could not parse the JSON body of your request.", None, "invalid_json")
    if not isinstance(doc, dict):
        return bad_request("Request body must be a JSON object.")
    model = doc.get("model")
    if not isinstance(model, str) or not model:
        return bad_request("you must provide a model parameter", "model")
    messages = doc.get("messages")
    if not isinstance(messages, list) or not messages:
        return bad_request("messages must be a non-empty array", "messages")
    stream = doc.get("stream", False)
    if stream is not None and not isinstance(stream, bool):
        return bad_request("stream must be a boolean", "stream")
    segments: list[Segment] = []
    for i, m in enumerate(messages):
        if not isinstance(m, dict) or not isinstance(m.get("role"), str):
            return bad_request("Each message needs a role.", f"messages[{i}]")
        err = _content_segments(i, m["role"], m.get("content"), segments)
        if err is not None:
            return err
        for j, tc in enumerate(m.get("tool_calls") or []):
            args = (tc.get("function") or {}).get("arguments") if isinstance(tc, dict) else None
            if isinstance(args, str):
                segments.append(Segment(("messages", i, "tool_calls", j, "function", "arguments"),
                                        m["role"], args))
    max_tokens = doc.get("max_completion_tokens", doc.get("max_tokens"))
    if max_tokens is not None and (not isinstance(max_tokens, int) or max_tokens < 0):
        return bad_request("max_tokens must be a non-negative integer", "max_tokens")
    return ChatRequest(model, bool(stream), max_tokens, tuple(segments), body, doc)
