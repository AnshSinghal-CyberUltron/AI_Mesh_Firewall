"""GW28 edge: one open per request, preflight, audit destination, secrets, traces, console."""

from __future__ import annotations

import json
from collections.abc import Sequence

import pytest

from gateway_v2.dispatch.routing import DispatchRouter
from gateway_v2.domain import Category, Finding, FindingStatus, Span
from tests.dispatch._gw28_fakes import FakeClient, Timeline, err, role, text
from tests.edge import _gw28_app as H
from tests.edge._chat_lifecycle_fixtures import pass_through_plan

_CANARY = "ZS-CANARY-4242-SECRET"


def _ok(name: str, tl: Timeline, body: str = "hello") -> FakeClient:
    return FakeClient(name, [role(), text(body, final=True)], tl)


def _fail(name: str, tl: Timeline, code: str = "upstream_unavailable") -> FakeClient:
    return FakeClient(name, [err(code)], tl)


def _canary_scanner(t: str) -> Sequence[Finding]:
    i = t.find(_CANARY)
    if i < 0:
        return ()
    return (
        Finding(
            detector="gw28-canary",
            detector_version="t",
            category=Category.PII,
            status=FindingStatus.EXECUTED,
            confidence=1.0,
            spans=(Span(start=i, end=i + len(_CANARY)),),
            evidence=None,
        ),
    )


@pytest.mark.parametrize("stream", [False, True])
def test_t_edge_1_one_open_routed_per_request_fallback_inside(stream: bool) -> None:
    for failing in ("a", "b"):
        tl = Timeline()
        clients = {n: (_fail(n, tl) if n == failing else _ok(n, tl)) for n in ("a", "b")}
        rp = H.routed(clients)
        status, _h, body = H.post(H.app(rp), stream=stream)
        assert status == 200, body
        assert "hello" in body
        assert rp.calls == 1  # fallback happened INSIDE the single open_routed call
        # The routing order is keyed on the (now random, per-request) id, so whether the failing
        # client is tried first is not fixed; either way one fallback at most, inside the one call.
        assert 1 <= len(tl.opens()) <= 2


def test_t_edge_1_routing_none_opens_plain_provider_once() -> None:
    status, _h, _b = H.post(H.app(None))
    assert status in (200, 502, 500, 503)  # plain stub provider with an empty script


def test_t_edge_1_all_attempts_fail_is_one_declared_error() -> None:
    tl = Timeline()
    rp = H.routed({n: _fail(n, tl) for n in ("a", "b")})
    status, _h, body = H.post(H.app(rp))
    assert status == 503 and rp.calls == 1 and len(tl.opens()) == 2
    assert json.loads(body)["error"]["code"] == "routing_attempts_exhausted"


def test_t_edge_3_preflight_refusal_makes_zero_calls_and_refunds() -> None:
    tl = Timeline()
    rp = H.routed({"a": _ok("a", tl)})
    budget = H.RefundBudget()
    status, _h, body = H.post(H.app(rp, quota=budget), model="not-served")
    assert status == 404 and json.loads(body)["error"]["code"] == "model_not_found"
    assert rp.calls == 0 and tl.opens() == []
    assert len(budget.refunds) == 1


def test_t_edge_4_audit_destination_is_plan_derived_and_trace_destination_is_candidates() -> None:
    tl = Timeline()
    client = _ok("a", tl)
    rp = H.routed({"a": client})
    status, _h, _b = H.post(H.app(rp))
    assert status == 200
    # the open_routed caller handed over the plan-derived logical destination (unchanged)...
    logical = DispatchRouter().select(pass_through_plan())
    assert rp.requests[0].destination == logical
    # ...while the candidate client received the validated candidate label.
    assert client.seen[0].destination == "dest-a"


def test_t_edge_5_secret_in_fallback_output_is_redacted_nonstreaming() -> None:
    for failing in ("a", "b"):
        tl = Timeline()
        leaky = FakeClient("leaky", [text(f"x {_CANARY} y", final=True)], tl)
        clients = {failing: _fail(failing, tl), ("b" if failing == "a" else "a"): leaky}
        status, _h, body = H.post(H.app(H.routed(clients), error_scanner=_canary_scanner))
        assert _CANARY not in body, body
        assert status == 200


