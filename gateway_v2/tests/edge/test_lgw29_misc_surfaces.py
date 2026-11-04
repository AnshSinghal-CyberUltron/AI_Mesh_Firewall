# Validates: AC14 (embeddings, moderations and MCP honour kills; every POST route is classified)
"""Embeddings / moderations / MCP gating and the route-table sweep (design 6.4b)."""

from __future__ import annotations

import ast
import asyncio
import inspect
import json

import pytest

from gateway_v2.dispatch.provider import UpstreamEvent
from gateway_v2.domain.incident import ModelProfile, RequestConstraints
from gateway_v2.edge import openai_misc
from gateway_v2.edge.routes import build_routes
from tests.admit._lgw29_support import iso_body, ks
from tests.edge._lgw29_app import ORG, Harness
from tests.edge._mcp_fixtures import Broker, Control, mcp_deps, server_row

VECTOR = (UpstreamEvent(text_deltas=(("embedding", "[0.5, 0.25]"),), final=True),)
EMBED = {"model": "embed-1", "input": "hello"}
MODERATE = {"input": "hello"}
TAGS = frozenset({"t"})
PROFILES = {
    "embed-1": ModelProfile("embed-1", "p", TAGS, 2),
    "embed-2": ModelProfile("embed-2", "p", TAGS, 2),
}


def _run(h: Harness, path: str, body: dict[str, object]):  # type: ignore[no-untyped-def]
    return asyncio.run(h.call(path, body))


def _refused(h: Harness, result) -> None:  # type: ignore[no-untyped-def]
    assert result.status == 503, result.text
    assert int(result.headers["retry-after"]) >= 1 and result.headers["x-should-retry"] == "false"
    assert h.provider.open_calls == 0 and h.budget.net == 0
    assert len(h.records) == 1 and h.records[0].outcome == "rejected"


# -- embeddings ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("scope", "code"),
    [
        ("global", "kill_switch_engaged"),
        (f"org:{ORG}", "kill_switch_engaged"),
        ("model:embed-1", "model_killed"),
        (f"om:{ORG}:embed-1", "kill_switch_engaged"),
    ],
)
def test_embeddings_honour_every_kill_scope(scope: str, code: str) -> None:
    h = Harness(VECTOR)
    h.engage(scope)
    result = _run(h, "/v1/embeddings", EMBED)
    _refused(h, result)
    assert result.code() == code
    assert h.admission.admitted == 0 and h.budget.spent == 0  # before the slot and the spend


def test_embeddings_for_another_org_or_model_are_unaffected() -> None:
    h = Harness(VECTOR)
    h.engage("org:other", "model:embed-9", f"om:{ORG}:embed-9")
    result = _run(h, "/v1/embeddings", EMBED)
    assert result.status == 200 and h.provider.open_calls == 1
    assert result.headers.get("x-should-retry") is None


def test_embeddings_never_reroute_an_isolated_model() -> None:
    h = Harness(
        VECTOR,
        rig={
            "catalog": True,
            "profiles": PROFILES,
            "constraints": RequestConstraints(True, None, frozenset(), 0),
        },
    )
    h.rig.engage(ks("iso:embed-1", body=iso_body(fallback="embed-2", expires_at=9e9), feed_seq=1))
    result = _run(h, "/v1/embeddings", EMBED)
    _refused(h, result)
    assert result.code() == "reroute_refused"
    assert h.records[0].detail["cause"] == "reroute_unsupported"


def test_embeddings_send_the_canonical_model_name() -> None:
    h = Harness(VECTOR, aliases={"embed-1-2024": "embed-1"})
    for raw in ("EMBED-1", "embed-1-2024"):
        result = _run(h, "/v1/embeddings", {"model": raw, "input": "x"})
        assert result.status == 200
    assert [b["model"] for b in h.provider.bodies] == ["embed-1", "embed-1"]


def test_an_embeddings_recheck_refusal_is_refunded_and_never_opens_the_provider() -> None:
    h = Harness(VECTOR)
    real = h.rig.gate.decide

    def decide_then_kill(**kwargs):  # type: ignore[no-untyped-def]
        verdict = real(**kwargs)
        h.engage("global")
        return verdict

    h.rig.gate.decide = decide_then_kill  # type: ignore[method-assign]
    result = _run(h, "/v1/embeddings", EMBED)
    assert result.status == 503 and h.provider.open_calls == 0
    assert h.budget.spent > 0 and h.budget.net == 0 and len(h.budget.refund_calls) == 1
    assert [r.detail["cause"] for r in h.records] == ["recheck"]


