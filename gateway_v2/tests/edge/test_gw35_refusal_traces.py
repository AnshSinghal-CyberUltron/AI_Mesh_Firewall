"""GW35: every refusal and failure shows the stages that ran, in the body, frame and audit row."""

from __future__ import annotations

import asyncio

import pytest

from gateway_v2.admit.grant import Admitted, BudgetVerdict, ShedVerdict
from gateway_v2.audit.console_schema import validate_audit_record
from gateway_v2.domain import posture
from gateway_v2.edge.chat_deps import unresolved_snapshot_source
from gateway_v2.edge.identity_resolver import IdentityOutcome
from tests.edge import _gw35_harness as h


def _shape(console: dict[str, object]) -> dict[str, tuple[str, bool]]:
    return {n: (str(s["action"]), bool(s["ran"])) for n, s in h.stages_of(console).items()}


def _audit_consoles(res: h.Result) -> list[dict[str, object]]:
    out = []
    for record in res.records:
        console = record.detail.get("console")
        assert isinstance(console, dict)
        out.append(console)
    return out


class _Shed:
    async def admit(self, owner_id: str, request_id: str, enqueued_at: float) -> object:
        del owner_id, enqueued_at
        return ShedVerdict(posture.OVERLOAD_SHED, 1.5, False, request_id)

    def complete(self, request_id: str) -> None:
        del request_id


class _Budget:
    def __init__(self, code: str | None) -> None:
        self.code = code

    async def evaluate(self, cost: int, *, request_id: str, **kw: object) -> object:
        del cost, kw
        if self.code is None:
            return Admitted("dev-org", request_id, 0.0)
        return BudgetVerdict(self.code, 1.5, False, request_id)

    def refund(self, *a: object, **k: object) -> None:
        return None


class _Unverified:
    async def resolve(self, scope: object) -> None:
        del scope
        return None

    async def resolve_outcome(self, scope: object) -> IdentityOutcome:
        del scope
        return IdentityOutcome.unverified()


_AUTH_ALLOW_ONLY = {"auth": ("allow", True)}


def _call(mutate, **kw) -> h.Result:  # type: ignore[no-untyped-def]
    app, records, _ = h.build_app(h.ScriptedProvider(h.ok_events()), mutate_deps=mutate)
    return asyncio.run(h.post(app, records, **kw))


def test_a_bad_key_shows_auth_block_and_nothing_else_ran() -> None:
    res = _call(None, auth="Bearer nope")
    body = res.json()
    assert res.status == 401 and "error" in body
    shape = _shape(body)
    assert shape["auth"] == ("block", True)
    assert all(shape[n] == ("skip", False) for n in h.STAGES[1:])
    assert body["zeroshield"]["action"] == "block"  # type: ignore[index]
    assert res.headers["x-zeroshield-action"] == "block"
    assert body["source"] == "gateway_v2"


def test_identity_doubt_is_an_auth_error_not_a_block() -> None:
    res = _call(lambda d: d.update(identity=_Unverified()))
    shape = _shape(res.json())
    assert res.status == 503 and shape["auth"] == ("error", True)
    assert res.json()["zeroshield"]["action"] == "error"  # type: ignore[index]


def test_plan_unavailable_is_input_scan_error_not_run() -> None:
    res = _call(lambda d: d.update(snapshot_source=unresolved_snapshot_source))
    body = res.json()
    shape = _shape(body)
    assert shape["auth"] == ("allow", True)
    assert shape["rate_limit"] == ("skip", False)
    assert shape["input_scan"] == ("error", False)
    assert body["zeroshield"]["request_id"]  # type: ignore[index]
    assert res.headers.get("x-request-id") != body["zeroshield"]["request_id"]  # type: ignore[index]
    assert [r.org_id for r in res.records] == ["dev-org"]


@pytest.mark.parametrize(
    ("mutate", "rate"),
    [
        (lambda d: d.update(admission=_Shed()), ("block", True)),
        (lambda d: d.update(quota=_Budget(posture.RATE_LIMITED)), ("block", True)),
        (lambda d: d.update(quota=_Budget(posture.BUDGET_UNAVAILABLE)), ("error", True)),
    ],
)
def test_rate_limit_stage_reports_its_own_outcome(mutate, rate) -> None:  # type: ignore[no-untyped-def]
    res = _call(mutate)
    shape = _shape(res.json())
    assert shape["auth"] == ("allow", True)
    assert shape["rate_limit"] == rate
    assert all(shape[n] == ("skip", False) for n in h.STAGES[2:])


