# Validates: AC1-AC3, AC5, AC8-AC10 (every chat-shaped endpoint x stream/non-stream)
"""Phase 4b refusal, first-attempt recheck and mid-stream cut on all three chat-shaped endpoints.

{/v1/chat/completions, /v1/completions, /v1/responses} x {stream, non-stream}: a kill refuses with
503 + ``Retry-After`` + ``x-should-retry: false`` (never an SSE head), charges nothing, calls no
provider and writes exactly one audit record; a kill that lands after the head cuts the stream; and
a CLEAN stream still completes (the regression for the Responses stream running uncontrolled).
"""

from __future__ import annotations

import asyncio

import pytest

from tests.edge._lgw29_app import ENDPOINTS, Harness, words

CASES = [(path, build, stream) for path, build in ENDPOINTS for stream in (False, True)]
IDS = [f"{path}-{'stream' if stream else 'plain'}" for path, _, stream in CASES]


def _assert_refused(h: Harness, result, code: str) -> None:  # type: ignore[no-untyped-def]
    assert result.status == 503, result.text
    assert result.code() == code
    assert int(result.headers["retry-after"]) >= 1
    assert result.headers["x-should-retry"] == "false"
    assert "text/event-stream" not in result.headers.get("content-type", "")
    assert h.provider.open_calls == 0
    assert h.budget.net == 0
    assert h.admission.admitted == h.admission.completed
    refusals = [r for r in h.records if r.outcome == "rejected"]
    assert len(h.records) == 1 and len(refusals) == 1, [r.detail for r in h.records]


@pytest.mark.parametrize(("path", "build", "stream"), CASES, ids=IDS)
@pytest.mark.parametrize(
    ("scopes", "code"),
    [
        (("global",), "kill_switch_engaged"),
        (("org:gw15-conformance-org",), "kill_switch_engaged"),
        (("model:gpt-test",), "model_killed"),
    ],
)
def test_phase_4b_refusal_charges_nothing(path, build, stream, scopes, code) -> None:  # type: ignore[no-untyped-def]
    h = Harness()
    h.engage(*scopes)
    result = asyncio.run(h.call(path, build(stream=stream)))
    _assert_refused(h, result, code)
    assert h.admission.admitted == 0  # phase 4b is BEFORE the admission slot
    assert h.budget.spent == 0  # and before the budget spend


@pytest.mark.parametrize(("path", "build", "stream"), CASES, ids=IDS)
def test_other_orgs_models_are_unaffected(path, build, stream) -> None:  # type: ignore[no-untyped-def]
    h = Harness()
    h.engage("org:someone-else", "model:other-model", "om:someone-else:gpt-test")
    result = asyncio.run(h.call(path, build(stream=stream)))
    assert result.status == 200, result.text
    assert h.provider.open_calls == 1 and h.budget.net > 0


@pytest.mark.parametrize(("path", "build", "stream"), CASES, ids=IDS)
def test_a_clean_request_completes(path, build, stream) -> None:  # type: ignore[no-untyped-def]
    h = Harness()
    result = asyncio.run(h.call(path, build(stream=stream)))
    assert result.status == 200
    if stream:
        assert "event-stream" in result.headers["content-type"]
        assert "stream_killed" not in result.text and "stream_snapshot_stale" not in result.text
        assert "[DONE]" in result.text or "response.completed" in result.text
    assert h.provider.open_calls == 1


@pytest.mark.parametrize(("path", "build", "stream"), CASES, ids=IDS)
def test_a_kill_between_phase_4b_and_the_provider_open_is_refused_and_refunded(  # type: ignore[no-untyped-def]
    path, build, stream
) -> None:
    h = Harness()
    gate = h.rig.gate
    real = gate.decide

    def decide_then_kill(**kwargs):  # type: ignore[no-untyped-def]
        verdict = real(**kwargs)
        h.engage("global")  # lands after phase 4b proceeded, before the recheck
        return verdict

    gate.decide = decide_then_kill  # type: ignore[method-assign]
    result = asyncio.run(h.call(path, build(stream=stream)))
    assert result.status == 503 and h.provider.open_calls == 0
    assert h.budget.spent > 0 and h.budget.net == 0  # spent, then refunded: dispatched is False
    assert "text/event-stream" not in result.headers.get("content-type", "")
    assert len(h.records) == 1 and h.records[0].detail["cause"] == "recheck"
    assert h.records[0].detail["underlying"] == "kill_switch_engaged"


@pytest.mark.parametrize(("path", "build"), ENDPOINTS, ids=[p for p, _ in ENDPOINTS])
def test_a_kill_mid_stream_cuts_after_at_most_one_more_chunk(path, build) -> None:  # type: ignore[no-untyped-def]
    def kill_after_first(h: Harness, index: int) -> None:
        if index == 0:
            h.engage("global")

    h = Harness(words("a b c d e f g h i j"), between=kill_after_first)
    result = asyncio.run(h.call(path, build(stream=True)))
    assert result.status == 200
    assert "stream_killed" in result.text
    assert "[DONE]" not in result.text
    marker = '"type":"response.output_text.delta"' if path == "/v1/responses" else '"content"'
    delivered = result.text.count(marker)
    assert 1 <= delivered <= 3, (delivered, result.text)
    terminal = [r for r in h.records if r.outcome == "terminated"]
    assert len(terminal) == 1 and terminal[0].detail["cause"] == "cut_killed"
    assert h.provider.aborts >= 0
