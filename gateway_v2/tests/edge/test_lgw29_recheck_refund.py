# Validates: AC5, AC20, P9 (the recheck sits ABOVE mark_dispatched, so a refusal is refunded)
"""First-attempt, retry-attempt and non-streaming rechecks: refund once, zero provider opens."""

from __future__ import annotations

import ast
import asyncio
import inspect
import textwrap

import pytest

from gateway_v2.domain.incident import GateVerdict
from gateway_v2.domain.posture import KILL_SWITCH_ENGAGED
from gateway_v2.edge import chat_stream, incident_recheck, openai_misc, openai_nonstream, routes
from tests.edge._lgw29_app import Harness, chat


def _order(source: str, first: str, second: str) -> bool:
    """True iff the first call named ``first`` precedes the first call named ``second``."""
    lines: dict[str, int] = {}
    for node in ast.walk(ast.parse(textwrap.dedent(source))):
        if isinstance(node, ast.Call):
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            lines[name] = min(lines.get(name, node.lineno), node.lineno)
    return first in lines and second in lines and lines[first] < lines[second]


def test_the_recheck_precedes_mark_dispatched_at_every_provider_open_site() -> None:
    assert _order(
        # GW34 moved the streaming attempt into ``ChatStream`` (``edge/chat_stream.py``).
        inspect.getsource(chat_stream.ChatStream._next_attempt),
        "attempt_recheck_cut",
        "mark_dispatched",
    )
    assert _order(
        inspect.getsource(openai_nonstream._collect_output_channels),
        "recheck_or_raise",
        "mark_dispatched",
    )
    assert _order(
        inspect.getsource(openai_misc.EmbeddingsSurface._drain_vectors),
        "recheck_or_raise",
        "mark_dispatched",
    )
    assert _order(
        inspect.getsource(routes.ChatRoute.handle), "first_attempt_refusal", "_stream_response"
    )


def test_negative_control_a_recheck_below_mark_dispatched_is_detected() -> None:
    wrong = "def f(h, r):\n    h.mark_dispatched()\n    attempt_recheck_cut(r)\n"
    assert not _order(wrong, "attempt_recheck_cut", "mark_dispatched")


@pytest.mark.parametrize("stream", [False, True])
def test_a_refusal_is_refunded_once_with_the_spent_cost_and_never_dispatched(stream: bool) -> None:
    h = Harness()
    real = h.rig.gate.decide

    def decide_then_kill(**kwargs):  # type: ignore[no-untyped-def]
        verdict = real(**kwargs)
        h.engage("global")
        return verdict

    h.rig.gate.decide = decide_then_kill  # type: ignore[method-assign]
    result = asyncio.run(h.call("/v1/chat/completions", chat(stream=stream)))
    assert result.status == 503 and h.provider.open_calls == 0
    assert len(h.budget.refund_calls) == 1
    cost, _request, owner = h.budget.refund_calls[0]
    assert cost == h.budget.spent and owner == h.org
    assert h.budget.net == 0 and h.admission.completed == h.admission.admitted == 1
    assert [r.detail["cause"] for r in h.records] == ["recheck"]


def test_a_refusal_at_the_streaming_attempt_cuts_the_stream_without_opening_the_provider() -> None:
    h = Harness()
    real = h.recheck
    calls: list[int] = []

    def recheck(**kwargs):  # type: ignore[no-untyped-def]
        calls.append(1)
        if len(calls) == 2:  # call 1 is first_attempt_refusal, call 2 is the streaming _attempt
            h.engage("global")
        return real(**kwargs)

    h.recheck = recheck
    result = asyncio.run(h.call("/v1/chat/completions", chat(stream=True)))
    # GW34 deferred start: no byte was written, so the cut is the plain HTTP 503 (with the GW29
    # retry hints), not a 200 SSE head followed by an error frame.
    assert result.status == 503 and result.code() == "stream_killed"
    assert result.headers["content-type"].startswith("application/json")
    assert int(result.headers["retry-after"]) >= 1 and result.headers["x-should-retry"] == "false"
    assert "[DONE]" not in result.text and not result.body.startswith(b"data:")
    assert h.provider.open_calls == 0 and len(calls) == 2
    assert h.budget.net == 0 and len(h.budget.refund_calls) == 1
    assert [r.detail["cause"] for r in h.records] == ["recheck"]
    assert h.records[0].detail["underlying"] == KILL_SWITCH_ENGAGED


def test_a_proceed_verdict_is_untouched_by_the_recheck() -> None:
    h = Harness()
    verdict = h.recheck(org_id=h.org, model="gpt-test")
    assert isinstance(verdict, GateVerdict) and verdict.routed_model == "gpt-test"
    assert incident_recheck.stream_code_for(KILL_SWITCH_ENGAGED) == "stream_killed"
    assert incident_recheck.stream_code_for("kill_switch_unavailable") == "stream_snapshot_stale"
    assert incident_recheck.stream_code_for("model_isolated_no_fallback") == "stream_model_isolated"
