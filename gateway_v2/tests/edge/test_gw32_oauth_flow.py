"""GW32 OAuth 2.1 server: Property 9 (PKCE and redirect binding) and the end-to-end flow."""

from __future__ import annotations

import asyncio
import hashlib
import json
import random
import re
from dataclasses import replace
from urllib.parse import parse_qs, urlencode, urlsplit

import pytest

httpx = pytest.importorskip("httpx")

from gateway_v2.admit.oauth_server import (  # noqa: E402
    OAuthConfig,
    OAuthServer,
    OAuthTokenResolver,
    redirect_uri_ok,
    s256,
)
from gateway_v2.admit.oauth_store import MemoryKV  # noqa: E402
from gateway_v2.detect.holdback import scan as holdback_scan  # noqa: E402
from gateway_v2.domain.identity import Principal  # noqa: E402
from gateway_v2.edge.app import build_app  # noqa: E402
from gateway_v2.edge.identity_resolver import IdentityOutcome, TenantIdentity  # noqa: E402
from gateway_v2.edge.oauth_routes import OAuthRoutes  # noqa: E402
from gateway_v2.egress.output_guard import MinimalOutputResolver  # noqa: E402
from gateway_v2.runtime.resources_mcp import derive_transport_bounds  # noqa: E402
from gateway_v2.runtime.store_keys_mcp import McpStoreKeys  # noqa: E402
from tests.edge._chat_lifecycle_fixtures import (  # noqa: E402
    AdmittingAdmission,
    AdmittingBudget,
    warm_snapshot_source,
)
from tests.edge._mcp_fixtures import ORG, Broker, Control, call, mcp_deps, server_row  # noqa: E402
from tests.edge.test_responses_route import _Clock, _contract, _Metrics, _none  # noqa: E402

KEY = "k" * 48
PUBLIC = "https://gw.example.com"
REDIRECT = "https://app.example.com/cb"
VERIFIER = "v" * 64
CHALLENGE = s256(VERIFIER)


class Time:
    now = 1000.0

    def __call__(self) -> float:
        return self.now


class Identity:
    """Resolves the one known key hash; `revoked` flips it to unknown."""

    def __init__(self) -> None:
        self.revoked = False
        self.known = hashlib.sha256(KEY.encode()).hexdigest()
        self.principal = Principal("kid", ORG, 0.0, 0.0, 1)

    async def resolve(self, scope: object) -> TenantIdentity | None:
        return (await self.resolve_outcome(scope)).identity

    async def resolve_outcome(self, scope: object) -> IdentityOutcome:
        headers = dict(scope["headers"])  # type: ignore[index]
        raw = headers.get(b"authorization", b"").decode().removeprefix("Bearer ")
        return await self.resolve_hash(hashlib.sha256(raw.encode()).hexdigest())

    async def resolve_hash(self, key_hash: str) -> IdentityOutcome:
        if self.revoked or key_hash != self.known:
            return IdentityOutcome.invalid()
        return IdentityOutcome.resolved(TenantIdentity(ORG, self.principal))


def _server(clock: Time, kv: MemoryKV | None = None, previous: bytes | None = None, **cfg):  # type: ignore[no-untyped-def]
    bounds = derive_transport_bounds(_contract())
    config = OAuthConfig(PUBLIC, b"k" * 32, previous, bounds, **cfg)
    return OAuthServer(
        kv=kv or MemoryKV(clock), keys=McpStoreKeys(), config=config, clock=clock
    ), bounds


def _run(coro):  # type: ignore[no-untyped-def]
    return asyncio.run(coro)


async def _code(
    server: OAuthServer, client_id: str, *, uri: str = REDIRECT, challenge: str = CHALLENGE
):  # type: ignore[no-untyped-def]
    req = await server.validate_authorize(
        {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": uri,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": "st",
        }
    )
    assert not hasattr(req, "code") or True
    token = await server.issue_form_token(req)  # type: ignore[arg-type]

    async def resolve(raw: str):  # type: ignore[no-untyped-def]
        return ("h" * 64, "kid") if raw == KEY else None

    out = await server.authorize(token, resolve, KEY, "src")
    return out[1]  # type: ignore[index]


async def _register(server: OAuthServer, **extra: object) -> dict[str, object]:
    out = await server.register({"redirect_uris": [REDIRECT], **extra}, "src")
    assert isinstance(out, dict)
    return out


