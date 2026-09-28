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
    TextChannel("output_content", "choices[].message.content", True),
    TextChannel("output_refusal", "choices[].message.refusal", True),
    TextChannel("output_reasoning", "choices[].message.reasoning_content", True),
    TextChannel("output_tool_arguments", "choices[].message.tool_calls[].function.arguments", True),
    TextChannel("logprobs", "choices[].logprobs.content[].token", True),
    TextChannel("structured_output", "choices[].message.content json_object", True),
)


def unscanned_channels() -> tuple[str, ...]:
    return tuple(channel.name for channel in TEXT_CHANNELS if not channel.scanned)