def test_embeddings_decide_is_called_before_admit_in_the_source() -> None:
    source = inspect.getsource(openai_misc.EmbeddingsSurface.handle)
    calls = [
        (n.lineno, getattr(n.func, "attr", getattr(n.func, "id", "")))
        for n in ast.walk(ast.parse(source.lstrip()))
        if isinstance(n, ast.Call)
    ]
    lines = {name: line for line, name in reversed(sorted(calls))}
    assert lines["embeddings_gate"] < lines["_admit"] < lines["_budget"]


# -- moderations -----------------------------------------------------------------------------


@pytest.mark.parametrize("scope", ["global", f"org:{ORG}"])
def test_moderations_honour_global_and_org_kills(scope: str) -> None:
    h = Harness()
    h.engage(scope)
    _refused(h, _run(h, "/v1/moderations", MODERATE))


def test_moderations_ignore_model_scoped_kills_and_other_orgs() -> None:
    h = Harness()
    h.engage("model:text-moderation", f"om:{ORG}:text-moderation", "org:other")
    assert _run(h, "/v1/moderations", MODERATE).status == 200


def test_a_stale_feed_refuses_moderations() -> None:
    h = Harness()
    h.rig.mono.now += 60.0
    result = _run(h, "/v1/moderations", MODERATE)
    assert result.status == 503 and result.code() == "kill_switch_unavailable"


# -- MCP -------------------------------------------------------------------------------------


def _mcp_harness(**kwargs: object) -> tuple[Harness, Broker]:
    broker = Broker()
    deps = mcp_deps(broker, Control([server_row("search")]))
    return Harness(mcp=deps, **kwargs), broker  # type: ignore[arg-type]


MCP_LIST = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}


@pytest.mark.parametrize("scope", ["global", f"org:{ORG}"])
def test_mcp_honours_global_and_org_kills(scope: str) -> None:
    h, broker = _mcp_harness()
    h.engage(scope)
    result = _run(h, "/v1/mcp/search", MCP_LIST)
    assert result.status == 503
    assert int(result.headers["retry-after"]) >= 1 and result.headers["x-should-retry"] == "false"
    assert broker.requests == [] and h.budget.net == 0
    assert json.loads(result.body)["error"]["data"]["code"] == "kill_switch_engaged"
    assert len(h.records) == 1 and h.records[0].detail["cause"] in {"global", "org"}


def test_mcp_for_another_org_is_unaffected() -> None:
    h, broker = _mcp_harness()
    h.engage("org:other")
    assert _run(h, "/v1/mcp/search", MCP_LIST).status == 200
    assert len(broker.requests) == 1


# -- unwired defaults and the route sweep ----------------------------------------------------


@pytest.mark.real_incident_defaults
@pytest.mark.parametrize(
    ("path", "body"),
    [
        ("/v1/chat/completions", {"model": "m", "messages": [{"role": "user", "content": "x"}]}),
        ("/v1/embeddings", EMBED),
        ("/v1/moderations", MODERATE),
    ],
)
def test_an_unwired_route_refuses_with_cause_unwired(path: str, body: dict[str, object]) -> None:
    import httpx

    from gateway_v2.detect.holdback import scan as holdback_scan
    from gateway_v2.edge.app import build_app
    from gateway_v2.egress.output_guard import MinimalOutputResolver
    from tests.edge._chat_lifecycle_fixtures import (
        AdmittingAdmission,
        AdmittingBudget,
        ResolvingIdentity,
        warm_snapshot_source,
    )
    from tests.edge._lgw29_app import CountingProvider, _Clock, _Metrics, contract

    provider = CountingProvider(VECTOR, None)
    app = build_app(
        contract=contract(),
        provider=provider,
        scanner=holdback_scan,  # type: ignore[arg-type]
        resolver=MinimalOutputResolver(),
        metrics_registry=_Metrics(),
        clock=_Clock(),  # type: ignore[arg-type]
        identity=ResolvingIdentity(),
        snapshot_source=warm_snapshot_source(),
        admission=AdmittingAdmission(),
        quota=AdmittingBudget(),
    )

    async def go() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)  # type: ignore[arg-type]
        async with httpx.AsyncClient(transport=transport, base_url="http://x.test") as client:
            return await client.post(
                path, content=json.dumps(body).encode(), headers={"authorization": "Bearer t"}
            )

    response = asyncio.run(go())
    assert response.status_code == 503 and provider.open_calls == 0
    assert response.json()["error"]["code"] == "kill_switch_unavailable"


