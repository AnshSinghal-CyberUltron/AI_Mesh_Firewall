"""Validation and folding of ``tools`` / ``tool_choice`` / ``response_format`` / tool history.

PURE wire logic: no I/O, no security decision. Everything the gateway forwards is either a closed
literal, a validated structural string (scanned BLOCK-only through the joined ``tool_schema_struct``
/ ``tool_name`` / ``tool_call_id`` runs and never redacted), or free text folded as a joined,
redactable, forwarded slot (``tool_description`` / ``tool_schema_text`` /
``response_format_schema``). The schema is classified keyword by keyword from the closed
:data:`SCHEMA_KEYWORDS` table; an unknown keyword is refused (``malformed_wrong_shape``) because
it is text the gateway could classify as neither structural nor scanned.

Depth, node count and string content are measured by an ITERATIVE walk (an explicit stack) BEFORE
the template is built, so a depth bomb cannot raise ``RecursionError``; the build pass that
follows recurses at most ``bounds.schema_depth`` levels.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping
from types import MappingProxyType

from gateway_v2.edge.wire.openai_codes import MALFORMED_WRONG_SHAPE, MalformedRequest
from gateway_v2.edge.wire.openai_template import (
    JOIN_SEPARATOR,
    Builder,
    SchemaObject,
    TemplateChoice,
    TemplateResponseFormat,
    TemplateTool,
    TemplateToolCall,
    TextSlot,
    ToolsTemplate,
)
from gateway_v2.runtime.resources_tools import ToolBounds

__all__ = (
    "SCHEMA_KEYWORDS",
    "TOOL_NAME_PATTERN",
    "canonical_arguments",
    "fold_tool_calls",
    "fold_tools",
    "validate_tool_messages",
)

TOOL_NAME_PATTERN = re.compile(r"[A-Za-z0-9_-]{1,64}")
_ID_PATTERN = re.compile(r"[A-Za-z0-9_.:-]{1,64}")
_REF_PATTERN = re.compile(r"#(/[^/]*)*")
_JSON_TYPES = frozenset({"string", "number", "integer", "boolean", "object", "array", "null"})
_CHOICE_LITERALS = frozenset({"none", "auto", "required"})
_FORMAT_TYPES = frozenset({"text", "json_object", "json_schema"})
_TOOL_KEYS = frozenset({"type", "function"})
_FUNCTION_KEYS = frozenset({"name", "description", "parameters", "strict"})
_CALL_KEYS = frozenset({"id", "type", "function"})
_CALL_FUNCTION_KEYS = frozenset({"name", "arguments"})
_JSON_SCHEMA_KEYS = frozenset({"name", "description", "schema", "strict"})

_STRUCT = "tool_schema_struct"
_TOOL_TEXT = "tool_schema_text"
_FORMAT_TEXT = "response_format_schema"

# Keyword classes (the right-hand side of SCHEMA_KEYWORDS).
_TYPE, _SUB, _SUBLIST, _NAMED, _REQUIRED, _DEP_REQUIRED = (
    "type", "sub", "sublist", "named", "required", "dep_required",
)
_STRUCT_STR, _FREE_STR, _REF, _FREE_JSON, _CLOSED_JSON = (
    "struct", "free", "ref", "free_json", "closed_json",
)
_NUMBER, _UINT, _BOOL = "number", "uint", "bool"

#: The closed JSON-Schema keyword table (design "Schema keyword table"). Anything not listed is
#: refused. ``strict`` lives at the function / json_schema level, not inside a schema.
SCHEMA_KEYWORDS: Mapping[str, str] = MappingProxyType(
    {
        "type": _TYPE,
        **dict.fromkeys(
            (
                "items", "additionalProperties", "additionalItems", "not", "if", "then", "else",
                "contains", "propertyNames", "unevaluatedProperties", "unevaluatedItems",
            ),
            _SUB,
        ),
        **dict.fromkeys(("anyOf", "oneOf", "allOf", "prefixItems"), _SUBLIST),
        **dict.fromkeys(
            ("properties", "$defs", "definitions", "patternProperties", "dependentSchemas"),
            _NAMED,
        ),
        "required": _REQUIRED,
        "dependentRequired": _DEP_REQUIRED,
        **dict.fromkeys(
            ("title", "pattern", "format", "$comment", "$anchor", "$id", "$schema"), _STRUCT_STR
        ),
        "description": _FREE_STR,
        "$ref": _REF,
        **dict.fromkeys(("default", "examples"), _FREE_JSON),
        **dict.fromkeys(("const", "enum"), _CLOSED_JSON),
        **dict.fromkeys(
            ("minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "multipleOf"), _NUMBER
        ),
        **dict.fromkeys(
            ("minLength", "maxLength", "minItems", "maxItems", "minProperties", "maxProperties"),
            _UINT,
        ),
        **dict.fromkeys(("uniqueItems", "nullable", "readOnly", "writeOnly", "deprecated"), _BOOL),
    }
)


def _refuse(param: str) -> MalformedRequest:
    return MalformedRequest(MALFORMED_WRONG_SHAPE, param=param)


def _check_text(value: str, param: str) -> None:
    """Refuse a lone surrogate (fails later at UTF-8 encode) or the join separator (D25)."""
    if JOIN_SEPARATOR in value:
        raise _refuse(param)
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        raise _refuse(param) from None


def _measure(root: object, *, depth_limit: int, node_limit: int, param: str) -> None:
    """Iterative bound walk: depth, node count, JSON-only types, finite numbers, string content."""
    stack: list[tuple[object, int]] = [(root, 1)]
    nodes = 0
    while stack:
        node, depth = stack.pop()
        nodes += 1
        if depth > depth_limit or nodes > node_limit:
            raise _refuse(param)
        if isinstance(node, str):
            _check_text(node, param)
        elif isinstance(node, bool) or node is None:
            continue
        elif isinstance(node, int):
            continue
        elif isinstance(node, float):
            if not math.isfinite(node):
                raise _refuse(param)
        elif isinstance(node, list):
            stack.extend((child, depth + 1) for child in node)
        elif isinstance(node, dict):
            for key, child in node.items():
                if not isinstance(key, str):
                    raise _refuse(param)
                _check_text(key, param)
                nodes += 1
                stack.append((child, depth + 1))
        else:
            raise _refuse(param)


class _Schema:
    """One schema build: classifies strings into the builder's joined channels."""

    __slots__ = ("builder", "free_channel", "param", "refs")

    def __init__(self, builder: Builder, *, free_channel: str, param: str) -> None:
        self.builder = builder
        self.free_channel = free_channel
        self.param = param
        self.refs: list[str] = []

    def struct(self, text: str) -> str:
        self.builder.scan_only(_STRUCT, text)
        return text

    def free(self, text: str) -> TextSlot:
        return self.builder.joined_slot(self.free_channel, text)

    def schema(self, node: object) -> object:
        if isinstance(node, bool):
            return node
        if not isinstance(node, dict):
            raise _refuse(self.param)
        return SchemaObject(
            tuple((key, self._keyword(key, value)) for key, value in node.items())
        )

    def _keyword(self, key: str, value: object) -> object:
        kind = SCHEMA_KEYWORDS.get(key)
        if kind is None:
            raise _refuse(self.param)
        if kind == _TYPE:
            return self._type(value)
        if kind == _SUB:
            return self.schema(value)
        if kind == _SUBLIST:
            if not isinstance(value, list):
                raise _refuse(self.param)
            return tuple(self.schema(item) for item in value)
        if kind == _NAMED:
            if not isinstance(value, dict):
                raise _refuse(self.param)
            return SchemaObject(
                tuple((self.struct(name), self.schema(sub)) for name, sub in value.items())
            )
        if kind == _REQUIRED:
            return self._names(value)
        if kind == _DEP_REQUIRED:
            if not isinstance(value, dict):
                raise _refuse(self.param)
            return SchemaObject(
                tuple((self.struct(name), self._names(sub)) for name, sub in value.items())
            )
        if kind == _STRUCT_STR:
            if not isinstance(value, str):
                raise _refuse(self.param)
            return self.struct(value)
        if kind == _FREE_STR:
            if not isinstance(value, str):
                raise _refuse(self.param)
            return self.free(value)
        if kind == _REF:
            if not isinstance(value, str) or not _REF_PATTERN.fullmatch(value):
                raise _refuse(self.param)
            self.refs.append(value)
            return self.struct(value)
        if kind == _FREE_JSON:
            return self._free_json(value)
        if kind == _CLOSED_JSON:
            return self._closed_json(value)
        return self._scalar(kind, value)

    def _type(self, value: object) -> object:
        names = value if isinstance(value, list) else [value]
        if not names or not all(isinstance(n, str) and n in _JSON_TYPES for n in names):
            raise _refuse(self.param)
        return tuple(names) if isinstance(value, list) else value

    def _names(self, value: object) -> tuple[str, ...]:
        if not isinstance(value, list) or not all(isinstance(n, str) for n in value):
            raise _refuse(self.param)
        return tuple(self.struct(name) for name in value)

    def _scalar(self, kind: str, value: object) -> object:
        if kind == _BOOL:
            ok = isinstance(value, bool)
        elif kind == _UINT:
            ok = isinstance(value, int) and not isinstance(value, bool) and value >= 0
        else:
            ok = isinstance(value, (int, float)) and not isinstance(value, bool)
        if not ok:
            raise _refuse(self.param)
        return value

    def _free_json(self, value: object) -> object:
        if isinstance(value, str):
            return self.free(value)
        if isinstance(value, list):
            return tuple(self._free_json(item) for item in value)
        if isinstance(value, dict):
            return SchemaObject(
                tuple((self.free(key), self._free_json(sub)) for key, sub in value.items())
            )
        return value

    def _closed_json(self, value: object) -> object:
        if isinstance(value, str):
            return self.struct(value)
        if isinstance(value, list):
            return tuple(self._closed_json(item) for item in value)
        if isinstance(value, dict):
            return SchemaObject(
                tuple((self.struct(key), self._closed_json(sub)) for key, sub in value.items())
            )
        return value