def test_input_block_skips_provider_stages_and_stays_block() -> None:
    res = _call(None, content="key AKIAIOSFODNN7EXAMPLE")
    shape = _shape(res.json())
    assert shape["input_scan"] == ("block", True)
    assert shape["model_routing"] == ("skip", False)
    assert res.json()["zeroshield"]["action"] == "block"  # type: ignore[index]


def test_a_clean_request_runs_all_six_and_allows() -> None:
    res = _call(None)
    assert all(v == ("allow", True) for v in _shape(res.json()).values())


@pytest.mark.parametrize("code", ["upstream_error", "upstream_rate_limited", "upstream_timeout"])
def test_upstream_failure_is_error_not_an_output_block(code: str) -> None:
    app, records, _ = h.build_app(h.ScriptedProvider(h.failed_events(code)))
    res = asyncio.run(h.post(app, records))
    body = res.json()
    shape = _shape(body)
    assert shape["model_output"] == ("error", True)
    assert shape["output_guardrail"] == ("skip", False)
    assert body["zeroshield"]["action"] == "error"  # type: ignore[index]
    assert res.headers["x-zeroshield-action"] == "error"
    assert "block" not in {a for a, _ in shape.values()}


def test_upstream_failure_after_input_redact_is_still_error() -> None:
    app, records, _ = h.build_app(h.ScriptedProvider(h.failed_events("upstream_error")))
    res = asyncio.run(h.post(app, records, "SSN 123-45-6789"))
    shape = _shape(res.json())
    assert shape["input_scan"] == ("redact", True)
    assert res.json()["zeroshield"]["action"] == "error"  # type: ignore[index]


def test_streamed_upstream_failure_frame_matches_the_audit_console() -> None:
    app, records, _ = h.build_app(h.ScriptedProvider(h.failed_events("upstream_error")))
    res = asyncio.run(h.post(app, records, stream=True))
    frame = res.trace_frame()
    assert frame is not None and frame["zeroshield"]["action"] == "error"  # type: ignore[index]
    assert _shape(frame)["model_output"] == ("error", True)
    assert all(console == {k: frame[k] for k in console} for console in _audit_consoles(res))


def test_audit_console_equals_the_response_console() -> None:
    for mutate, kw in (
        (None, {"auth": "Bearer nope"}),
        (lambda d: d.update(admission=_Shed()), {}),
        (lambda d: d.update(quota=_Budget(posture.BUDGET_UNAVAILABLE)), {}),
        (lambda d: d.update(snapshot_source=unresolved_snapshot_source), {}),
        (None, {"content": "key AKIAIOSFODNN7EXAMPLE"}),
        (None, {"content": "key AKIAIOSFODNN7EXAMPLE", "stream": True}),
    ):
        res = _call(mutate, **kw)
        console = res.trace_frame() if kw.get("stream") else res.json()
        assert console is not None
        for audited in _audit_consoles(res):
            assert audited["pipeline_trace"] == console["pipeline_trace"], kw
            assert audited["zeroshield"] == console["zeroshield"], kw


def test_audit_rows_validate_against_the_schema() -> None:
    for kw in ({"auth": "Bearer nope"}, {}):
        res = _call(None, **kw)
        for record in res.records:
            validate_audit_record(dict(record.as_mapping()))


def test_a_streamed_identity_refusal_is_plain_http_with_the_console() -> None:
    res = _call(None, auth="Bearer nope", stream=True)
    assert res.status == 401 and res.headers["content-type"].startswith("application/json")
    assert _shape(res.json())["auth"] == ("block", True)


def test_a_malformed_body_keeps_no_trace() -> None:
    res = _call(None, body=b"{not json")
    assert "pipeline_trace" not in res.body.decode()


def test_the_body_gains_exactly_zeroshield_pipeline_trace_and_source() -> None:
    res = _call(None)
    body = res.json()
    assert {"zeroshield", "pipeline_trace", "source"} <= set(body)
    assert body["source"] == "gateway_v2"
    assert set(body) - {"zeroshield", "pipeline_trace", "source"} == {
        "id", "object", "created", "model", "choices", "usage",
    }


def test_the_stream_trace_frame_carries_source() -> None:
    res = _call(None, stream=True)
    frame = res.trace_frame()
    assert frame is not None and frame["source"] == "gateway_v2"
