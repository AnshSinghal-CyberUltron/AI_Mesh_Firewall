"""Render the tool fields of the upstream OpenAI request from the TEMPLATE only (GW34).

``tools``, ``tool_choice``, ``parallel_tool_calls``, ``response_format`` and the per-message
``tool_calls`` / ``tool_call_id`` are built from the validated template, never from the client
body: every string leaf is a verified slot delta (free text, filled by the caller), a validated
literal that the input scan returned ALLOW on (names, ids, structural strings), or a closed-set
literal (``type: "function"``, schema keywords, ``tool_choice`` literals). An object key that is
free text can collide after redaction; that is refused rather than merged.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from types import MappingProxyType

from gateway_v2.edge.wire.openai_template import (
    SchemaObject,
    TemplateMessage,
    TemplateResponseFormat,
    TemplateTool,
    TextSlot,
    ToolsTemplate,
)

__all__ = ("Unfillable", "render_message_extras", "render_tool_fields")

Fill = Callable[[TextSlot], str]


class Unfillable(Exception):
    """A slot cannot be filled. ``code`` is the refusal (``None``: the plain slot mismatch)."""

    def __init__(self, code: str | None = None) -> None:
        super().__init__(code)
        self.code = code


def _node(node: object, fill: Fill, code: str) -> object:
    if isinstance(node, TextSlot):
        return fill(node)
    if isinstance(node, tuple):
        return [_node(item, fill, code) for item in node]
    if isinstance(node, SchemaObject):
        out: dict[str, object] = {}
        for key, value in node.items:
            name = fill(key) if isinstance(key, TextSlot) else str(key)
            if name in out:
                raise Unfillable(code)
            out[name] = _node(value, fill, code)
        return out
    return node


def _tool(tool: TemplateTool, fill: Fill, code: str) -> object:
    function: dict[str, object] = {"name": tool.name}
    if tool.description is not None:
        function["description"] = fill(tool.description)
    if tool.parameters is not None:
        function["parameters"] = _node(tool.parameters, fill, code)
    if tool.strict is not None:
        function["strict"] = tool.strict
    return {"type": "function", "function": function}


def _response_format(fmt: TemplateResponseFormat, fill: Fill, code: str) -> object:
    if fmt.type != "json_schema":
        return {"type": fmt.type}
    spec: dict[str, object] = {"name": fmt.name}
    if fmt.description is not None:
        spec["description"] = fill(fmt.description)
    if fmt.schema is not None:
        spec["schema"] = _node(fmt.schema, fill, code)
    if fmt.strict is not None:
        spec["strict"] = fmt.strict
    return {"type": "json_schema", "json_schema": spec}


def render_tool_fields(
    tools: ToolsTemplate, fill: Fill, *, collision_code: str
) -> Mapping[str, object]:
    """The request-level tool fields as upstream body entries (empty when none were sent)."""
    body: dict[str, object] = {}
    if tools.tools:
        body["tools"] = [_tool(tool, fill, collision_code) for tool in tools.tools]
    choice = tools.tool_choice
    if choice is not None:
        body["tool_choice"] = (
            choice.literal
            if choice.literal is not None
            else {"type": "function", "function": {"name": choice.name}}
        )
    if tools.parallel_tool_calls is not None:
        body["parallel_tool_calls"] = tools.parallel_tool_calls
    if tools.response_format is not None:
        body["response_format"] = _response_format(tools.response_format, fill, collision_code)
    return MappingProxyType(body)


def render_message_extras(message: TemplateMessage, fill: Fill) -> Mapping[str, object]:
    """The assistant ``tool_calls`` / tool-message ``tool_call_id`` entries of one message."""
    extras: dict[str, object] = {}
    if message.tool_calls:
        extras["tool_calls"] = [
            {
                "id": call.id,
                "type": "function",
                "function": {"name": call.name, "arguments": fill(call.arguments)},
            }
            for call in message.tool_calls
        ]
    if message.tool_call_id is not None:
        extras["tool_call_id"] = message.tool_call_id
    return extras
