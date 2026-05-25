"""
Normalize OpenAI chat completion request bodies from various IDEs and clients
(Cursor, etc.) into a single OpenAI-compatible shape. Use before policy check
and before forwarding to any OpenAI-compatible upstream.
"""

# Map non-OpenAI content block types to OpenAI-supported type.
# Add entries as new IDEs are discovered (e.g. "user_input" -> "text").
CONTENT_TYPE_ALIASES = {
    "input_text": "text",
}

# Top-level keys allowed when forwarding to OpenAI-compatible APIs.
# Includes GPT-5.2 / reasoning model params (reasoning, reasoning_effort, logprobs, etc.)
OPENAI_TOP_LEVEL_KEYS = frozenset({
    "model", "messages", "stream", "temperature", "top_p", "max_tokens",
    "stop", "presence_penalty", "frequency_penalty", "tools", "tool_choice",
    "response_format", "seed", "n", "user",
    "reasoning", "reasoning_effort", "logprobs", "max_output_tokens", "text",
})


def _normalize_content_parts(content):
    if not isinstance(content, list):
        return content
    out = []
    for part in content:
        if not isinstance(part, dict):
            out.append(part)
            continue
        part = dict(part)
        t = part.get("type")
        if t in CONTENT_TYPE_ALIASES:
            part["type"] = CONTENT_TYPE_ALIASES[t]
        out.append(part)
    return out


def _normalize_messages(messages):
    if not messages:
        return messages
    out = []
    for m in messages:
        msg = dict(m)
        if "content" in msg:
            msg["content"] = _normalize_content_parts(msg["content"])
        out.append(msg)
    return out


def normalize_openai_chat_request(body, strip_unknown_top_level=True):
    """
    Normalize a chat request body to OpenAI-compatible form.
    - Use 'input' as messages when 'messages' is missing.
    - Normalize message content parts (e.g. input_text -> text).
    - If strip_unknown_top_level is True, only keep known top-level keys.
    """
    if not body or not isinstance(body, dict):
        return body or {}
    body = dict(body)
    messages = body.get("messages") or body.get("input")
    if messages is not None:
        body["messages"] = _normalize_messages(messages)
    if strip_unknown_top_level:
        body = {k: v for k, v in body.items() if k in OPENAI_TOP_LEVEL_KEYS}
    return body