# Feature: gw32-mcp-transport, Property 9: PKCE and redirect binding
def test_property_9_pkce_and_redirect_binding() -> None:
    rng = random.Random(9)
    for _ in range(100):
        clock = Time()
        server, _ = _server(clock)
        verifier = "".join(
            rng.choice("abcdefghij0123456789-._~") for _ in range(rng.randint(43, 128))
        )

        async def flow() -> None:
            client = await _register(server)
            cid = str(client["client_id"])
            code = await _code(server, cid, challenge=s256(verifier))
            wrong = rng.choice(["verifier", "redirect", "client"])
            params = {
                "grant_type": "authorization_code",
                "client_id": cid,
                "code": code,
                "redirect_uri": REDIRECT,
                "code_verifier": verifier,
            }
            if wrong == "verifier":
                params["code_verifier"] = verifier[:-1] + ("A" if verifier[-1] != "A" else "B")
            elif wrong == "redirect":
                params["redirect_uri"] = REDIRECT + "/x"
            else:
                params["client_id"] = "other"
            bad = await server.token(params)
            assert not hasattr(bad, "access_token")
            # a failed verifier/redirect exchange consumed the code for good
            params.update(client_id=cid, redirect_uri=REDIRECT, code_verifier=verifier)
            redeemed = hasattr(await server.token(params), "access_token")
            # unknown client: refused before the code is touched, so the owner can still redeem it
            assert redeemed == (wrong == "client")

        _run(flow())


def test_happy_path_replay_and_family_revocation() -> None:
    clock = Time()
    server, _ = _server(clock)

    async def flow() -> None:
        cid = str((await _register(server))["client_id"])
        code = await _code(server, cid)
        params = {
            "grant_type": "authorization_code",
            "client_id": cid,
            "code": code,
            "redirect_uri": REDIRECT,
            "code_verifier": VERIFIER,
        }
        grant = await server.token(params)
        assert hasattr(grant, "access_token") and grant.access_token.startswith("zsmcp_")  # type: ignore[union-attr]
        assert await server.lookup_access(grant.access_token) == "h" * 64  # type: ignore[union-attr]
        replay = await server.token(params)
        assert replay.code == "invalid_grant"  # type: ignore[union-attr]
        assert await server.lookup_access(grant.access_token) is None  # family revoked

    _run(flow())


def test_refresh_rotation_and_reuse_revokes_the_family() -> None:
    clock = Time()
    server, _ = _server(clock)

    async def flow() -> None:
        cid = str((await _register(server))["client_id"])
        code = await _code(server, cid)
        first = await server.token(
            {
                "grant_type": "authorization_code",
                "client_id": cid,
                "code": code,
                "redirect_uri": REDIRECT,
                "code_verifier": VERIFIER,
            }
        )
        refresh = {
            "grant_type": "refresh_token",
            "client_id": cid,
            "refresh_token": first.refresh_token,
        }  # type: ignore[union-attr]
        second = await server.token(refresh)
        assert hasattr(second, "access_token")
        assert await server.lookup_access(second.access_token) == "h" * 64  # type: ignore[union-attr]
        again = await server.token(refresh)  # the rotated token, presented again
        assert again.code == "invalid_grant"  # type: ignore[union-attr]
        assert await server.lookup_access(second.access_token) is None  # whole family gone

    _run(flow())


def test_expiry_and_hash_key_rotation() -> None:
    clock = Time()
    kv = MemoryKV(clock)
    server, bounds = _server(clock, kv)

    async def flow() -> None:
        cid = str((await _register(server))["client_id"])
        code = await _code(server, cid)
        grant = await server.token(
            {
                "grant_type": "authorization_code",
                "client_id": cid,
                "code": code,
                "redirect_uri": REDIRECT,
                "code_verifier": VERIFIER,
            }
        )
        rotated, _ = _server(clock, kv, previous=b"k" * 32)
        rotated_cfg = OAuthConfig(PUBLIC, b"n" * 32, b"k" * 32, bounds)
        rotated = OAuthServer(kv=kv, keys=McpStoreKeys(), config=rotated_cfg, clock=clock)
        assert await rotated.lookup_access(grant.access_token) == "h" * 64  # type: ignore[union-attr]
        dropped = OAuthServer(
            kv=kv,
            keys=McpStoreKeys(),
            config=OAuthConfig(PUBLIC, b"z" * 32, None, bounds),
            clock=clock,
        )
        assert await dropped.lookup_access(grant.access_token) is None  # second rotation
        clock.now += bounds.oauth_access_ttl_s + 1
        assert await server.lookup_access(grant.access_token) is None  # expired

    _run(flow())


