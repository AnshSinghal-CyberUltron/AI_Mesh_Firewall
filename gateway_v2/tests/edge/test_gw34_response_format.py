"""GW34 Task 8: response_format end to end (request fields forwarded, JSON-mode output guard)."""
from __future__ import annotations

import json
from typing import Any

from tests.edge.test_gw34_nonstream_tools import _chat, _Scripted
from tests.edge.test_gw36_s1_upstream_body import SSN

_USER = {"role": "user", "content": "hi"}


def _post(provider: _Scripted, fmt: dict[str, Any]) -> tuple[int, bytes]:
    return _chat(provider, {"messages": [_USER], "response_format": fmt})  # type: ignore[arg-type]


_JSON_OBJECT = {"type": "json_object"}
_JSON_SCHEMA = {"type": "json_schema", "json_schema": {
    "name": "answer", "strict": True,
    "schema": {"type": "object", "properties": {"a": {"type": "string"}}, "required": ["a"]}}}


def test_response_format_is_forwarded_and_valid_json_content_is_returned() -> None:
    for fmt in (_JSON_OBJECT, _JSON_SCHEMA):
        provider = _Scripted(("content", '{"a":"x"}'))
        status, body = _post(provider, fmt)
        assert status == 200, body
        assert provider.sent[0]["response_format"]["type"] == fmt["type"]  # type: ignore[index]
        assert json.loads(body)["choices"][0]["message"]["content"] == '{"a":"x"}'


def test_json_mode_content_that_is_not_json_is_blocked() -> None:
    status, body = _post(_Scripted(("content", "Sure! here you go: {")), _JSON_OBJECT)
    assert status != 200 and json.loads(body)["error"]["code"] == "output_blocked"
    assert "Sure" not in body.decode()


def test_redaction_inside_a_json_string_keeps_the_content_valid_json() -> None:
    status, body = _post(_Scripted(("content", json.dumps({"a": f"ssn {SSN}"}))), _JSON_OBJECT)
    assert status == 200, body
    content = json.loads(body)["choices"][0]["message"]["content"]
    assert json.loads(content) == {"a": "ssn [REDACTED]"} and SSN not in body.decode()


def test_text_response_format_does_not_require_json_content() -> None:
    status, body = _post(_Scripted(("content", "plain words")), {"type": "text"})
    assert status == 200, body