def _resolve_pointer(root: object, pointer: str) -> bool:
    """Whether the local ``$ref`` pointer resolves inside ``root`` (RFC 6901)."""
    node = root
    for raw in pointer[1:].split("/")[1:] if pointer != "#" else []:
        token = raw.replace("~1", "/").replace("~0", "~")
        if isinstance(node, dict) and token in node:
            node = node[token]
        elif isinstance(node, list) and token.isdigit() and int(token) < len(node):
            node = node[int(token)]
        else:
            return False
    return True


def _build_schema(
    builder: Builder, raw: object, bounds: ToolBounds, *, free_channel: str, param: str
) -> object:
    _measure(raw, depth_limit=bounds.schema_depth, node_limit=bounds.schema_nodes, param=param)
    ctx = _Schema(builder, free_channel=free_channel, param=param)
    node = ctx.schema(raw)
    for ref in ctx.refs:
        if not _resolve_pointer(raw, ref):
            raise _refuse(param)
    return node


def _name(value: object, param: str) -> str:
    if not isinstance(value, str) or not TOOL_NAME_PATTERN.fullmatch(value):
        raise _refuse(param)
    return value


def _optional_bool(value: object, param: str) -> bool | None:
    if value is None:
        return None
    if not isinstance(value, bool):
        raise _refuse(param)
    return value


