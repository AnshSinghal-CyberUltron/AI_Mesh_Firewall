"""GW32 / GW17b / GW17c are constructed by the boot path and reachable through the real app.

`build_dev_mcp` is the one production construction site. These tests go through it (not through
hand-built `McpDeps`) and then through `build_app`, so a service that is never wired shows up as
a failure here instead of as a silently inert feature.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import replace

import pytest

httpx = pytest.importorskip("httpx")
fakeredis = pytest.importorskip("fakeredis")

from gateway_v2.detect.holdback import scan as holdback_scan  # noqa: E402
from gateway_v2.domain.identity import Principal  # noqa: E402
from gateway_v2.edge.app import build_app  # noqa: E402
from gateway_v2.edge.dev_mcp import build_dev_mcp  # noqa: E402
from gateway_v2.edge.identity_resolver import IdentityOutcome, TenantIdentity  # noqa: E402
from gateway_v2.edge.publisher import Publisher  # noqa: E402
from gateway_v2.egress.output_guard import MinimalOutputResolver  # noqa: E402
from tests.edge._chat_lifecycle_fixtures import (  # noqa: E402
    AdmittingAdmission,
    AdmittingBudget,
    warm_snapshot_source,
)
from tests.edge._mcp_fixtures import ORG, Broker, Control, call, resolve, server_row  # noqa: E402
from tests.edge._publisher_fixture import build_publisher  # noqa: E402
from tests.edge.test_responses_route import _Clock, _contract, _Metrics, _none  # noqa: E402

KEY = "k" * 48
_ENV = {
    "AMF_DEV_MCP_BROKER_URL": "http://mcp-broker:8311",
    "AMF_DEV_MCP_BROKER_KEY": "broker-secret-value",
    "AMF_DEV_CONTROL_URL": "http://control:8100",
    "AMF_DEV_GATEWAY_INTERNAL_KEY": "internal-secret-value",
    "AMF_DEV_MCP_ORG_MAP": f"{ORG}=acme",
}
_OAUTH_ENV = {
    "AMF_GATEWAY_PUBLIC_URL": "https://gw.example.com",
    "AMF_OAUTH_HASH_KEY": "h" * 40,
}


class Identity:
    """A resolver with `resolve_hash`, like the production `CachingIdentityResolver`."""

    def __init__(self, principal: Principal) -> None:
        self._identity = TenantIdentity(ORG, principal)

    async def resolve(self, scope: object) -> TenantIdentity | None:
        return (await self.resolve_outcome(scope)).identity

    async def resolve_outcome(self, scope: object) -> IdentityOutcome:
        headers = dict(scope["headers"])  # type: ignore[index]
        if b"authorization" not in headers:
            return IdentityOutcome.invalid()
        return IdentityOutcome.resolved(self._identity)

    async def resolve_hash(self, key_hash: str) -> IdentityOutcome:
        if key_hash != hashlib.sha256(KEY.encode()).hexdigest():
            return IdentityOutcome.invalid()
        return IdentityOutcome.resolved(self._identity)


def _boot(env: dict[str, str], principal: Principal, *, redis: bool = True):  # type: ignore[no-untyped-def]
    client = fakeredis.FakeAsyncRedis(server=fakeredis.FakeServer()) if redis else None
    deps, _log = build_dev_mcp(
        {**_ENV, **env}, _contract(), redis_client=client, clock=lambda: 0.0,
        identity=Identity(principal),
    )
    assert deps is not None
    broker = Broker()
    tools = {"known_tools": ["a", "b"], "enabled_tools": ["a", "b"], "disabled_tools": []}
    deps = replace(
        deps,
        registry=replace_registry(Control([server_row("s")], tools)),
        broker=replace_broker(broker),
        dns=resolve,
    )
    app = build_app(
        contract=_contract(), provider=None, scanner=holdback_scan,  # type: ignore[arg-type]
        resolver=MinimalOutputResolver(), metrics_registry=_Metrics(), clock=_Clock(),
        input_detector=_none, error_scanner=_none, identity=Identity(principal),
        snapshot_source=warm_snapshot_source(), admission=AdmittingAdmission(),
        quota=AdmittingBudget(), mcp=deps,
    )
    return deps, app, broker


def replace_registry(control: Control):  # type: ignore[no-untyped-def]
    from tests.edge._mcp_fixtures import mcp_deps

    return mcp_deps(Broker(), control).registry


def replace_broker(broker: Broker):  # type: ignore[no-untyped-def]
    from tests.edge._mcp_fixtures import mcp_deps

    return mcp_deps(broker, Control([])).broker


async def _request(app: object, method: str, path: str, **kw: object) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)  # type: ignore[arg-type]
    async with httpx.AsyncClient(transport=transport, base_url="http://gw.example.com") as c:
        return await c.request(method, path, **kw)  # type: ignore[arg-type]


def _post(app: object, body: object) -> httpx.Response:
    return asyncio.run(_request(
        app, "POST", "/v1/mcp/s", content=json.dumps(body).encode(),
        headers={"authorization": f"Bearer {KEY}", "content-type": "application/json"},
    ))


def test_boot_constructs_every_service() -> None:
    deps, _, _ = _boot(
        {**_OAUTH_ENV, "AMF_DEV_CONTROL_URL": "http://127.0.0.1:8100"},
        Principal("kid", ORG, 0.0, 0.0, 1),
    )
    assert deps.sessions is not None and deps.cap_table is not None
    assert deps.credentials is not None and deps.policy_metrics is not None
    assert deps.oauth is not None and deps.token_store is not None
    assert deps.transport_bounds is not None and deps.public_base == "https://gw.example.com"


def test_plaintext_control_url_leaves_credentials_unset() -> None:
    deps, _, _ = _boot({}, Principal("kid", ORG, 0.0, 0.0, 1))
    assert deps.credentials is None  # an upstream secret never crosses a plaintext link


def test_oauth_discovery_is_live_not_a_503() -> None:
    _, app, _ = _boot(_OAUTH_ENV, Principal("kid", ORG, 0.0, 0.0, 1))
    resp = asyncio.run(_request(app, "GET", "/.well-known/oauth-authorization-server"))
    assert resp.status_code == 200
    assert resp.json()["issuer"].startswith("https://gw.example.com")


def test_oauth_unset_answers_a_declared_503() -> None:
    _, app, _ = _boot({}, Principal("kid", ORG, 0.0, 0.0, 1))
    resp = asyncio.run(_request(app, "GET", "/.well-known/oauth-authorization-server"))
    assert resp.status_code == 503


def test_partial_oauth_configuration_refuses_to_boot() -> None:
    from gateway_v2.edge.dev_components import DevProfileError

    with pytest.raises(DevProfileError, match="partly configured"):
        _boot({"AMF_OAUTH_HASH_KEY": "h" * 40}, Principal("kid", ORG, 0.0, 0.0, 1))


def test_session_is_issued_on_initialize() -> None:
    _, app, _ = _boot({}, Principal("kid", ORG, 0.0, 0.0, 1))
    resp = _post(app, call("initialize", {"protocolVersion": "2025-06-18"}))
    assert resp.status_code == 200
    assert resp.headers.get("mcp-session-id")


def test_allow_list_is_enforced_with_boot_built_services() -> None:
    deps, app, broker = _boot({}, Principal("kid", ORG, 0.0, 0.0, 1, frozenset({"a"}), 0))
    resp = _post(app, call("tools/call", {"name": "b", "arguments": {}}))
    assert resp.status_code == 403 and broker.requests == []
    assert deps.policy_metrics is not None and deps.policy_metrics.snapshot().tool_denied_total == 1


def test_capped_key_is_admitted_by_the_boot_built_cap_table() -> None:
    # With no cap table this would be the fail-closed refusal; the boot path supplies one.
    _, app, _ = _boot({}, Principal("kid", ORG, 0.0, 0.0, 1, frozenset(), 5))
    resp = _post(app, call("tools/call", {"name": "a", "arguments": {}}))
    assert resp.status_code == 200, resp.text


def test_capped_key_is_refused_when_no_store_exists() -> None:
    deps, app, _ = _boot({}, Principal("kid", ORG, 0.0, 0.0, 1, frozenset(), 5), redis=False)
    assert deps.cap_table is None and deps.sessions is None
    resp = _post(app, call("tools/call", {"name": "a", "arguments": {}}))
    assert resp.status_code >= 400


def test_publisher_exports_policy_series_and_enforcement_version() -> None:
    deps, _, _ = _boot({}, Principal("kid", ORG, 0.0, 0.0, 1))
    publisher: Publisher = build_publisher(_contract(), lambda: 0.0)
    before = publisher.SERIES_COUNT
    assert deps.policy_metrics is not None
    publisher.attach_mcp_policy(deps.policy_metrics)
    deps.policy_metrics.observe_cap_refused()
    body = publisher.exposition()
    assert publisher.SERIES_COUNT == before + 7
    assert "amf_mcp_policy_enforcement_version 1" in body
    assert "amf_mcp_cap_refused_total 1" in body
    assert "amf_mcp_fail_open_total 0" in body


def test_credential_cache_is_dropped_when_the_registry_version_changes() -> None:
    from gateway_v2.dispatch.mcp_credentials import CredentialCache
    from gateway_v2.edge.mcp_boot import _dropper

    cache = CredentialCache(slots=4, ttl_s=100.0, clock=lambda: 0.0)
    assert cache.keys() == frozenset()
    from gateway_v2.domain.mcp_credential import UpstreamCredential

    sample = UpstreamCredential(kind="bearer", token="s" * 12, version=1)
    cache.put("acme", "s", "bearer", sample)
    cache.put("acme", "t", "bearer", sample)
    cache.put("other", "s", "bearer", sample)
    _dropper(cache)("acme", "s")
    assert cache.keys() == frozenset({("acme", "t"), ("other", "s")})
    _dropper(cache)("acme", "")  # an org-wide bump
    assert cache.keys() == frozenset({("other", "s")})
