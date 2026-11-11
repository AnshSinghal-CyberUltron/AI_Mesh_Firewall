"""Error_Envelope mapping for ``request_id_unavailable`` (spec request-id-uniqueness, task 3.4)."""

from __future__ import annotations

import json
from pathlib import Path

from gateway_v2.domain import posture
from gateway_v2.edge import errors

_GOLDEN = Path(__file__).parent / "golden" / "gw35_pre_change.json"


def test_mint_failure_renders_503_without_id() -> None:
    rendered = errors.render_http(posture.REQUEST_ID_UNAVAILABLE)
    assert rendered.status == 503
    names = {name for name, _ in rendered.headers}
    assert "x-request-id" not in names
    body = json.loads(rendered.body)["error"]
    assert body["code"] == "request_id_unavailable"
    assert body["type"] == "service_unavailable"
    assert body["request_id"] is None
    assert body["message"] == "The request could not be started."


def test_mint_failure_is_a_mapped_code_not_the_generic_fallback() -> None:
    assert errors.GENERIC_HTTP_STATUS != 503
    assert errors.render_http(posture.REQUEST_ID_UNAVAILABLE).status == 503
    assert (
        b"The gateway could not complete the request."
        not in errors.render_http(posture.REQUEST_ID_UNAVAILABLE).body
    )


def test_sse_frame_has_generic_message_and_no_id() -> None:
    frame = errors.render_sse(posture.REQUEST_ID_UNAVAILABLE)
    obj = json.loads(frame.decode().removeprefix("data: ").strip())
    assert obj["error"]["code"] == "request_id_unavailable"
    assert obj["error"]["message"] == "The request could not be started."
    assert "request_id" not in obj["error"]


def test_other_codes_bytes_unchanged() -> None:
    shared = errors.render_http(posture.SHARED_STATE_UNAVAILABLE)
    assert shared.status == 503
    assert json.loads(shared.body)["error"]["code"] == "shared_state_unavailable"
    budget = errors.render_http(posture.BUDGET_UNAVAILABLE)
    assert (
        json.loads(budget.body)["error"]["message"]
        == errors.error_frame_for(posture.BUDGET_UNAVAILABLE).message
    )
    assert errors.render_http("not_a_code").status == errors.GENERIC_HTTP_STATUS


def test_golden_does_not_mention_the_new_code() -> None:
    assert "request_id_unavailable" not in _GOLDEN.read_text(encoding="utf-8")
