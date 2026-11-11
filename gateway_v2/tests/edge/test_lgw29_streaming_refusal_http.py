# Validates: AC8 (a refused streaming request is HTTP 503, never a 200 SSE head)
"""A killed org + a streaming request: status 503, retry hints, no event-stream, no SSE bytes."""

from __future__ import annotations

import asyncio

import pytest

from tests.edge._lgw29_app import ENDPOINTS, Harness


@pytest.mark.parametrize(("path", "build"), ENDPOINTS, ids=[p for p, _ in ENDPOINTS])
def test_streaming_requests_get_http_503_with_the_retry_headers(path, build) -> None:  # type: ignore[no-untyped-def]
    h = Harness()
    h.engage("org:gw15-conformance-org")
    result = asyncio.run(h.call(path, build(stream=True)))
    assert result.status == 503
    assert result.headers["content-type"].startswith("application/json")
    assert int(result.headers["retry-after"]) >= 1 and result.headers["x-should-retry"] == "false"
    assert not result.body.startswith(b"data:") and b"event:" not in result.body
    assert result.code() == "kill_switch_engaged"


def test_the_non_incident_streaming_refusals_are_plain_http_without_retry_hints() -> None:
    h = Harness()
    # GW34 R7 (merged after GW29): every post-parse refusal is a plain HTTP error, stream or not.
    # A refusal that is NOT an incident code gets its own status and no GW29 retry hints.
    h.budget.evaluate = _budget_refuses  # type: ignore[method-assign]
    result = asyncio.run(
        h.call(
            "/v1/chat/completions",
            {"model": "m", "stream": True, "messages": [{"role": "user", "content": "x"}]},
        )
    )
    assert result.status == 503 and result.code() == "budget_unavailable"
    assert result.headers["content-type"].startswith("application/json")
    assert "retry-after" not in result.headers and "x-should-retry" not in result.headers


async def _budget_refuses(cost, *, request_id, owner="", principal=None, key_hash=""):  # type: ignore[no-untyped-def]
    from gateway_v2.admit.grant import budget_verdict

    return budget_verdict(request_id)
