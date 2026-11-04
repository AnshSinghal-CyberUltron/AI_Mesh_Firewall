# Validates: AC8, AC10, P15 (exactly one audit record per refusal and per cut)
"""Count ALL records over one whole request, for every refusal path and a cut."""

from __future__ import annotations

import asyncio

import pytest

from tests.edge._lgw29_app import ENDPOINTS, Harness, words


@pytest.mark.parametrize(("path", "build"), ENDPOINTS, ids=[p for p, _ in ENDPOINTS])
@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("scope", ["global", "org:gw15-conformance-org", "model:gpt-test"])
def test_a_phase_4b_refusal_writes_exactly_one_record(path, build, stream, scope) -> None:  # type: ignore[no-untyped-def]
    h = Harness()
    h.engage(scope)
    asyncio.run(h.call(path, build(stream=stream)))
    assert len(h.records) == 1
    record = h.records[0]
    assert (record.outcome, record.phase.value) == ("rejected", "admission")
    assert record.detail["cause"] in {"global", "org", "model"}
    assert record.detail["retry_after_ms"] >= 1000 and record.detail["status"] == 503


def test_a_stale_snapshot_refusal_is_one_record_with_cause_stale() -> None:
    h = Harness()
    h.rig.mono.now += 60.0
    asyncio.run(
        h.call(
            "/v1/chat/completions", {"model": "m", "messages": [{"role": "user", "content": "x"}]}
        )
    )
    assert [r.detail["cause"] for r in h.records] == ["stale"]


def test_no_secret_bearing_key_reaches_an_incident_record() -> None:
    h = Harness()
    h.engage("global")
    asyncio.run(
        h.call(
            "/v1/chat/completions",
            {"model": "m", "messages": [{"role": "user", "content": "SECRET-PROMPT"}]},
        )
    )
    blob = repr([r.detail for r in h.records])
    assert (
        "SECRET-PROMPT" not in blob
        and "Bearer" not in blob
        and "test" not in blob.replace("status", "")
    )


def test_a_cut_stream_writes_one_terminal_record_and_the_generic_input_record() -> None:
    def kill(h: Harness, index: int) -> None:
        if index == 0:
            h.engage("global")

    h = Harness(words("a b c d e f g h"), between=kill)
    asyncio.run(
        h.call(
            "/v1/chat/completions",
            {"model": "m", "stream": True, "messages": [{"role": "user", "content": "x"}]},
        )
    )
    terminal = [r for r in h.records if r.outcome == "terminated"]
    assert len(terminal) == 1 and terminal[0].detail["cause"] == "cut_killed"
    assert terminal[0].detail["status"] == 200
    assert not [
        r for r in h.records if r.phase.value == "output"
    ]  # the cut replaces the output record
