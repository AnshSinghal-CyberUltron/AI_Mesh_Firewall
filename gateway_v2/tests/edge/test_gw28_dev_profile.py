"""GW28 profiles: dev routing wiring (T-edge-2/7, T-log-1) and the production factory (T-life-1)."""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import httpx
import pytest

from gateway_v2.dispatch.openai_upstream import OpenAIUpstreamProvider, httpx_client_factory
from gateway_v2.dispatch.routed_provider import RoutedProvider
from gateway_v2.dispatch.routing_types import RoutingConfigError, RoutingSnapshot, RoutingTable
from gateway_v2.edge import serve
from gateway_v2.edge.production_profile import RoutedBundle, build_routed_provider
from gateway_v2.edge.routing_boot import bounds_line
from gateway_v2.edge.wire.sse import SSEDecoder
from gateway_v2.runtime.kinds import PoolKind
from tests.dispatch._gw28_fakes import FakeClient, Timeline, cand, cset
from tests.edge import _gw28_app as H

EXAMPLE_PLAN = Path(__file__).resolve().parents[2] / "dev" / "plan.example.json"
KEY = "sk-dev-gw28"
_REAL_CLIENT = httpx.AsyncClient  # captured before any test patches httpx.AsyncClient
_C = H.contract(p99_ms=1.0)  # first_event 1.5 s, max_stream_duration 15 s


def _sse(text: str) -> bytes:
    chunk = {"choices": [{"index": 0, "delta": {"role": "assistant", "content": text}}]}
    return f"data: {json.dumps(chunk)}\n\ndata: [DONE]\n\n".encode()


def _boot(monkeypatch: pytest.MonkeyPatch, delay_s: float, log: list[str] | None = None):  # type: ignore[no-untyped-def]
    """The dev-profile app with a real OpenAIUpstreamProvider over an in-process silent upstream."""
    seen: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        await asyncio.sleep(delay_s)
        return httpx.Response(
            200, headers={"content-type": "text/event-stream"}, content=_sse("from-upstream")
        )

    real = httpx.AsyncClient
    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw)
    )
    env = {
        "AMF_DEV_PROFILE": "1",
        "AMF_DEV_API_KEY": KEY,
        "AMF_DEV_PLAN_PATH": str(EXAMPLE_PLAN),
        "AMF_DEV_MODELS": "echo-1",
        "AMF_DEV_Q_SAFE": "500",
        "AMF_DEV_AUDIT_DRAIN_RATE_PER_S": "1000",
        "AMF_DEV_AUDIT_BYTES_PER_RECORD": "2765",
        "AMF_DEV_BUDGET": "unlimited",
        "AMF_DEV_PROVIDER_URL": "http://upstream.test/v1",
        "AMF_DEV_PROVIDER_API_KEY": "sk-up",
    }
    deps, logs = serve.build_boot_dependencies(contract=_C, env=env)
    if log is not None:
        log.extend(logs)
    return deps, serve._assemble_app(deps), seen


async def _post(app: object, path: str, payload: dict[str, object]) -> tuple[int, bytes]:
    transport = httpx.ASGITransport(app=app)  # type: ignore[arg-type]
    async with _REAL_CLIENT(transport=transport, base_url="http://t.test") as cl:
        r = await cl.post(
            path,
            content=json.dumps(payload),
            headers={"authorization": f"Bearer {KEY}", "content-type": "application/json"},
        )
        return r.status_code, r.content


def _chat(stream: bool) -> dict[str, object]:
    return {
        "model": "echo-1",
        "stream": stream,
        "messages": [{"role": "user", "content": "hello"}],
    }


def test_dev_profile_builds_one_plain_provider_and_one_passthrough_routing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    deps, _app, _seen = _boot(monkeypatch, 0.0)
    provider, routing = deps["provider"], deps["routing"]
    assert isinstance(provider, OpenAIUpstreamProvider) and isinstance(routing, RoutedProvider)
    assert provider.connections == _C.pool_size(PoolKind.PROVIDER)
    # the routed candidate's client IS the same instance (one pool, no second client)
    assert routing._clients["dev"] is provider


def test_dev_profile_without_upstream_url_has_no_routing(monkeypatch: pytest.MonkeyPatch) -> None:
    env = {
        "AMF_DEV_PROFILE": "1",
        "AMF_DEV_API_KEY": KEY,
        "AMF_DEV_PLAN_PATH": str(EXAMPLE_PLAN),
        "AMF_DEV_MODELS": "echo-1",
        "AMF_DEV_Q_SAFE": "500",
        "AMF_DEV_AUDIT_DRAIN_RATE_PER_S": "1000",
        "AMF_DEV_AUDIT_BYTES_PER_RECORD": "2765",
        "AMF_DEV_BUDGET": "unlimited",
    }
    deps, _logs = serve.build_boot_dependencies(contract=_C, env=env)
    assert deps["routing"] is None


def test_t_log_1_dev_boot_logs_the_four_bounds(monkeypatch: pytest.MonkeyPatch) -> None:
    logs: list[str] = []
    _boot(monkeypatch, 0.0, logs)
    line = bounds_line(_C)
    assert any(line in entry for entry in logs)
    for value in (
        _C.upstream_first_event_timeout_s(),
        _C.upstream_gap_timeout_s(),
        _C.upstream_total_timeout_s(),
        _C.routing_pre_content_budget_s(_C.routing_max_attempts()),
    ):
        assert str(value) in line


