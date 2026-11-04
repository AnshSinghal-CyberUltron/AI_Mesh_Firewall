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
    # GW34 request-side tool calling. ``tool_arguments`` is the CANONICAL (decoded, re-serialised)
    # arguments that are forwarded; ``tool_arguments_raw`` is the raw string, scan-only (D4a).
    TextChannel("tool_arguments_raw", "messages[].tool_calls[].function.arguments raw", True),
    # Names and ids are scanned but BLOCK-only (never redacted): a redacted name or id would
    # break the declared-set / id ties. ``tool_schema_struct`` is the joined structural-string run
    # of a schema (property names, ``$defs`` keys, ``required``, ``enum`` members, ...).
    TextChannel("tool_name", "tools[].function.name", True),
    TextChannel("tool_call_id", "messages[].tool_calls[].id", True),
    TextChannel("tool_schema_struct", "tools[].function.parameters structural", True),
    TextChannel("tool_description", "tools[].function.description", True),
    TextChannel("tool_schema_text", "tools[].function.parameters", True),
    TextChannel("response_format_schema", "response_format.json_schema", True),
    TextChannel("function_arguments", "messages[].function_call.arguments", True),
    # GW36 S1: the legacy completions ``prompt`` and the chat ``stop`` strings are request text
    # forwarded to the provider, so they are folded and scanned like ``messages[]``.
    TextChannel("completion_prompt", "prompt", True),
    TextChannel("stop_sequence", "stop", True),
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
    TextChannel("output_tool_id", "choices[].message.tool_calls[].id", True),
    TextChannel("output_tool_name", "choices[].message.tool_calls[].function.name", True),
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


#: A meta channel is not text: its value is checked against a closed set by the decoder before it
#: enters the pipeline, so it is passed through egress unscanned and unheld (and
#: ``unscanned_channels()`` stays empty because it is not a ``TextChannel``).
META_CHANNELS: frozenset[str] = frozenset({"finish_reason"})

#: Input channels whose findings are decided alone and never redacted: any non-ALLOW decision on
#: an overlapping span blocks the request (D23).
BLOCK_ONLY_CHANNELS: frozenset[str] = frozenset({"tool_name", "tool_call_id", "tool_schema_struct"})