def test_every_post_route_is_classified() -> None:
    """Run_lifecycle, the 6.4b gates or a declared placeholder; GET /v1/models* is exempt."""
    from gateway_v2.edge.mcp_deps import McpDeps  # noqa: F401
    from tests.edge._lgw29_app import Harness as H

    h = H()
    chat = _chat_route(h)
    misc = _misc_route(h)
    exact, prefix = build_routes(chat, misc, None, mcp_deps(Broker(), Control([server_row("s")])))
    covered_by_lifecycle = {"/v1/chat/completions", "/v1/completions", "/v1/responses"}
    covered_by_misc_gate = {"/v1/embeddings", "/v1/moderations"}
    placeholders = {"/v1/rag/query", "/v1/vector/query"}
    for (method, path), handler in {**exact, **prefix}.items():
        if method == "GET" and path.startswith("/v1/models"):
            continue
        if method != "POST":
            continue
        classified = (
            path in covered_by_lifecycle
            or path in covered_by_misc_gate
            or path in placeholders
            or path.startswith("/v1/mcp")
        )
        assert classified, f"unclassified POST route {path}"
        del handler
    assert exact[("POST", "/v1/completions")] == exact[("POST", "/v1/chat/completions")]
    assert {
        p for (m, p) in exact if m == "POST"
    } >= covered_by_lifecycle | covered_by_misc_gate | placeholders


def _chat_route(h: Harness):  # type: ignore[no-untyped-def]
    from gateway_v2.edge.incident_passthrough import PassThroughIncidentGate
    from gateway_v2.edge.inflight_source import DevNoControlSource
    from gateway_v2.edge.routes import ChatRoute

    del h
    import importlib

    app_mod = importlib.import_module("gateway_v2.edge.app")
    from gateway_v2.detect.holdback import scan as holdback_scan
    from gateway_v2.edge.executor import ScanExecutor
    from gateway_v2.egress.output_guard import MinimalOutputResolver
    from gateway_v2.runtime.holdback_config import load_holdback_config
    from tests.edge._lgw29_app import _Clock, contract
    from tests.edge._org_limits import org_limits_for

    cfg, _ = load_holdback_config()
    route = app_mod._build_chat_route(
        contract=contract(),
        org_limits=org_limits_for(contract()),
        provider=None,
        scanner=holdback_scan,
        resolver=MinimalOutputResolver(),  # type: ignore[arg-type]
        cfg=cfg,
        clock=_Clock(),
        scan_executor=ScanExecutor(contract=contract()),
        active_streams=None,
        control=DevNoControlSource(),
        error_scanner=None,
        input_detector=None,
        identity=None,
        snapshot_source=None,
        admission=None,
        quota=None,
        exports=None,
        incident_gate=PassThroughIncidentGate(),
    )
    assert isinstance(route, ChatRoute)
    return route


def _misc_route(h: Harness):  # type: ignore[no-untyped-def]
    import importlib

    from gateway_v2.edge.incident_passthrough import PassThroughIncidentGate
    from gateway_v2.egress.output_guard import MinimalOutputResolver
    from tests.edge._lgw29_app import _Clock, contract

    del h
    app_mod = importlib.import_module("gateway_v2.edge.app")
    from tests.edge._org_limits import org_limits_for

    return app_mod._build_misc_route(
        contract=contract(),
        org_limits=org_limits_for(contract()),
        provider=None,
        resolver=MinimalOutputResolver(),
        clock=_Clock(),  # type: ignore[arg-type]
        input_detector=None,
        identity=None,
        snapshot_source=None,
        admission=None,
        quota=None,
        exports=None,
        catalog=None,
        incident_gate=PassThroughIncidentGate(),
    )
