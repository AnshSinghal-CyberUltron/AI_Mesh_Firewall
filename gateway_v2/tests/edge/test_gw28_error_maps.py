"""GW28: every ROUTING_* posture code has a declared HTTP envelope and SSE frame (additive)."""

from __future__ import annotations

import json

import pytest

from gateway_v2.domain import posture
from gateway_v2.edge import errors
from gateway_v2.edge.wire.sse import ERROR_SHAPES, error_frame_for

_CODES = sorted(
    v for k, v in vars(posture).items() if k.startswith("ROUTING_") and isinstance(v, str)
)


def test_all_eight_routing_codes_are_discovered() -> None:
    assert len(_CODES) == 8


@pytest.mark.parametrize("code", _CODES)
def test_http_and_sse_render_a_declared_non_generic_body(code: str) -> None:
    response = errors.render_http(code)
    expected = 404 if code == posture.ROUTING_UNKNOWN_MODEL else 503
    assert response.status == expected
    body = json.loads(response.body)["error"]
    assert body["code"] == code
    assert body["message"] != "The gateway could not complete the request."
    assert code in ERROR_SHAPES
    frame = error_frame_for(code)
    assert frame.code == code and frame.message == body["message"]
    assert code in errors.render_sse(code).decode()
