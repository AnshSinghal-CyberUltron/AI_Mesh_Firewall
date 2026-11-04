"""GW32 R1 + Property 1: identity before any MCP method, zero downstream calls on refusal.

Also pins the MCP-only 401 challenge, the token branch, and the `resolve_hash` seam.
"""

from __future__ import annotations

import asyncio
import json
import random

import pytest

httpx = pytest.importorskip("httpx")

from gateway_v2.detect.holdback import scan as holdback_scan  # noqa: E402
from gateway_v2.domain import posture  # noqa: E402
from gateway_v2.domain.identity import Principal  # noqa: E402
from gateway_v2.domain.state import StoreDataUnavailable  # noqa: E402
from gateway_v2.edge.app import build_app  # noqa: E402
from gateway_v2.edge.identity_resolver import (  # noqa: E402
    IdentityOutcome,
    TenantIdentity,
)
from gateway_v2.edge.mcp import McpRoute  # noqa: E402
from gateway_v2.edge.mcp_identity import TOKEN_PREFIX  # noqa: E402
from gateway_v2.edge.wire import jsonrpc  # noqa: E402
from gateway_v2.egress.output_guard import MinimalOutputResolver  # noqa: E402
from tests.edge._chat_lifecycle_fixtures import (  # noqa: E402
    AdmittingAdmission,
    AdmittingBudget,
    warm_snapshot_source,
)
from tests.edge._mcp_fixtures import ORG, Broker, Control, call, mcp_deps, server_row  # noqa: E402
from tests.edge.test_responses_route import _Clock, _contract, _Metrics, _none  # noqa: E402

ITERATIONS = 200
KEY = "k" * 48
_PRINCIPAL = Principal("kid", ORG, 1.0, 1.0, 0)
_IDENTITY = TenantIdentity(ORG, _PRINCIPAL)


class Resolver:
    """Names a key by presence of any bearer; counts calls; can be told to fail."""

    def __init__(self, mode: str = "ok", tokens: dict[str, str] | None = None) -> None:
        self.mode, self.calls, self.hash_calls = mode, 0, 0
        self.tokens = tokens or {}

    async def resolve(self, scope: object) -> TenantIdentity | None:
        return (await self.resolve_outcome(scope)).identity

    async def resolve_outcome(self, scope: object) -> IdentityOutcome:
        self.calls += 1
        headers = dict(scope["headers"])  # type: ignore[index]
        if b"authorization" not in headers or self.mode == "bad":
            return IdentityOutcome.invalid()
        if self.mode == "down":
            return IdentityOutcome.unverified()
        return IdentityOutcome.resolved(_IDENTITY)

    async def resolve_hash(self, key_hash: str) -> IdentityOutcome:
        self.hash_calls += 1
        return IdentityOutcome.resolved(_IDENTITY)


class Tokens:
    def __init__(self, known: dict[str, str], fail: bool = False) -> None:
        self.known, self.fail, self.calls = known, fail, 0

    async def resolve(self, token: str) -> str | None:
        self.calls += 1
        if self.fail:
            raise StoreDataUnavailable("down")
        return self.known.get(token)


def _app(
    resolver: Resolver, tokens: Tokens | None = None, public: str = ""
) -> tuple[object, Broker, Control]:
    broker = Broker()
    control = Control([server_row("search")])
    deps = mcp_deps(broker, control)
    from dataclasses import replace

    deps = replace(deps, token_store=tokens, public_base=public)
    app = build_app(
        contract=_contract(),
        provider=None,
        scanner=holdback_scan,  # type: ignore[arg-type]
        resolver=MinimalOutputResolver(),
        metrics_registry=_Metrics(),
        clock=_Clock(),
        input_detector=_none,
        error_scanner=_none,
        identity=resolver,
        snapshot_source=warm_snapshot_source(),
        admission=AdmittingAdmission(),
        quota=AdmittingBudget(),
        mcp=deps,
    )
    return app, broker, control


async def _post(app: object, body: bytes, auth: str | None) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)  # type: ignore[arg-type]
    async with httpx.AsyncClient(transport=transport, base_url="http://mcp.test") as client:
        headers = {"content-type": "application/json"}
        if auth is not None:
            headers["authorization"] = auth
        return await client.post("/v1/mcp/search", content=body, headers=headers)


