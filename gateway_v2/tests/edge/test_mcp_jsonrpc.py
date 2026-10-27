# Validates: Requirements 2.1, 2.2, 2.7, 2.8, 2.9, 8.1, 8.2, 8.3
"""JSON-RPC 2.0 parse and render for the MCP surface."""

from __future__ import annotations

import json

import pytest

from gateway_v2.edge.wire.jsonrpc import (
    MCP_INVALID_PARAMS,
    MCP_INVALID_REQUEST,
    MCP_METHOD_NOT_FOUND,
    MCP_PARSE_ERROR,
    JsonRpcMessage,
    ProtocolError,
    code_for,
    error,
    parse_message,
    result,
)


@pytest.mark.parametrize(
    ("body", "code"),
    (
        (b"{not json", MCP_PARSE_ERROR),
        (b"\xff\xfe", MCP_PARSE_ERROR),
        (b"[]", MCP_INVALID_REQUEST),
        (b'[{"jsonrpc":"2.0","id":1,"method":"ping"}]', MCP_INVALID_REQUEST),
        (b'"text"', MCP_INVALID_REQUEST),
        (b'{"id":1,"method":"ping"}', MCP_INVALID_REQUEST),
        (b'{"jsonrpc":"1.0","id":1,"method":"ping"}', MCP_INVALID_REQUEST),
        (b'{"jsonrpc":"2.0","id":1,"method":""}', MCP_INVALID_REQUEST),
        (b'{"jsonrpc":"2.0","id":1,"method":5}', MCP_INVALID_REQUEST),
        (b'{"jsonrpc":"2.0","id":true,"method":"ping"}', MCP_INVALID_REQUEST),
        (b'{"jsonrpc":"2.0","id":1.5,"method":"ping"}', MCP_INVALID_REQUEST),
        (b'{"jsonrpc":"2.0","id":1,"method":"tools/call","params":"x"}', MCP_INVALID_PARAMS),
        (b'{"jsonrpc":"2.0","id":1,"result":{},"error":{}}', MCP_INVALID_REQUEST),
        (b'{"jsonrpc":"2.0","result":{}}', MCP_INVALID_REQUEST),
    ),
)
def test_protocol_errors(body: bytes, code: str) -> None:
    parsed = parse_message(body)
    assert isinstance(parsed, ProtocolError), parsed
    assert parsed.code == code


@pytest.mark.parametrize("msg_id", ("abc", 7, 0, None))
def test_request_id_type_is_kept(msg_id: object) -> None:
    body = json.dumps({"jsonrpc": "2.0", "id": msg_id, "method": "tools/list"}).encode()
    parsed = parse_message(body)
    assert isinstance(parsed, JsonRpcMessage)
    assert parsed.id == msg_id
    assert type(parsed.id) is type(msg_id)
    rendered = json.loads(result(parsed.id, {"tools": []}))
    assert rendered["id"] == msg_id and type(rendered["id"]) is type(msg_id)


def test_notification_and_reply_shapes() -> None:
    note = parse_message(b'{"jsonrpc":"2.0","method":"notifications/initialized"}')
    assert isinstance(note, JsonRpcMessage) and note.is_notification
    reply = parse_message(b'{"jsonrpc":"2.0","id":3,"result":{"content":[]}}')
    assert isinstance(reply, JsonRpcMessage) and reply.method is None


def test_protocol_code_table() -> None:
    assert code_for(MCP_PARSE_ERROR) == (-32700, 400)
    assert code_for(MCP_INVALID_REQUEST) == (-32600, 400)
    assert code_for(MCP_METHOD_NOT_FOUND) == (-32601, 200)
    assert code_for(MCP_INVALID_PARAMS) == (-32602, 200)


@pytest.mark.parametrize(
    ("value_code", "status"),
    (("input_blocked", 400), ("output_blocked", 403), ("plan_unavailable", 503)),
)
def test_gateway_refusals_keep_the_envelope_status(value_code: str, status: int) -> None:
    http_status, body = error(9, value_code, request_id="zs-x")
    assert http_status == status
    decoded = json.loads(body)
    assert decoded["jsonrpc"] == "2.0" and decoded["id"] == 9
    assert decoded["error"]["code"] == -32000
    assert decoded["error"]["data"] == {"code": value_code, "request_id": "zs-x"}
    assert set(decoded["error"]) == {"code", "message", "data"}


def test_error_message_never_carries_extra_text() -> None:
    _, body = error(1, "input_blocked", request_id=None)
    message = json.loads(body)["error"]["message"]
    assert message == "The request was blocked by an input content policy."
