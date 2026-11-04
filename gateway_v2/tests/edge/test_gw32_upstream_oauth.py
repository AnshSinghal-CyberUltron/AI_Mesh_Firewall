"""GW32 Requirement 9: upstream OAuth setup routes (oauth/start, callback, status) in process.

Fakes: the upstream IdP (an `UpstreamHttp` that records every call) and control's flow
endpoints (a `ControlCall` holding the flow row). The app is the real `build_app`.
"""
from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from dataclasses import replace
from urllib.parse import parse_qs, urlsplit

import pytest

httpx = pytest.importorskip("httpx")

from gateway_v2.detect.holdback import scan as holdback_scan  # noqa: E402
from gateway_v2.dispatch.mcp_upstream_oauth import (  # noqa: E402
    ControlOAuthFlowClient,
    UpstreamMetadata,
    discover,
)
from gateway_v2.domain.identity import Principal  # noqa: E402
from gateway_v2.edge import errors  # noqa: E402
from gateway_v2.edge.app import build_app  # noqa: E402
from gateway_v2.edge.identity_resolver import IdentityOutcome, TenantIdentity  # noqa: E402
from gateway_v2.edge.mcp_upstream_routes import UpstreamOAuthRoutes, oauth_url_of  # noqa: E402
from gateway_v2.egress.output_guard import MinimalOutputResolver  # noqa: E402
from gateway_v2.runtime.resources_mcp import derive_credential_bounds  # noqa: E402
from tests.edge._chat_lifecycle_fixtures import (  # noqa: E402
    AdmittingAdmission,
    AdmittingBudget,
    warm_snapshot_source,
)
from tests.edge._mcp_fixtures import ORG, Broker, Control, mcp_deps, server_row  # noqa: E402
from tests.edge.test_responses_route import _Clock, _contract, _Metrics, _none  # noqa: E402

BOUNDS = derive_credential_bounds(_contract())
SERVER = "https://mcp.example.com/mcp"
IDP = "https://idp.example.com"
ACCESS = "upstream-access-token-123456"
REFRESH = "upstream-refresh-token-987654"
PUBLIC = "https://gw.example.com"


class Idp:
    """The upstream: PRM, AS metadata, DCR and the token endpoint."""

    def __init__(self, **meta_over: object) -> None:
        self.calls: list[tuple[str, str, bytes | None]] = []
        self.meta: dict[str, object] = {
            "issuer": IDP,
            "authorization_endpoint": f"{IDP}/authorize",
            "token_endpoint": f"{IDP}/token",
            "registration_endpoint": f"{IDP}/register",
            "code_challenge_methods_supported": ["S256"],
        }
        self.meta.update(meta_over)
        self.prm_status = 200
        self.token_status = 200

    async def __call__(
        self, method: str, url: str, headers: Mapping[str, str], body: bytes | None,
        timeout_s: float, max_bytes: int,
    ) -> tuple[int, bytes]:
        self.calls.append((method, url, body))
        if url == "https://mcp.example.com/.well-known/oauth-protected-resource/mcp":
            return self.prm_status, json.dumps({"authorization_servers": [IDP]}).encode()
        if url == f"{IDP}/.well-known/oauth-authorization-server":
            return 200, json.dumps(self.meta).encode()
        if url == f"{IDP}/register":
            return 201, json.dumps({"client_id": "dcr-client"}).encode()
        if url == f"{IDP}/token":
            reply = {"access_token": ACCESS, "refresh_token": REFRESH, "expires_in": 3600}
            return self.token_status, json.dumps(reply).encode()
        return 404, b"{}"


