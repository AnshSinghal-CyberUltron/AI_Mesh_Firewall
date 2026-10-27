"""JSON-RPC 2.0 message shape and rendering for the MCP surface (GW17, task 4.1).

Pure. Parses one request body into a message or a protocol error, and renders a
result or an error. Every refusal is ``{"jsonrpc": "2.0", "id": <id>, "error": {code,
message, data}}`` where ``data`` carries only the v2 value-code and the request id.
The message is the declared ``error_frame_for`` text, never matched content or an
exception string.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from gateway_v2.edge import errors
from gateway_v2.edge.wire.sse import error_frame_for

__all__ = (
    "MCP_INVALID_PARAMS",
    "MCP_INVALID_REQUEST",
    "MCP_METHOD_NOT_FOUND",
    "MCP_PARSE_ERROR",
    "JsonRpcId",
    "JsonRpcMessage",
    "ProtocolError",
    "code_for",
    "error",
    "parse_message",
    "result",
)

MCP_PARSE_ERROR = "mcp_parse_error"
MCP_INVALID_REQUEST = "mcp_invalid_request"
MCP_METHOD_NOT_FOUND = "mcp_method_not_found"
MCP_INVALID_PARAMS = "mcp_invalid_params"

#: Gateway refusals that are not JSON-RPC protocol errors use this code.
_SERVER_ERROR = -32000
_OK_STATUS = 200

#: JSON-RPC protocol codes. HTTP 400 for a body that is not a request; HTTP 200 for a
#: well-formed request the method table rejects, as JSON-RPC servers answer it.
_PROTOCOL: Mapping[str, tuple[int, int]] = MappingProxyType(
    {
        MCP_PARSE_ERROR: (-32700, 400),
        MCP_INVALID_REQUEST: (-32600, 400),
        MCP_METHOD_NOT_FOUND: (-32601, _OK_STATUS),
        MCP_INVALID_PARAMS: (-32602, _OK_STATUS),
    }
)

JsonRpcId = str | int | None


@dataclass(frozen=True, slots=True)
class JsonRpcMessage:
    """One parsed message. ``method`` is ``None`` for a reply (``result`` / ``error``)."""

    id: JsonRpcId
    method: str | None
    params: object
    is_notification: bool
    raw: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class ProtocolError:
    """A body that is not one valid JSON-RPC 2.0 message. ``id`` is echoed when known."""

    code: str
    id: JsonRpcId = None


def code_for(value_code: str) -> tuple[int, int]:
    """``(jsonrpc_code, http_status)`` for a value-code, from one table.

    Protocol codes come from ``_PROTOCOL``. Every other code is a gateway refusal
    (-32000) with the HTTP status the Error_Envelope already gives it.
    """
    protocol = _PROTOCOL.get(value_code)
    if protocol is not None:
        return protocol
    return _SERVER_ERROR, errors.render_http(value_code).status


def parse_message(body: bytes) -> JsonRpcMessage | ProtocolError:
    """Parse one JSON-RPC 2.0 message. A batch list is an invalid request."""
    try:
        decoded = json.loads(body)
    except (UnicodeDecodeError, ValueError):
        return ProtocolError(MCP_PARSE_ERROR)
    if not isinstance(decoded, dict):
        return ProtocolError(MCP_INVALID_REQUEST)
    has_id = "id" in decoded
    raw_id = decoded.get("id")
    if has_id and not _valid_id(raw_id):
        return ProtocolError(MCP_INVALID_REQUEST)
    msg_id: JsonRpcId = raw_id if isinstance(raw_id, (str, int)) else None
    if decoded.get("jsonrpc") != "2.0":
        return ProtocolError(MCP_INVALID_REQUEST, msg_id)
    method = decoded.get("method")
    if method is None:
        if ("result" in decoded) == ("error" in decoded) or not has_id:
            return ProtocolError(MCP_INVALID_REQUEST, msg_id)
        return JsonRpcMessage(msg_id, None, None, False, decoded)
    if not isinstance(method, str) or not method:
        return ProtocolError(MCP_INVALID_REQUEST, msg_id)
    params = decoded.get("params")
    if params is not None and not isinstance(params, (dict, list)):
        return ProtocolError(MCP_INVALID_PARAMS, msg_id)
    return JsonRpcMessage(msg_id, method, params, not has_id, decoded)


def _valid_id(value: object) -> bool:
    if value is None or isinstance(value, str):
        return True
    return isinstance(value, int) and not isinstance(value, bool)


def result(msg_id: JsonRpcId, value: object, extra: Mapping[str, object] | None = None) -> bytes:
    """A success reply. ``extra`` adds top-level siblings such as the console object."""
    body: dict[str, object] = {"jsonrpc": "2.0", "id": msg_id, "result": value}
    if extra:
        body.update({key: item for key, item in extra.items() if key not in body})
    return _dumps(body)


def error(
    msg_id: JsonRpcId,
    value_code: str,
    *,
    request_id: str | None = None,
    extra: Mapping[str, object] | None = None,
) -> tuple[int, bytes]:
    """``(http_status, body)`` for a refusal carrying ``value_code``."""
    jsonrpc_code, status = code_for(value_code)
    body: dict[str, object] = {
        "jsonrpc": "2.0",
        "id": msg_id,
        "error": {
            "code": jsonrpc_code,
            "message": error_frame_for(value_code).message,
            "data": {"code": value_code, "request_id": request_id},
        },
    }
    if extra:
        body.update({key: item for key, item in extra.items() if key not in body})
    return status, _dumps(body)


def _dumps(body: Mapping[str, object]) -> bytes:
    return json.dumps(dict(body), separators=(",", ":"), ensure_ascii=False).encode("utf-8")
