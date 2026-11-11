"""GW32: path grammar, Origin / protocol-version / session-header gates, SSE framing (P12)."""

from __future__ import annotations

import json
import random

import pytest

from gateway_v2.edge.mcp_paths import classify_gateway_path, parse_mcp_path
from gateway_v2.edge.mcp_session_gate import (
    check_origin,
    check_protocol_version,
    session_header,
)
from gateway_v2.edge.wire.mcp import prefers_sse
from gateway_v2.edge.wire.mcp_sse import decode_events, encode_message
from gateway_v2.edge.wire.sse import SSEDecoder


def _scope(**headers: str) -> dict[str, object]:
    return {"headers": [(k.encode(), v.encode()) for k, v in headers.items()]}


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("/v1/mcp/search", ("search", None)),
        ("/v1/mcp/search/", ("search", None)),
        ("/gateway/acme/mcp/search", ("search", "acme")),
        ("/gateway/acme/mcp/search/", ("search", "acme")),
        ("/v1/mcp/a%2Fb", None),
        ("/v1/mcp/a/b", None),
        ("/v1/mcp/", None),
        ("/v1/mcp/..", None),
        ("/v1/mcp/-bad", None),
        ("/v1/mcp/" + "a" * 65, None),
        ("/v1/mcp/a\x00b", None),
        ("/gateway/Acme/mcp/search", None),
        ("/gateway//mcp/search", None),
        ("/gateway/acme/mcp/search/tools", None),
        ("/gateway/acme/mcp/search/health", None),
        ("/gateway/acme/mcp/search/tools/call", None),
        ("/gateway/acme/mcp", None),
        ("/gateway/../mcp/x", None),
        ("/gateway/acme/mcp/..", None),
    ],
)
def test_parse_mcp_path(raw: str, expected: tuple[str, str | None] | None) -> None:
    parsed = parse_mcp_path(raw)
    assert (None if parsed is None else (parsed.server_slug, parsed.path_org)) == expected


def test_gateway_classification_serves_exactly_five_shapes() -> None:
    assert classify_gateway_path("/gateway/acme/mcp/s").kind == "mcp"
    assert classify_gateway_path("/gateway/acme/mcp/s/oauth/start").kind == "oauth_start"
    assert classify_gateway_path("/gateway/acme/mcp/s/oauth/status").kind == "oauth_status"
    assert classify_gateway_path("/gateway/oauth/callback").kind == "oauth_callback"
    for other in ("/gateway/acme/mcp/s/tools", "/gateway/acme/mcp/s/tools/call", "/gateway/x"):
        assert classify_gateway_path(other).kind == "miss"


def test_origin_rule() -> None:
    allowed = frozenset({"https://app.example.com"})
    assert check_origin(_scope(host="gw.example.com"), allowed)  # absent passes
    assert check_origin(_scope(host="gw.example.com", origin="https://gw.example.com"), allowed)
    assert check_origin(_scope(host="gw:8080", origin="http://gw:8080"), allowed)
    assert check_origin(_scope(host="gw.example.com", origin="https://app.example.com"), allowed)
    for bad in ("https://evil.example.com", "null", "not a url", "https://gw.example.com:99"):
        assert not check_origin(_scope(host="gw.example.com", origin=bad), allowed), bad


def test_protocol_version_and_session_header() -> None:
    assert check_protocol_version(_scope()) == "2025-03-26"
    assert check_protocol_version(_scope(**{"mcp-protocol-version": "2025-06-18"})) == "2025-06-18"
    assert check_protocol_version(_scope(**{"mcp-protocol-version": "1999-01-01"})) is None
    assert session_header(_scope()) is None
    assert session_header(_scope(**{"mcp-session-id": "abc123"})) == "abc123"
    assert session_header(_scope(**{"mcp-session-id": "x" * 129})) == ""
    assert session_header(_scope(**{"mcp-session-id": "a b"})) == ""


def test_accept_negotiation() -> None:
    assert prefers_sse("text/event-stream")
    assert prefers_sse("application/json, text/event-stream")
    assert not prefers_sse("application/json")
    assert not prefers_sse("*/*") and not prefers_sse(None) and not prefers_sse("")
    assert not prefers_sse("text/event-stream;q=0.5, application/json;q=0.9")
    assert prefers_sse("text/event-stream;q=0.9, application/json;q=0.5")
    assert not prefers_sse("text/event-stream;q=0")


# Feature: gw32-mcp-transport, Property 12: SSE framing round trip
def test_property_12_sse_round_trip() -> None:
    rng = random.Random(12)
    alphabet = ["a", "Z", " ", "\n", "\r", "\u00e9", "\u2028", "\U0001f600", '"', "\\", "data:"]
    for _ in range(200):
        text = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 40)))
        body = json.dumps({"jsonrpc": "2.0", "id": rng.randint(0, 9), "result": {"t": text}})
        frame = encode_message(body.encode())
        assert frame.endswith(b"\n\n") and frame.isascii()
        assert frame.count(b"\n\n") == 1  # one event, no early terminator inside the data
        events = decode_events(frame)
        assert events == [
            ("message", json.dumps(json.loads(body), ensure_ascii=True, separators=(",", ":")))
        ]
        assert json.loads(events[0][1]) == json.loads(body)


def test_sse_grammar_matches_the_shipped_decoder() -> None:
    """The shipped `SSEDecoder._data_payload` extracts the same payload text from a frame."""
    decoder = SSEDecoder()
    for payload in ('{"a":1}', "line1\nline2"):
        lines = "".join(f"data: {line}\n" for line in payload.split("\n"))
        event = f": c\nevent: message\n{lines}"
        assert decoder._data_payload(event) == payload  # noqa: SLF001
        assert decode_events((event + "\n").encode()) == [("message", payload)]