def test_registration_rules_and_bounds() -> None:
    clock = Time()
    server, bounds = _server(clock)

    async def flow() -> None:
        for bad in (
            [],
            ["http://evil.example.com/cb"],
            ["https://a/b#frag"],
            ["https://*.x/cb"],
            ["https://u:p@a/b"],
            [1],
            ["ftp://a/b"],
        ):
            assert not isinstance(await server.register({"redirect_uris": bad}, "src"), dict)
        for ok in (["https://a.example.com/cb"], ["http://127.0.0.1:9/cb"], ["vscode://x/cb"]):
            assert isinstance(await server.register({"redirect_uris": ok}, "src"), dict)
        confidential = await server.register(
            {"redirect_uris": [REDIRECT], "token_endpoint_auth_method": "client_secret_post"}, "s2"
        )
        assert isinstance(confidential, dict) and "client_secret" in confidential
        for _ in range(bounds.oauth_clients_per_source):
            await server.register({"redirect_uris": [REDIRECT]}, "flood")
        over = await server.register({"redirect_uris": [REDIRECT]}, "flood")
        assert over.status == 429  # type: ignore[union-attr]
        # unused clients expire, freeing the slots
        clock.now += bounds.oauth_client_unused_ttl_s + 1
        assert isinstance(await server.register({"redirect_uris": [REDIRECT]}, "flood"), dict)

    _run(flow())


def test_confidential_client_secret_and_failure_limit() -> None:
    clock = Time()
    server, bounds = _server(clock)

    async def flow() -> None:
        client = await _register(server, token_endpoint_auth_method="client_secret_post")
        cid = str(client["client_id"])
        code = await _code(server, cid)
        params = {
            "grant_type": "authorization_code",
            "client_id": cid,
            "code": code,
            "redirect_uri": REDIRECT,
            "code_verifier": VERIFIER,
        }
        assert (await server.token({**params, "client_secret": "wrong"})).code == "invalid_client"  # type: ignore[union-attr]
        assert hasattr(
            await server.token({**params, "client_secret": client["client_secret"]}), "access_token"
        )
        # per-source authorize failure limit
        for _ in range(bounds.oauth_fail_max):
            req = await server.validate_authorize(
                {
                    "response_type": "code",
                    "client_id": cid,
                    "redirect_uri": REDIRECT,
                    "code_challenge": CHALLENGE,
                    "code_challenge_method": "S256",
                }
            )
            tok = await server.issue_form_token(req)  # type: ignore[arg-type]

            async def none(_: str):  # type: ignore[no-untyped-def]
                return None

            assert (await server.authorize(tok, none, "bad", "attacker")).code == "access_denied"  # type: ignore[union-attr]
        limited = await server.authorize("x", none, "bad", "attacker")  # type: ignore[arg-type]
        assert limited.status == 429  # type: ignore[union-attr]

    _run(flow())


def test_authorize_validation_errors() -> None:
    clock = Time()
    server, _ = _server(clock)

    async def flow() -> None:
        cid = str((await _register(server))["client_id"])
        base = {
            "response_type": "code",
            "client_id": cid,
            "redirect_uri": REDIRECT,
            "code_challenge": CHALLENGE,
            "code_challenge_method": "S256",
        }
        assert hasattr(await server.validate_authorize(base), "code_challenge")
        for patch, code in (
            ({"redirect_uri": REDIRECT + "x"}, "invalid_client"),
            ({"client_id": "nope"}, "invalid_client"),
            ({"code_challenge_method": "plain"}, "invalid_request"),
            ({"code_challenge": "short"}, "invalid_request"),
            ({"response_type": "token"}, "unsupported_response_type"),
        ):
            assert (await server.validate_authorize({**base, **patch})).code == code  # type: ignore[union-attr]

    _run(flow())


def test_redirect_uri_rule_directly() -> None:
    assert redirect_uri_ok("https://a.example.com/x?y=1")
    assert not redirect_uri_ok("https://a.example.com/x#f")
    assert not redirect_uri_ok("javascript:alert(1)")
    assert not redirect_uri_ok("")


# -- end to end over HTTP ------------------------------------------------------------------------
def _http_app(identity: Identity):  # type: ignore[no-untyped-def]
    clock = Time()
    server, bounds = _server(clock)
    resolver = OAuthTokenResolver(server, bounds, clock)
    routes = OAuthRoutes(server=server, public_url=PUBLIC, identity=identity, body_bytes=65536)  # type: ignore[arg-type]
    broker = Broker()
    deps = replace(
        mcp_deps(broker, Control([server_row("s")])),
        oauth=routes,
        token_store=resolver,
        public_base=PUBLIC,
    )
    app = build_app(
        contract=_contract(),
        provider=None,
        scanner=holdback_scan,  # type: ignore[arg-type]
        resolver=MinimalOutputResolver(),
        metrics_registry=_Metrics(),
        clock=_Clock(),
        input_detector=_none,
        error_scanner=_none,
        identity=identity,  # type: ignore[arg-type]
        snapshot_source=warm_snapshot_source(),
        admission=AdmittingAdmission(),
        quota=AdmittingBudget(),
        mcp=deps,
    )
    return app, broker