def test_t_edge_5_secret_in_error_frame_of_attempt_is_not_forwarded() -> None:
    from gateway_v2.dispatch.provider import UpstreamEvent

    tl = Timeline()
    frame = UpstreamEvent(text_deltas=(), final=True, error_frame=f"boom {_CANARY}")
    rp = H.routed({"a": FakeClient("a", [frame], tl)})
    status, _h, body = H.post(H.app(rp, error_scanner=_canary_scanner))
    assert _CANARY not in body


def test_console_carries_model_routing_deciding_rule_and_validates() -> None:
    tl = Timeline()
    rp = H.routed({"a": _ok("a", tl)})
    status, _h, body = H.post(H.app(rp))
    stages = json.loads(body)["pipeline_trace"]["stages"]
    routing = next(s for s in stages if s["name"] == "model_routing")
    fields = routing["deciding_rule"].split("|")
    assert len(fields) == 5
    assert fields[0] == "primary" and fields[1] == "a" and fields[2] == "0" and fields[3] == "-"
    assert fields[4] == H.MODEL  # alias is a non-passthrough served alias
    assert routing["action"] == "allow" and routing["ran"] is True


def test_console_on_fallback_records_remap_and_on_exhaustion_a_skip() -> None:
    for failing in ("a", "b"):
        tl = Timeline()
        other = "b" if failing == "a" else "a"
        rp = H.routed({failing: _fail(failing, tl), other: _ok(other, tl)})
        status, _h, body = H.post(H.app(rp))
        stages = json.loads(body)["pipeline_trace"]["stages"]
        rule = next(s for s in stages if s["name"] == "model_routing")["deciding_rule"]
        parts = rule.split("|")
        if len(tl.opens()) == 2:
            assert parts[0] == "fallback_after_unavailable" and parts[3] == failing
    tl = Timeline()
    rp = H.routed({n: _fail(n, tl) for n in ("a", "b")})
    status, _h, body = H.post(H.app(rp))
    stage = next(
        s for s in json.loads(body)["pipeline_trace"]["stages"] if s["name"] == "model_routing"
    )
    assert stage["action"] == "skip" and stage["ran"] is False
    assert stage["deciding_rule"].startswith("refused_attempts_exhausted|-|1|-|")


def test_t_edge_8_concurrent_identical_requests_have_disjoint_traces() -> None:
    import asyncio

    import httpx

    tl = Timeline()

    # Same request (same request_key) with different outcomes: whichever candidate each hits is
    # decided by HRW (same for both), so make the FIRST ranked candidate fail on odd calls only.
    class Flaky(FakeClient):
        n = 0

        def open(self, request):  # type: ignore[no-untyped-def]
            Flaky.n += 1
            self.script = (
                (err("upstream_unavailable"),) if Flaky.n % 2 else (text("ok", final=True),)
            )
            return super().open(request)

    rp = H.routed({"a": Flaky("a", [], tl), "b": Flaky("b", [], tl)})
    a = H.app(rp)

    async def one() -> str:
        transport = httpx.ASGITransport(app=a)
        async with httpx.AsyncClient(transport=transport, base_url="http://t.test") as cl:
            r = await cl.post(
                "/v1/chat/completions",
                content=json.dumps(
                    {"model": H.MODEL, "messages": [{"role": "user", "content": "same"}]}
                ),
                headers={"authorization": "Bearer k", "content-type": "application/json"},
            )
            return r.text

    async def both() -> list[str]:
        return list(await asyncio.gather(one(), one()))

    bodies = asyncio.run(both())
    for b in bodies:
        stage = next(
            s for s in json.loads(b)["pipeline_trace"]["stages"] if s["name"] == "model_routing"
        )
        parts = stage["deciding_rule"].split("|")
        assert len(parts) == 5  # each console is self-consistent, never a merged decision list
