"""Text-bearing SDK channels. A field with no scanner mapping fails the gate (C21)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class TextChannel:
    name: str
    sdk_path: str
    scanned: bool


# Every text-bearing request/response field the pinned contract must scan.
TEXT_CHANNELS: tuple[TextChannel, ...] = (
    TextChannel("message_content", "messages[].content", True),
    TextChannel("content_part_text", "messages[].content[].text", True),
    TextChannel("tool_arguments", "messages[].tool_calls[].function.arguments", True),
    TextChannel("function_arguments", "messages[].function_call.arguments", True),
    # GW16 misc INPUT channels (C21): embeddings/moderations ``input`` field. Their
    # ``sdk_path`` is a DISJOINT scope -- it starts with neither ``messages[]`` (the
    # chat-input scope GW15's ``_input_channel_names()`` filters on) nor ``choices[]``
    # (the output scope). ``scanned=True`` keeps ``unscanned_channels()`` empty (R3.3).
    TextChannel("embeddings_input", "input", True),
    TextChannel("moderations_input", "input[]", True),
    TextChannel("output_content", "choices[].message.content", True),
    TextChannel("output_refusal", "choices[].message.refusal", True),
    TextChannel("output_reasoning", "choices[].message.reasoning_content", True),
    TextChannel("output_tool_arguments", "choices[].message.tool_calls[].function.arguments", True),
    TextChannel("logprobs", "choices[].logprobs.content[].token", True),
    TextChannel("structured_output", "choices[].message.content json_object", True),
    # GW17 MCP channels. The ``mcp:`` prefix keeps them out of the chat (``messages[]``),
    # output (``choices[]``) and misc scopes; ``edge/wire/mcp.py`` owns their fold check.
    TextChannel("mcp_arguments", "mcp:params.arguments", True),
    TextChannel("mcp_completion", "mcp:params.argument.value", True),
    TextChannel("mcp_result", "mcp:result", True),
    TextChannel("mcp_error", "mcp:error", True),
)


def unscanned_channels() -> tuple[str, ...]:
    return tuple(channel.name for channel in TEXT_CHANNELS if not channel.scanned)