def _fold_tool(builder: Builder, entry: object, bounds: ToolBounds) -> TemplateTool:
    well_formed = isinstance(entry, dict) and set(entry) <= _TOOL_KEYS
    if not isinstance(entry, dict) or not well_formed or entry.get("type") != "function":
        raise _refuse("tools")
    function = entry.get("function")
    if not isinstance(function, dict) or not set(function) <= _FUNCTION_KEYS:
        raise _refuse("tools")
    name = _name(function.get("name"), "tools")
    builder.scan_only("tool_name", name)
    description = function.get("description")
    description_slot: TextSlot | None = None
    if description is not None:
        if not isinstance(description, str):
            raise _refuse("tools")
        _check_text(description, "tools")
        description_slot = builder.joined_slot("tool_description", description)
    parameters = function.get("parameters")
    node: object = None
    if parameters is not None:
        if not isinstance(parameters, dict):
            raise _refuse("tools")
        node = _build_schema(builder, parameters, bounds, free_channel=_TOOL_TEXT, param="tools")
    return TemplateTool(
        name=name,
        description=description_slot,
        parameters=node,
        strict=_optional_bool(function.get("strict"), "tools"),
    )


def _fold_choice(builder: Builder, value: object, declared: frozenset[str]) -> TemplateChoice:
    if isinstance(value, str) and value in _CHOICE_LITERALS:
        return TemplateChoice(literal=value, name=None)
    if isinstance(value, dict) and set(value) <= _TOOL_KEYS and value.get("type") == "function":
        function = value.get("function")
        if isinstance(function, dict) and set(function) == {"name"}:
            name = _name(function["name"], "tool_choice")
            if name not in declared:
                raise _refuse("tool_choice")
            builder.scan_only("tool_name", name)
            return TemplateChoice(literal=None, name=name)
    raise _refuse("tool_choice")