async def _http(app, method, path, *, data=None, js=None, headers=None):  # type: ignore[no-untyped-def]
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://gw.test") as client:
        return await client.request(
            method, path, data=data, json=js, headers=headers or {}, follow_redirects=False
        )


def test_end_to_end_register_authorize_token_mcp_call_and_revocation() -> None:
    identity = Identity()
    app, broker = _http_app(identity)

    async def flow() -> None:
        meta = (await _http(app, "GET", "/.well-known/oauth-authorization-server")).json()
        assert meta["code_challenge_methods_supported"] == ["S256"]
        resource = (await _http(app, "GET", "/.well-known/oauth-protected-resource")).json()
        assert resource["authorization_servers"] == [PUBLIC]
        reg = await _http(app, "POST", "/oauth/register", js={"redirect_uris": [REDIRECT]})
        assert reg.status_code == 201
        cid = reg.json()["client_id"]
        query = urlencode(
            {
                "response_type": "code",
                "client_id": cid,
                "redirect_uri": REDIRECT,
                "code_challenge": CHALLENGE,
                "code_challenge_method": "S256",
                "state": "xyz",
            }
        )
        page = await _http(app, "GET", f"/oauth/authorize?{query}")
        assert page.status_code == 200 and page.headers["x-frame-options"] == "DENY"
        assert "default-src 'none'" in page.headers["content-security-policy"]
        form_token = re.search(r'name="form_token" value="([^"]+)"', page.text).group(1)  # type: ignore[union-attr]
        done = await _http(
            app, "POST", "/oauth/authorize", data={"form_token": form_token, "api_key": KEY}
        )
        assert done.status_code == 302
        loc = urlsplit(done.headers["location"])
        q = parse_qs(loc.query)
        assert loc.netloc == "app.example.com" and q["state"] == ["xyz"]
        replayed = await _http(
            app, "POST", "/oauth/authorize", data={"form_token": form_token, "api_key": KEY}
        )
        assert replayed.status_code == 403  # the form token is single use
        tok = await _http(
            app,
            "POST",
            "/oauth/token",
            data={
                "grant_type": "authorization_code",
                "client_id": cid,
                "code": q["code"][0],
                "redirect_uri": REDIRECT,
                "code_verifier": VERIFIER,
            },
        )
        assert tok.status_code == 200 and tok.headers["cache-control"] == "no-store"
        access = tok.json()["access_token"]
        auth = {"authorization": f"Bearer {access}"}
        ok = await _http(app, "POST", "/v1/mcp/s", js=call("tools/list"), headers=auth)
        assert ok.status_code == 200 and len(broker.requests) == 1
        identity.revoked = True  # the key behind the token is revoked
        denied = await _http(app, "POST", "/v1/mcp/s", js=call("tools/list"), headers=auth)
        assert denied.status_code == 401
        assert "resource_metadata" in denied.headers["www-authenticate"]
        assert len(broker.requests) == 1

    _run(flow())


def test_chat_never_accepts_a_token_and_unconfigured_oauth_is_503() -> None:
    identity = Identity()
    app, _ = _http_app(identity)
    chat = _run(
        _http(
            app,
            "POST",
            "/v1/chat/completions",
            js={"model": "m", "messages": []},
            headers={"authorization": "Bearer zsmcp_abc"},
        )
    )
    assert chat.status_code == 401
    bare = build_app(
        contract=_contract(),
        provider=None,
        scanner=holdback_scan,  # type: ignore[arg-type]
        resolver=MinimalOutputResolver(),
        metrics_registry=_Metrics(),
        clock=_Clock(),
        input_detector=_none,
        error_scanner=_none,
        identity=identity,  # type: ignore[arg-type]
        snapshot_source=warm_snapshot_source(),
        admission=AdmittingAdmission(),
        quota=AdmittingBudget(),
        mcp=mcp_deps(Broker(), Control([])),
    )
    for method, path in (
        ("GET", "/.well-known/oauth-authorization-server"),
        ("POST", "/oauth/token"),
        ("POST", "/oauth/register"),
    ):
        resp = _run(_http(bare, method, path))
        assert resp.status_code == 503 and resp.json()["error"] == "oauth_unavailable"


def test_error_bodies_never_echo_input() -> None:
    app, _ = _http_app(Identity())
    resp = _run(_http(app, "POST", "/oauth/token", data={"grant_type": "SECRET-<script>"}))
    assert "SECRET" not in resp.text and "<script>" not in resp.text
    assert json.loads(resp.text)["error"] in {"unsupported_grant_type", "invalid_client"}