@pytest.mark.parametrize("stream", [False, True])
def test_t_edge_2_chat_is_routed_and_succeeds(
    monkeypatch: pytest.MonkeyPatch, stream: bool
) -> None:
    calls: list[int] = []
    orig = RoutedProvider.open_routed

    def counting(self, request, ctx):  # type: ignore[no-untyped-def]
        calls.append(1)
        return orig(self, request, ctx)

    monkeypatch.setattr(RoutedProvider, "open_routed", counting)
    _deps, app, seen = _boot(monkeypatch, 0.0)
    status, body = asyncio.run(_post(app, "/v1/chat/completions", _chat(stream)))
    assert status == 200 and b"from-upstream" in body
    assert calls == [1] and seen == ["/v1/chat/completions"]


def test_t_edge_2_non_chat_surface_never_touches_open_routed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[int] = []
    orig = RoutedProvider.open_routed

    def counting(self, request, ctx):  # type: ignore[no-untyped-def]
        calls.append(1)
        return orig(self, request, ctx)

    monkeypatch.setattr(RoutedProvider, "open_routed", counting)
    _deps, app, _seen = _boot(monkeypatch, 0.0)
    asyncio.run(_post(app, "/v1/embeddings", {"model": "echo-1", "input": "hello"}))
    asyncio.run(_post(app, "/v1/moderations", {"input": "hello"}))
    assert calls == []


def test_t_edge_7_chat_is_cut_at_first_event_bound_non_chat_is_not(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    silence = _C.upstream_first_event_timeout_s() + 1.0  # longer than the chat bound, << 15 s
    _deps, app, _seen = _boot(monkeypatch, silence)
    t0 = time.perf_counter()
    status, body = asyncio.run(_post(app, "/v1/chat/completions", _chat(False)))
    elapsed = time.perf_counter() - t0
    assert status == 504 and json.loads(body)["error"]["code"] == "upstream_timeout"
    assert elapsed < silence  # cut at first_event_s, not when the upstream finally answered
    # the provider the non-chat surfaces use is the same, unchanged client: its httpx timeout is
    # still max_stream_duration_s (no application bound applies off the routed chat path).
    provider = _deps["provider"]
    assert isinstance(provider, OpenAIUpstreamProvider)
    assert provider._client_factory().timeout.read == _C.max_stream_duration_s()  # noqa: SLF001


# --- production factory: T-life-1 -----------------------------------------------------------
def _factory(
    created: dict[str, OpenAIUpstreamProvider],
) -> object:
    def make(key: str, share: int) -> OpenAIUpstreamProvider:
        p = OpenAIUpstreamProvider(
            base_url=f"http://{key}.test/v1",
            decoder_factory=SSEDecoder,
            client_factory=httpx_client_factory(api_key="k", timeout_s=1.0, connections=share),
            connections=share,
        )
        created[key] = p
        return p

    return make


def _table(*keys: str) -> RoutingTable:
    cands = [cand(f"c{i}", provider=k) for i, k in enumerate(keys)]
    return RoutingTable.build({"org": cset(*cands)})


def test_t_life_1_closers_only_for_factory_built_and_share_arithmetic() -> None:
    created: dict[str, OpenAIUpstreamProvider] = {}
    owned = FakeClient("shared", [], Timeline())
    logs: list[str] = []
    contract = H.contract()
    pool = contract.pool_size(PoolKind.PROVIDER)
    assert pool >= 3
    bundle = build_routed_provider(
        contract,
        _table("p1", "p2", "p3", "mine"),
        _factory(created),  # type: ignore[arg-type]
        lambda: RoutingSnapshot(),
        owned_elsewhere={"mine": owned},
        log=logs.append,
    )
    assert isinstance(bundle, RoutedBundle) and len(bundle.closers) == 3
    share = contract.provider_pool_share(3)
    assert {p.connections for p in created.values()} == {share} and set(created) == {
        "p1",
        "p2",
        "p3",
    }
    assert 3 * share <= pool
    assert logs == [bounds_line(contract)]  # T-log-1, production path
    # every factory-built client is closed once; the owned client is never touched
    asyncio.run(_run_closers(bundle))
    assert owned.tl.live == 0


async def _run_closers(bundle: RoutedBundle) -> None:
    for closer in bundle.closers:
        await closer()


def test_t_life_1_share_is_never_over_budget_and_p_plus_1_refuses() -> None:
    contract = H.contract()
    pool = contract.pool_size(PoolKind.PROVIDER)
    for n in range(1, pool + 1):
        assert n * contract.provider_pool_share(n) <= pool
    keys = [f"k{i}" for i in range(pool + 1)]
    with pytest.raises(RoutingConfigError):
        build_routed_provider(
            contract,
            _table(*keys),
            _factory({}),  # type: ignore[arg-type]
            lambda: RoutingSnapshot(),
            log=lambda _l: None,
        )


def test_over_budget_factory_refuses() -> None:
    contract = H.contract()

    def greedy(key: str, share: int) -> OpenAIUpstreamProvider:
        return OpenAIUpstreamProvider(
            base_url="http://x/v1",
            decoder_factory=SSEDecoder,
            client_factory=lambda: httpx.AsyncClient(),
            connections=contract.pool_size(PoolKind.PROVIDER) + 1,
        )

    with pytest.raises(RoutingConfigError):
        build_routed_provider(
            contract, _table("a"), greedy, lambda: RoutingSnapshot(), log=lambda _l: None
        )
