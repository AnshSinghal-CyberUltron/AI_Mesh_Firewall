# Validates: AC8 (Retry-After + x-should-retry on every incident refusal; others byte-identical)
"""Rendering of the incident refusal headers and the closed incident code set (design 6.4a)."""

from __future__ import annotations

import pytest

from gateway_v2.domain import posture
from gateway_v2.edge import errors
from gateway_v2.edge.counters import POSTURE_CODES

INCIDENT_CODES = (
    posture.KILL_SWITCH_ENGAGED,
    posture.MODEL_KILLED,
    posture.MODEL_ISOLATED_NO_FALLBACK,
    posture.REROUTE_REFUSED,
    posture.KILL_SWITCH_UNAVAILABLE,
)


def _headers(response: errors.HTTPResponse) -> dict[str, str]:
    return dict(response.headers)


@pytest.mark.parametrize("code", INCIDENT_CODES)
@pytest.mark.parametrize(("retry", "shown"), [(0.2, "1"), (1.0, "1"), (1.01, "2"), (7.4, "8")])
def test_retry_after_is_an_integer_of_at_least_one_second(
    code: str, retry: float, shown: str
) -> None:
    response = errors.render_http(code, request_id="r", retry_after_s=retry)
    assert response.status == 503
    headers = _headers(response)
    assert headers["retry-after"] == shown and headers["x-should-retry"] == "false"


@pytest.mark.parametrize("code", [*INCIDENT_CODES, posture.PLAN_UNAVAILABLE, "input_blocked", "x"])
def test_without_a_retry_hint_the_response_is_byte_identical_to_before(code: str) -> None:
    plain = errors.render_http(code, request_id="r")
    assert "retry-after" not in _headers(plain) and "x-should-retry" not in _headers(plain)
    assert plain == errors.render_http(code, request_id="r", retry_after_s=None)


def test_the_incident_http_code_set_is_exactly_the_five_gate_codes() -> None:
    assert set(errors.INCIDENT_HTTP_CODES) == set(INCIDENT_CODES)
    assert all(errors.render_http(c).status == 503 for c in errors.INCIDENT_HTTP_CODES)


def test_every_new_code_has_an_envelope_and_a_posture_counter() -> None:
    new = (*INCIDENT_CODES[:4], posture.STREAM_MODEL_ISOLATED)
    for code in new:
        assert errors.render_http(code).json()["error"]["type"] == "service_unavailable"  # type: ignore[index]
        assert "generic" not in errors.render_http(code).json()["error"]["message"].lower()  # type: ignore[index]
        assert code in POSTURE_CODES
    assert errors.retry_headers(None) == ()
