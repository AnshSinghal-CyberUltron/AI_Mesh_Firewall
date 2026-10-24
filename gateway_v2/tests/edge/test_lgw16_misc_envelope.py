# Feature: chat-surface-gw16-misc
# Validates: Requirements 7.3, 9.1
"""Unit tests for the two GW16 misc-surface envelope entries (Decision 1).

Covers the two ``_ENVELOPE`` render entries added to :mod:`gateway_v2.edge.errors`:

* ``missing_required_parameter`` → HTTP ``400`` / ``error.type == "invalid_request_error"``
  (a moderations body missing ``input`` so the stock SDK raises a 400/BadRequest and the
  ``error.code == "missing_required_parameter"`` assertion holds).
* ``model_not_found`` → HTTP ``404`` / ``error.type == "not_found_error"`` (so
  ``openai.models.retrieve(unknown)`` raises ``openai.NotFoundError``).

These are example/unit tests pinning the declared HTTP renderings; the Property-15 envelope
sweep lives in a later task. Each assertion reads ``render_http`` output (the single render
authority) so it tracks the table rather than a drift-prone hardcode.
"""

from __future__ import annotations

import json

from gateway_v2.edge import errors
from gateway_v2.runtime.resources import ResourceContract


def _error_obj(body: bytes) -> dict[str, object]:
    """Parse an ``{"error": {...}}`` HTTP JSON body and return the inner ``error`` object."""
    outer = json.loads(body.decode("utf-8"))
    assert isinstance(outer, dict), f"HTTP body is not a JSON object: {outer!r}"
    error = outer.get("error")
    assert isinstance(error, dict), f"HTTP body has no error object: {outer!r}"
    return error


def test_missing_required_parameter_renders_400_invalid_request() -> None:
    resp = errors.render_http("missing_required_parameter")
    assert resp.status == 400, resp.status
    error = _error_obj(resp.body)
    assert error["type"] == "invalid_request_error", error
    assert error["code"] == "missing_required_parameter", error


def test_model_not_found_renders_404_not_found() -> None:
    resp = errors.render_http("model_not_found")
    assert resp.status == 404, resp.status
    error = _error_obj(resp.body)
    assert error["type"] == "not_found_error", error
    assert error["code"] == "model_not_found", error


# --------------------------------------------------------------------------- #
# v2-enforcement-leaks task 26.4: input_unmaskable + the two boundary codes.
# --------------------------------------------------------------------------- #


def test_input_unmaskable_renders_400_invalid_request() -> None:
    """An unmaskable input REDACT refusal renders the input content-block HTTP family (26.4).

    ``input_unmaskable`` is the same disposition as an input block (GW01 §10.2.2 / v1
    ``GATEWAY_BLOCK_STATUS`` default 400): HTTP 400 ``invalid_request_error`` with the v2
    posture ``code`` ``input_unmaskable``.
    """
    resp = errors.render_http("input_unmaskable")
    assert resp.status == 400, resp.status
    error = _error_obj(resp.body)
    assert error["type"] == "invalid_request_error", error
    assert error["code"] == "input_unmaskable", error


def test_invalid_max_tokens_renders_400_with_contract_boundary_in_message() -> None:
    """A ``max_tokens`` boundary refusal renders 400 and names the contract boundary (26.4).

    The declared message is boundary-free; the concrete boundary reaches the body ONLY when the
    contract-bearing caller supplies it as ``detail``. The number is sourced from
    ``ResourceContract.max_output_tokens_boundary()`` here — not hardcoded — so the assertion
    tracks the contract rather than a drift-prone literal (and no capacity literal lives in the
    render modules).
    """
    boundary = ResourceContract.max_output_tokens_boundary()
    resp = errors.render_http(
        "invalid_max_tokens", param="max_tokens", boundary=boundary
    )
    assert resp.status == 400, resp.status
    error = _error_obj(resp.body)
    assert error["type"] == "invalid_request_error", error
    assert error["code"] == "invalid_max_tokens", error
    assert error["param"] == "max_tokens", error
    assert str(boundary) in str(error["message"]), error

    # Without a supplied boundary the declared message stays boundary-free (no literal leaks).
    bare = errors.render_http("invalid_max_tokens", param="max_tokens")
    assert str(boundary) not in str(_error_obj(bare.body)["message"]), bare.body


def test_invalid_max_completion_tokens_renders_400_invalid_request() -> None:
    """The sibling ``max_completion_tokens`` boundary refusal: HTTP 400 invalid_request_error."""
    resp = errors.render_http(
        "invalid_max_completion_tokens", param="max_completion_tokens"
    )
    assert resp.status == 400, resp.status
    error = _error_obj(resp.body)
    assert error["type"] == "invalid_request_error", error
    assert error["code"] == "invalid_max_completion_tokens", error
    assert error["param"] == "max_completion_tokens", error