class FlowControl:
    """Control's internal flow endpoints over one in-memory row."""

    def __init__(self, *, authorized: bool = False, client_id: str = "") -> None:
        self.row: dict[str, object] = {}
        self.status = {
            "authorized": authorized, "expired": False, "refreshable": False,
            "needs_reauth": False, "obtained_at": None, "client_id": client_id,
            "client_token_endpoint": f"{IDP}/token" if client_id else "",
        }
        self.stored: list[dict[str, object]] = []
        self.saved: list[dict[str, object]] = []
        self.orgs: list[str] = []

    async def __call__(
        self, method: str, url: str, headers: Mapping[str, str], query: Mapping[str, str],
        body: Mapping[str, object] | None, timeout_s: float, max_bytes: int,
    ) -> tuple[int, bytes]:
        self.orgs.append(headers.get("X-Org-Slug", ""))
        if url.endswith("/oauth-status/"):
            return 200, json.dumps(self.status).encode()
        if url.endswith("/oauth-flow/"):
            assert body is not None
            self.saved.append(dict(body))
            self.row = {**body, "client_secret": body.get("client_secret", "kept-secret")}
            return 200, b'{"stored": true}'
        if url.endswith("/oauth-flow/consume/"):
            assert body is not None
            if not self.row or body.get("state") != self.row.get("state"):
                return 404, b"{}"
            row, self.row = self.row, {}
            flow = {
                "org_slug": "acme", "server_slug": row["server_slug"], "key_id": row["key_id"],
                "code_verifier": row["code_verifier"], "token_endpoint": row["token_endpoint"],
                "client_id": row["client_id"], "client_secret": row["client_secret"],
                "resource": row["resource"],
            }
            return 200, json.dumps(flow).encode()
        if url.endswith("/credential/oauth-tokens/"):
            assert body is not None
            self.stored.append(dict(body))
            return 200, b'{"stored": true}'
        return 404, b"{}"


class Identity:
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
        return IdentityOutcome.resolved(self._identity)


class Tokens:
    async def resolve(self, token: str) -> str | None:
        return "h" * 64


def _principal(**over: object) -> Principal:
    return replace(Principal("kid", ORG, 0.0, 0.0, 1), **over)  # type: ignore[arg-type]


def _app(
    idp: Idp, flow: FlowControl, *, principal: Principal | None = None, public: str = PUBLIC,
    wired: bool = True, rows: list[dict[str, object]] | None = None,
):  # type: ignore[no-untyped-def]
    audit: list[tuple[str, dict[str, str]]] = []
    upstream = UpstreamOAuthRoutes(
        flow=ControlOAuthFlowClient(
            base_url="https://control.example.com", internal_key="k", call=flow, bounds=BOUNDS
        ),
        http=idp,
        bounds=BOUNDS,
        body_bytes=1024,
        public_base=public,
        audit=lambda event, detail: audit.append((event, dict(detail))),
    )
    deps = replace(
        mcp_deps(Broker(), Control(rows or [server_row("s")])),
        upstream_oauth=upstream if wired else None,
        token_store=Tokens(),
    )
    app = build_app(
        contract=_contract(), provider=None, scanner=holdback_scan,  # type: ignore[arg-type]
        resolver=MinimalOutputResolver(), metrics_registry=_Metrics(), clock=_Clock(),
        input_detector=_none, error_scanner=_none,
        identity=Identity(principal or _principal()),  # type: ignore[arg-type]
        snapshot_source=warm_snapshot_source(), admission=AdmittingAdmission(),
        quota=AdmittingBudget(), mcp=deps,
    )
    return app, audit


def _call(app: object, method: str, path: str, *, body: object = None,
          bearer: str = "Bearer " + "k" * 48) -> httpx.Response:
    async def go() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)  # type: ignore[arg-type]
        async with httpx.AsyncClient(transport=transport, base_url="http://gw.test") as client:
            headers = {"authorization": bearer} if bearer else {}
            content = None if body is None else json.dumps(body).encode()
            return await client.request(method, path, content=content, headers=headers)

    return asyncio.run(go())


START = "/gateway/acme/mcp/s/oauth/start"
STATUS = "/gateway/acme/mcp/s/oauth/status"


def _start_ok(idp: Idp, flow: FlowControl):  # type: ignore[no-untyped-def]
    app, audit = _app(idp, flow)
    resp = _call(app, "POST", START, body={})
    assert resp.status_code == 200, resp.text
    url = resp.json()["authorization_url"]
    return app, audit, url, parse_qs(urlsplit(url).query)


def test_start_discovers_registers_and_binds_state_to_the_key() -> None:
    idp, flow = Idp(), FlowControl()
    _, audit, url, query = _start_ok(idp, flow)
    assert url.startswith(f"{IDP}/authorize?")
    assert query["code_challenge_method"] == ["S256"]
    assert query["redirect_uri"] == [f"{PUBLIC}/gateway/oauth/callback"]
    assert query["client_id"] == ["dcr-client"]
    assert query["state"] == [flow.row["state"]]
    assert flow.row["key_id"] == "kid" and flow.row["ttl_s"] == BOUNDS.upstream_flow_ttl_s
    assert flow.row["code_verifier"] not in url  # only the challenge is sent to the browser
    assert [c[1] for c in idp.calls][-1] == f"{IDP}/register"
    assert audit == [("oauth_start", {"key_id": "kid", "server": "s", "force": "false"})]


