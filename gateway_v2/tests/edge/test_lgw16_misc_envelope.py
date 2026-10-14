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