def _fold_response_format(
    builder: Builder, value: object, bounds: ToolBounds
) -> TemplateResponseFormat:
    if not isinstance(value, dict) or value.get("type") not in _FORMAT_TYPES:
        raise _refuse("response_format")
    kind = str(value["type"])
    if kind != "json_schema":
        if set(value) != {"type"}:
            raise _refuse("response_format")
        return TemplateResponseFormat(type=kind)
    spec = value.get("json_schema")
    if set(value) != {"type", "json_schema"} or not isinstance(spec, dict):
        raise _refuse("response_format")
    if not set(spec) <= _JSON_SCHEMA_KEYS:
        raise _refuse("response_format")
    name = _name(spec.get("name"), "response_format")
    description = spec.get("description")
    description_slot: TextSlot | None = None
    if description is not None:
        if not isinstance(description, str):
            raise _refuse("response_format")
        _check_text(description, "response_format")
        description_slot = builder.joined_slot(_FORMAT_TEXT, description)
    schema = spec.get("schema")
    node: object = None
    if schema is not None:
        if not isinstance(schema, dict):
            raise _refuse("response_format")
        node = _build_schema(
            builder, schema, bounds, free_channel=_FORMAT_TEXT, param="response_format"
        )
    builder.scan_only("tool_name", name)
    return TemplateResponseFormat(
        type=kind,
        name=name,
        description=description_slot,
        schema=node,
        strict=_optional_bool(spec.get("strict"), "response_format"),
    )


def fold_tools(
    builder: Builder, parsed: Mapping[str, object], bounds: ToolBounds
) -> tuple[ToolsTemplate, frozenset[str] | None]:
    """Validate and fold the request-level tool fields; return the template + declared names.

    ``None`` for the declared set means the request declared no ``tools`` (the name tie is then
    skipped, R3.4). A ``null`` field is treated as absent.
    """
    raw_tools = parsed.get("tools")
    tools: list[TemplateTool] = []
    declared: frozenset[str] | None = None
    if raw_tools is not None:
        if not isinstance(raw_tools, list) or len(raw_tools) > bounds.max_tools:
            raise _refuse("tools")
        tools = [_fold_tool(builder, entry, bounds) for entry in raw_tools]
        names = [tool.name for tool in tools]
        if len(set(names)) != len(names):
            raise _refuse("tools")
        declared = frozenset(names)
    choice: TemplateChoice | None = None
    if parsed.get("tool_choice") is not None:
        choice = _fold_choice(builder, parsed["tool_choice"], declared or frozenset())
    parallel = parsed.get("parallel_tool_calls")
    if parallel is not None and not isinstance(parallel, bool):
        raise _refuse("parallel_tool_calls")
    response_format: TemplateResponseFormat | None = None
    if parsed.get("response_format") is not None:
        response_format = _fold_response_format(builder, parsed["response_format"], bounds)
    return (
        ToolsTemplate(
            tools=tuple(tools),
            tool_choice=choice,
            parallel_tool_calls=parallel,
            response_format=response_format,
        ),
        declared,
    )