def test_start_reuses_a_stored_client_and_keeps_its_secret() -> None:
    idp, flow = Idp(), FlowControl(client_id="stored-client")
    _, _, _, query = _start_ok(idp, flow)
    assert query["client_id"] == ["stored-client"]
    assert all(call[1] != f"{IDP}/register" for call in idp.calls)
    assert "client_secret" not in flow.saved[-1]  # control keeps the stored secret
    assert flow.row["client_secret"] == "kept-secret"


@pytest.mark.parametrize(
    ("principal", "bearer"),
    (
        (_principal(mcp_allowed_tools=frozenset({"a"})), "Bearer " + "k" * 48),
        (_principal(mcp_max_tool_calls=5), "Bearer " + "k" * 48),
        (_principal(), "Bearer zsmcp_" + "t" * 40),
    ),
)
def test_restricted_key_or_oauth_token_cannot_replace_the_credential(
    principal: Principal, bearer: str
) -> None:
    idp, flow = Idp(), FlowControl()
    app, audit = _app(idp, flow, principal=principal)
    resp = _call(app, "POST", START, body={}, bearer=bearer)
    assert resp.status_code == 403 and resp.json()["error"]["code"] == "mcp_tool_not_permitted"
    assert idp.calls == [] and flow.row == {}
    assert audit[0][0] == "oauth_start_refused_restricted"


def test_already_authorized_needs_force() -> None:
    idp, flow = Idp(), FlowControl(authorized=True)
    app, _ = _app(idp, flow)
    assert _call(app, "POST", START, body={}).status_code == 400
    assert idp.calls == []
    assert _call(app, "POST", START, body={"force": True}).status_code == 200


def test_bad_body_org_mismatch_and_no_public_url() -> None:
    idp, flow = Idp(), FlowControl()
    app, _ = _app(idp, flow)
    assert _call(app, "POST", START, body=[1]).status_code == 400
    assert _call(app, "POST", START, body={"force": "yes"}).status_code == 400
    wrong = _call(app, "POST", "/gateway/other/mcp/s/oauth/start", body={})
    assert (wrong.status_code, wrong.json()["error"]["code"]) == (403, "mcp_org_mismatch")
    assert _call(app, "POST", START, body={}, bearer="").status_code == 401
    unset, _ = _app(idp, flow, public="")
    resp = _call(unset, "POST", START, body={})
    assert (resp.status_code, resp.json()["error"]["code"]) == (
        503, "mcp_upstream_oauth_unavailable"
    )
    assert idp.calls == []


def test_unwired_paths_keep_the_routing_miss_bytes() -> None:
    app, _ = _app(Idp(), FlowControl(), wired=False)
    miss = errors.render_http("endpoint_not_found")
    for method, path in (("POST", START), ("GET", STATUS), ("GET", "/gateway/oauth/callback")):
        resp = _call(app, method, path, body={} if method == "POST" else None)
        assert (resp.status_code, resp.content) == (miss.status, miss.body)


def test_wrong_method_is_405() -> None:
    app, _ = _app(Idp(), FlowControl())
    assert _call(app, "GET", START).status_code == 405
    assert _call(app, "POST", STATUS, body={}).status_code == 405


@pytest.mark.parametrize(
    "meta_over",
    (
        {"issuer": "https://evil.example.com"},
        {"token_endpoint": "http://idp.example.com/token"},
        {"authorization_endpoint": "javascript:alert(1)"},
        {"code_challenge_methods_supported": ["plain"]},
    ),
)
def test_unsafe_metadata_fails_with_no_registration_and_no_flow(meta_over: dict) -> None:
    idp, flow = Idp(**meta_over), FlowControl()
    app, _ = _app(idp, flow)
    resp = _call(app, "POST", START, body={})
    assert (resp.status_code, resp.json()["error"]["code"]) == (502, "mcp_upstream_oauth_failed")
    assert all(call[1] != f"{IDP}/register" for call in idp.calls) and flow.row == {}