_BODIES = [
    json.dumps(call("initialize", {"protocolVersion": "2025-03-26"})).encode(),
    json.dumps(call("ping")).encode(),
    json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}).encode(),
    json.dumps(call("tools/list")).encode(),
    json.dumps(call("tools/call", {"name": "find", "arguments": {}})).encode(),
    json.dumps(call("no/such")).encode(),
    b"{not json",
    b"",
    b"x" * (11 * 1024 * 1024),
    b'{"jsonrpc":"2.0","id":4,"result":{}}',
]


def _assert_refused(resp: httpx.Response, broker: Broker, control: Control, status: int) -> None:
    assert resp.status_code == status
    assert broker.requests == [] and control.calls == 0
    assert "mcp-session-id" not in resp.headers


# Feature: gw32-mcp-transport, Property 1: identity before any method
@pytest.mark.parametrize(
    ("mode", "auth", "status"),
    [("ok", None, 401), ("bad", f"Bearer {KEY}", 401), ("down", f"Bearer {KEY}", 503)],
)
def test_property_1_every_method_refused_before_any_downstream_call(
    mode: str, auth: str | None, status: int
) -> None:
    rng = random.Random(101)
    for _ in range(ITERATIONS):
        app, broker, control = _app(Resolver(mode))
        body = rng.choice(_BODIES)
        resp = asyncio.run(_post(app, body, auth))
        _assert_refused(resp, broker, control, status)


def test_unknown_token_and_token_store_down() -> None:
    token = f"{TOKEN_PREFIX}{'a' * 43}"
    app, broker, control = _app(Resolver(), Tokens({}))
    _assert_refused(asyncio.run(_post(app, _BODIES[0], f"Bearer {token}")), broker, control, 401)
    app, broker, control = _app(Resolver(), Tokens({}, fail=True))
    _assert_refused(asyncio.run(_post(app, _BODIES[0], f"Bearer {token}")), broker, control, 503)
    app, broker, control = _app(Resolver(), None)  # no OAuth server configured
    _assert_refused(asyncio.run(_post(app, _BODIES[0], f"Bearer {token}")), broker, control, 401)


def test_negative_control_local_before_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    """Restore the pre-GW32 order (answer locally first): Property 1 must then fail."""

    async def old_serve(self: McpRoute, receive, send, ctx) -> None:  # type: ignore[no-untyped-def]
        message = jsonrpc.parse_message(_BODIES[1])
        assert not isinstance(message, jsonrpc.ProtocolError)
        await self._local(send, message)

    monkeypatch.setattr(McpRoute, "_serve", old_serve)
    app, broker, control = _app(Resolver("bad"))
    resp = asyncio.run(_post(app, _BODIES[1], f"Bearer {KEY}"))
    with pytest.raises(AssertionError):
        _assert_refused(resp, broker, control, 401)


def test_valid_key_still_works_and_token_resolves_through_resolve_hash() -> None:
    resolver = Resolver()
    app, _, _ = _app(resolver, Tokens({f"{TOKEN_PREFIX}ok": "h" * 64}))
    assert asyncio.run(_post(app, _BODIES[1], f"Bearer {KEY}")).status_code == 200
    resp = asyncio.run(_post(app, _BODIES[1], f"Bearer {TOKEN_PREFIX}ok"))
    assert resp.status_code == 200 and resolver.hash_calls == 1


def test_mcp_401_has_exactly_one_challenge_and_chat_is_untouched() -> None:
    app, _, _ = _app(Resolver("bad"), public="https://gw.example.com")
    resp = asyncio.run(_post(app, _BODIES[1], f"Bearer {KEY}"))
    challenge = resp.headers.get_list("www-authenticate")
    assert challenge == [
        'Bearer resource_metadata="https://gw.example.com/.well-known/oauth-protected-resource"'
    ]
    app, _, _ = _app(Resolver("bad"))
    assert asyncio.run(_post(app, _BODIES[1], f"Bearer {KEY}")).headers.get_list(
        "www-authenticate"
    ) == ["Bearer"]


def test_refusal_codes_are_the_declared_ones() -> None:
    app, _, _ = _app(Resolver("down"))
    body = asyncio.run(_post(app, _BODIES[0], f"Bearer {KEY}")).json()
    assert body["error"]["data"]["code"] == posture.SHARED_STATE_UNAVAILABLE