def _refuse_constant(_token: str) -> object:
    raise ValueError("non-finite JSON constant")


def canonical_arguments(text: str, bounds: ToolBounds) -> str:
    """The canonical (decoded, compact, ``ensure_ascii=False``) form of a call's arguments (D4a).

    Empty / whitespace-only arguments become ``{}`` (known diff). Invalid JSON, NaN / Infinity,
    a float that overflows, a lone surrogate, the join separator, or a depth / size / node bound
    breach raise ``malformed_wrong_shape`` (``param: messages``).
    """
    if len(text) > bounds.arguments_bytes:
        raise _refuse("messages")
    if not text.strip():
        return "{}"
    try:
        value = json.loads(text, parse_constant=_refuse_constant)
    except (ValueError, RecursionError):
        raise _refuse("messages") from None
    _measure(
        value, depth_limit=bounds.schema_depth, node_limit=bounds.schema_nodes, param="messages"
    )
    try:
        return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    except (ValueError, RecursionError):
        raise _refuse("messages") from None


def validate_tool_messages(
    messages: object, declared: frozenset[str] | None, bounds: ToolBounds
) -> None:
    """Tie every tool turn to a declaration, in message order (D4).

    A ``tool`` message's ``tool_call_id`` must equal an ``id`` from an EARLIER assistant message;
    ids are unique within the request; an assistant call ``name`` must be declared when ``tools``
    is present. Shape violations raise ``malformed_wrong_shape`` (``param: messages``).
    """
    if not isinstance(messages, list):
        return
    seen: set[str] = set()
    for message in messages:
        if not isinstance(message, dict):
            continue
        role = message.get("role")
        calls = message.get("tool_calls")
        if role == "assistant" and calls is not None:
            _check_calls(calls, declared, seen, bounds)
        elif calls is not None:
            raise _refuse("messages")
        if role == "tool":
            call_id = message.get("tool_call_id")
            if not isinstance(call_id, str) or call_id not in seen:
                raise _refuse("messages")
        elif message.get("tool_call_id") is not None:
            raise _refuse("messages")


def _check_calls(
    calls: object, declared: frozenset[str] | None, seen: set[str], bounds: ToolBounds
) -> None:
    if not isinstance(calls, list) or len(calls) > bounds.max_tool_calls:
        raise _refuse("messages")
    for call in calls:
        if not isinstance(call, dict) or not set(call) <= _CALL_KEYS:
            raise _refuse("messages")
        call_id = call.get("id")
        function = call.get("function")
        if (
            not isinstance(call_id, str)
            or not _ID_PATTERN.fullmatch(call_id)
            or call_id in seen
            or call.get("type", "function") != "function"
            or not isinstance(function, dict)
            or not set(function) <= _CALL_FUNCTION_KEYS
            or not isinstance(function.get("arguments"), str)
        ):
            raise _refuse("messages")
        name = _name(function.get("name"), "messages")
        if declared is not None and name not in declared:
            raise _refuse("messages")
        seen.add(call_id)


def fold_tool_calls(
    builder: Builder, message: Mapping[str, object], bounds: ToolBounds
) -> tuple[TemplateToolCall, ...]:
    """Fold one assistant message's ``tool_calls`` (already validated by the tie pass)."""
    calls = message.get("tool_calls")
    if not isinstance(calls, list):
        return ()
    folded: list[TemplateToolCall] = []
    for call in calls:
        function = call["function"]
        raw = function["arguments"]
        canonical = canonical_arguments(raw, bounds)
        builder.scan_only("tool_call_id", call["id"])
        builder.scan_only("tool_name", function["name"])
        builder.scan_only("tool_arguments_raw", raw)
        slot = builder.joined_slot("tool_arguments", canonical)
        folded.append(TemplateToolCall(id=call["id"], name=function["name"], arguments=slot))
    return tuple(folded)