def test_metadata_redirect_is_refused() -> None:
    idp, flow = Idp(), FlowControl()
    idp.prm_status = 302
    app, _ = _app(idp, flow)
    assert _call(app, "POST", START, body={}).status_code == 502
    assert len(idp.calls) == 1


def test_callback_exchanges_once_and_never_renders_a_token() -> None:
    idp, flow = Idp(), FlowControl()
    app, audit, _, query = _start_ok(idp, flow)
    state = query["state"][0]
    verifier = flow.row["code_verifier"]
    resp = _call(app, "GET", f"/gateway/oauth/callback?code=the-code&state={state}", bearer="")
    assert resp.status_code == 200
    assert resp.headers["content-security-policy"] == "default-src 'none'"
    for secret in (ACCESS, REFRESH, "the-code", state, verifier):
        assert secret not in resp.text
    token_call = [c for c in idp.calls if c[1] == f"{IDP}/token"][0]
    form = parse_qs((token_call[2] or b"").decode())
    assert form["code_verifier"] == [verifier] and form["code"] == ["the-code"]
    assert flow.stored == [{
        "server_slug": "s", "access_token": ACCESS, "refresh_token": REFRESH, "expires_in": 3600,
    }]
    assert flow.orgs[-1] == "acme"
    assert audit[-1] == ("oauth_callback", {"key_id": "kid", "server": "s"})
    replay = _call(app, "GET", f"/gateway/oauth/callback?code=the-code&state={state}", bearer="")
    assert replay.status_code == 400 and len(flow.stored) == 1


def test_callback_error_consumes_the_state_and_echoes_nothing() -> None:
    idp, flow = Idp(), FlowControl()
    app, _, _, query = _start_ok(idp, flow)
    state = query["state"][0]
    hostile = "%3Cscript%3Ealert(1)%3C%2Fscript%3E"
    resp = _call(
        app, "GET",
        f"/gateway/oauth/callback?error=access_denied&error_description={hostile}&state={state}",
        bearer="",
    )
    assert resp.status_code == 400 and "<script>" not in resp.text and "alert" not in resp.text
    assert flow.row == {}  # consumed: the same state cannot be used again
    unknown = _call(app, "GET", "/gateway/oauth/callback?code=c&state=nope", bearer="")
    assert unknown.status_code == 400


def test_failed_exchange_is_a_fixed_page_and_stores_nothing() -> None:
    idp, flow = Idp(), FlowControl()
    app, _, _, query = _start_ok(idp, flow)
    idp.token_status = 400
    path = f"/gateway/oauth/callback?code=c&state={query['state'][0]}"
    resp = _call(app, "GET", path, bearer="")
    assert resp.status_code == 502 and flow.stored == []


def test_status_returns_only_the_five_fields() -> None:
    flow = FlowControl(client_id="stored-client")
    app, _ = _app(Idp(), flow)
    resp = _call(app, "GET", STATUS)
    assert resp.status_code == 200
    fields = {"authorized", "expired", "refreshable", "needs_reauth", "obtained_at"}
    assert set(resp.json()) == fields
    assert "stored-client" not in resp.text


def test_stdio_mcp_remote_uses_the_registered_argument_url() -> None:
    rows = [server_row("s", transport="stdio", url="", command="npx",
                       args=["-y", "mcp-remote", SERVER, "--debug"])]
    idp, flow = Idp(), FlowControl()
    app, _ = _app(idp, flow, rows=rows)
    resp = _call(app, "POST", START, body={"server_url": "https://attacker.example"})
    assert resp.status_code == 200
    assert all("attacker" not in call[1] for call in idp.calls)
    from gateway_v2.edge.mcp_registry import ServerEntry

    entry = ServerEntry("s", "stdio", "", "npx", ("mcp-remote", "http://plain"), {}, True, "none")
    assert oauth_url_of(entry) == ""


def test_discover_falls_back_to_the_server_origin_without_prm() -> None:
    idp = Idp(issuer="https://mcp.example.com")
    idp.prm_status = 404

    async def http(method: str, url: str, headers: Mapping[str, str], body: bytes | None,
                   timeout_s: float, max_bytes: int) -> tuple[int, bytes]:
        if url == "https://mcp.example.com/.well-known/oauth-authorization-server":
            return 200, json.dumps(idp.meta).encode()
        return 404, b"{}"

    meta = asyncio.run(discover(SERVER, http, BOUNDS))
    assert isinstance(meta, UpstreamMetadata) and meta.resource == SERVER
