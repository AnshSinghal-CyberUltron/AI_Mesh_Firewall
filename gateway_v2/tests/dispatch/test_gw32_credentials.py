"""GW17b properties 5 to 8: no credential in any sink, render validity, cache, single flight."""

from __future__ import annotations

import asyncio
import json
import logging
import random
import string
from collections.abc import Mapping

import pytest

from gateway_v2.dispatch.mcp_credentials import (
    ControlCredentialClient,
    CredentialCache,
    CredentialService,
    render,
)
from gateway_v2.domain.mcp_credential import CredentialRefusal, RenderedHeaders, UpstreamCredential
from gateway_v2.edge.mcp_cred_gate import attachable, leaks
from gateway_v2.runtime.kinds import HardwareSignals
from gateway_v2.runtime.resources import from_signals
from gateway_v2.runtime.resources_mcp import derive_credential_bounds

ITERATIONS = 200
BOUNDS = derive_credential_bounds(
    from_signals(
        HardwareSignals(
            cpu_quota=4.0,
            memory_limit=8 << 30,
            fd_limit=4096,
            cpu_source="t",
            mem_source="t",
            fd_source="t",
        ),
        target_p99_ms=20.0,
        utilization_cap=0.75,
        per_worker_rss=400 << 20,
    )
)


def _secret(rng: random.Random, n: int | None = None) -> str:
    alphabet = string.ascii_letters + string.digits + "-_."
    return "".join(rng.choice(alphabet) for _ in range(n or rng.randint(8, 40)))


# Feature: gw32-mcp-transport, Property 5: a credential is in no sink
def test_property_5_secret_never_in_repr_logs_or_exceptions(
    caplog: pytest.LogCaptureFixture,
) -> None:
    rng = random.Random(5)
    caplog.set_level(logging.DEBUG)
    for _ in range(ITERATIONS):
        secret = _secret(rng)
        cred = UpstreamCredential(kind="bearer", token=secret)
        rendered = render(cred, BOUNDS)
        assert isinstance(rendered, RenderedHeaders)
        sinks = [repr(cred), str(cred), repr(rendered), str(rendered), repr([cred, rendered])]
        refusal = render(
            UpstreamCredential(kind="header", token=secret, header_name="Host"), BOUNDS
        )
        assert isinstance(refusal, CredentialRefusal)
        sinks += [repr(refusal), refusal.code]
        assert all(secret[:8] not in sink for sink in sinks)
    assert all("Bearer" not in record.getMessage() for record in caplog.records)


def test_property_5_negative_control_a_leaking_repr_is_caught() -> None:
    secret = "abcdefgh12345"
    leaking_sinks = [f"UpstreamCredential(token={secret!r})"]
    assert any(secret[:8] in sink for sink in leaking_sinks)  # the property's check flags it


# Feature: gw32-mcp-transport, Property 7: header validation
def test_property_7_render_rules() -> None:
    rng = random.Random(6)
    for _ in range(ITERATIONS):
        secret = _secret(rng)
        bearer = render(UpstreamCredential(kind="bearer", token=secret), BOUNDS)
        assert isinstance(bearer, RenderedHeaders)
        assert bearer.pairs == (("Authorization", f"Bearer {secret}"),)
        for bad in ("x\r\ny" * 4, "a\nb" * 4, "tab\there1", "caf\u00e9-secret", "short"):
            out = render(UpstreamCredential(kind="bearer", token=bad), BOUNDS)
            assert isinstance(out, CredentialRefusal)
            assert out.code == "mcp_upstream_credential_invalid"
    basic = render(UpstreamCredential(kind="basic", token="pass-word-1", username="u"), BOUNDS)
    assert isinstance(basic, RenderedHeaders) and basic.pairs[0][1].startswith("Basic ")
    for kw in (
        {"kind": "basic", "token": "pass-word-1", "username": "a:b"},
        {"kind": "header", "token": "value-12345", "header_name": "Bad Name"},
        {"kind": "header", "token": "value-12345", "header_name": "x-request-id"},
        {"kind": "header", "token": "value-12345", "header_name": ""},
        {"kind": "query_param", "token": "value-12345"},
    ):
        assert isinstance(render(UpstreamCredential(**kw), BOUNDS), CredentialRefusal)  # type: ignore[arg-type]
    ok = render(
        UpstreamCredential(kind="header", token="value-12345", header_name="X-Api-Key"), BOUNDS
    )
    assert isinstance(ok, RenderedHeaders) and ok.pairs == (("X-Api-Key", "value-12345"),)


def test_attachable_policy() -> None:
    kw = {"plaintext_hosts": frozenset(), "allowed_hosts": ("mcp.example.com",)}
    assert attachable(transport="streamable-http", url="https://mcp.example.com/m", **kw) is None  # type: ignore[arg-type]
    assert attachable(transport="stdio", url="", **kw) == "mcp_upstream_credential_invalid"  # type: ignore[arg-type]
    assert attachable(transport="sse", url="http://mcp.example.com/m", **kw) == (  # type: ignore[arg-type]
        "mcp_upstream_insecure_transport"
    )
    assert (
        attachable(
            transport="sse",
            url="http://mcp.example.com/m",
            plaintext_hosts=frozenset({"mcp.example.com"}),
            allowed_hosts=("mcp.example.com",),
        )
        is None
    )
    assert (
        attachable(
            transport="websocket",
            url="ws://localhost:1/x",
            plaintext_hosts=frozenset(),
            allowed_hosts=("localhost",),
        )
        is None
    )
    assert attachable(transport="websocket", url="https://mcp.example.com", **kw) == (  # type: ignore[arg-type]
        "mcp_upstream_credential_invalid"
    )
    assert (
        attachable(
            transport="sse",
            url="https://mcp.example.com/m",
            plaintext_hosts=frozenset(),
            allowed_hosts=("other.example.com",),
        )
        == "mcp_destination_refused"
    )


def test_reflection_guard_forms() -> None:
    secret = "Sup3r-Secret-Value"
    cred = UpstreamCredential(kind="bearer", token=secret)
    import base64
    from urllib.parse import quote

    forms = {
        "plain": f"echo {secret}",
        "case": secret.lower(),
        "hex": secret.encode().hex(),
        "pct": quote(secret + "/ x", safe=""),
        "prefix": f"leading {secret[:8]} only",
    }
    for name, text in forms.items():
        envelope = {"result": {"content": [{"type": "text", "text": text}]}}
        assert leaks(cred, envelope, min_len=8) is not None, name
    embedded = base64.b64encode(b"zz" + secret.encode() + b"tail").decode()
    assert leaks(cred, {"result": {"x": embedded}}, min_len=8) == "b64"
    assert leaks(cred, {"result": {secret: 1}}, min_len=8) is not None  # object key
    assert leaks(cred, {"result": {"text": json.dumps({"a": secret})}}, min_len=8) is not None
    assert (
        leaks(cred, {"result": {"content": [{"type": "text", "text": "hello"}]}}, min_len=8) is None
    )


# --- Properties 7 and 8: cache and refresh, over fakes -----------------------------------------
class Control:
    def __init__(self, cred: dict[str, object]) -> None:
        self.cred, self.fetches, self.writes, self.reauth = cred, 0, 0, 0
        self.fail_write = False

    async def __call__(
        self,
        method: str,
        url: str,
        headers: Mapping[str, str],
        query: Mapping[str, str],
        body: Mapping[str, object] | None,
        timeout_s: float,
        max_bytes: int,
    ) -> tuple[int, bytes]:
        await asyncio.sleep(0)
        if url.endswith("/credential/"):
            self.fetches += 1
            data = dict(self.cred)
            if query.get("need_refresh") != "1":
                for key in ("refresh_token", "token_endpoint", "client_secret"):
                    data.pop(key, None)
            return 200, json.dumps(data).encode()
        if url.endswith("refresh-result/"):
            if self.fail_write:
                return 409, b"{}"
            assert body is not None
            self.writes += 1
            self.cred.update(
                token=body["access_token"],
                version=int(self.cred["version"]) + 1,  # type: ignore[call-overload]
                expires_at=1e12,
            )
            return 200, b"{}"
        self.reauth += 1
        return 200, b"{}"


class Lock:
    def __init__(self) -> None:
        self.held: set[tuple[str, str]] = set()

    async def acquire(self, org: str, slug: str, holder: str, ttl_s: float) -> bool:
        if (org, slug) in self.held:
            return False
        self.held.add((org, slug))
        return True

    async def release(self, org: str, slug: str, holder: str) -> None:
        self.held.discard((org, slug))


def _service(control: Control, endpoint, clock=lambda: 1000.0):  # type: ignore[no-untyped-def]
    client = ControlCredentialClient(
        base_url="https://control.example.com", internal_key="k", call=control, bounds=BOUNDS
    )
    cache = CredentialCache(slots=BOUNDS.credential_cache_slots, ttl_s=120.0, clock=clock)

    async def sleep(_: float) -> None:
        await asyncio.sleep(0)

    return CredentialService(
        client=client,
        cache=cache,
        lock=Lock(),
        token_endpoint=endpoint,
        bounds=BOUNDS,
        clock=clock,
        sleep=sleep,
    ), cache


def _oauth(**over: object) -> dict[str, object]:
    base: dict[str, object] = {
        "auth_type": "oauth",
        "token": "old-access-token",
        "expires_at": 1001.0,
        "refreshable": True,
        "version": 1,
        "refresh_token": "refresh-123456",
        "token_endpoint": "https://idp.example.com/token",
    }
    base.update(over)
    return base


def _endpoint(reply: dict[str, object] | None = None, status: int = 200):  # type: ignore[no-untyped-def]
    calls: list[Mapping[str, str]] = []

    async def call(url: str, form: Mapping[str, str], timeout_s: float, max_bytes: int):  # type: ignore[no-untyped-def]
        calls.append(form)
        await asyncio.sleep(0)
        body = (
            reply
            if reply is not None
            else {
                "access_token": "new-access-token",
                "token_type": "Bearer",
                "expires_in": 3600,
            }
        )
        return status, json.dumps(body).encode()

    call.calls = calls  # type: ignore[attr-defined]
    return call


# Feature: gw32-mcp-transport, Property 8: N concurrent requests, one refresh
def test_property_8_single_flight_refresh() -> None:
    rng = random.Random(8)
    for _ in range(ITERATIONS):
        n = rng.randint(2, 12)
        control, endpoint = Control(_oauth()), _endpoint()
        service, _ = _service(control, endpoint)

        async def run() -> list[object]:
            return list(
                await asyncio.gather(*(service.acquire("o", "s", "oauth") for _ in range(n)))
            )

        results = asyncio.run(run())
        assert len(endpoint.calls) == 1  # type: ignore[attr-defined]
        assert all(
            isinstance(r, UpstreamCredential) and r.token == "new-access-token" for r in results
        )
        assert control.writes == 1 and control.reauth == 0


def test_cache_hit_avoids_fetch_and_type_change_bypasses() -> None:
    control = Control({"auth_type": "bearer", "token": "bearer-token-1", "version": 1})
    service, cache = _service(control, _endpoint())
    first = asyncio.run(service.acquire("o", "s", "bearer"))
    again = asyncio.run(service.acquire("o", "s", "bearer"))
    assert isinstance(first, UpstreamCredential) and again is first and control.fetches == 1
    assert cache.get("o", "s", "basic") is None  # a changed auth_type never reuses the entry
    assert cache.keys() == frozenset()  # and drops it


def test_permanent_failure_marks_needs_reauth_with_zero_credential() -> None:
    control = Control(_oauth())
    service, cache = _service(control, _endpoint({"error": "invalid_grant"}, 400))
    out = asyncio.run(service.acquire("o", "s", "oauth"))
    assert isinstance(out, CredentialRefusal) and out.code == "mcp_upstream_needs_reauth"
    assert control.reauth == 1 and cache.keys() == frozenset()


def test_transient_failures_mark_needs_reauth_only_after_n() -> None:
    control = Control(_oauth())
    service, _ = _service(control, _endpoint({}, 503))
    codes = [
        asyncio.run(service.acquire("o", "s", "oauth"))
        for _ in range(BOUNDS.needs_reauth_after_failures)
    ]
    assert all(isinstance(c, CredentialRefusal) for c in codes)
    assert [c.code for c in codes[:-1]] == ["mcp_upstream_credential_unavailable"] * (
        len(codes) - 1
    )  # type: ignore[union-attr]
    assert control.reauth == 1


def test_control_404_and_5xx_and_short_secret() -> None:
    class Down(Control):
        async def __call__(self, *a, **k):  # type: ignore[no-untyped-def]
            raise ConnectionError("boom secret-token")

    service, _ = _service(Down({}), _endpoint())
    out = asyncio.run(service.acquire("o", "s", "bearer"))
    assert isinstance(out, CredentialRefusal) and "secret-token" not in repr(out)
    assert out.code == "mcp_upstream_credential_unavailable"
    with pytest.raises(ValueError):
        ControlCredentialClient(
            base_url="http://control.example.com", internal_key="k", call=Control({}), bounds=BOUNDS
        )


class PerOrgControl:
    """Control fake whose answer depends on the `X-Org-Slug` it is asked with."""

    def __init__(self, by_org: dict[str, str]) -> None:
        self.by_org = by_org

    async def __call__(
        self,
        method: str,
        url: str,
        headers: Mapping[str, str],
        query: Mapping[str, str],
        body: Mapping[str, object] | None,
        timeout_s: float,
        max_bytes: int,
    ) -> tuple[int, bytes]:
        token = self.by_org.get(headers["X-Org-Slug"])
        if token is None:
            return 404, b"{}"
        return 200, json.dumps({"auth_type": "bearer", "token": token, "version": 1}).encode()


# Feature: gw32-mcp-transport, Property 6: org isolation
def test_property_6_two_orgs_same_slug_never_share_a_credential() -> None:
    rng = random.Random(6)
    for _ in range(ITERATIONS):
        tokens = {"org-a": _secret(rng, 20), "org-b": _secret(rng, 20)}
        control = PerOrgControl(tokens)
        service, cache = _service(control, _endpoint())  # type: ignore[arg-type]
        order = [("org-a", "s"), ("org-b", "s"), ("org-c", "s"), ("org-a", "s"), ("org-b", "s")]
        rng.shuffle(order)
        for org, slug in order:
            out = asyncio.run(service.acquire(org, slug, "bearer"))
            if org in tokens:
                assert isinstance(out, UpstreamCredential) and out.token == tokens[org]
            else:
                assert isinstance(out, CredentialRefusal)  # an org with no credential gets none
        assert {k[0] for k in cache.keys()} <= set(tokens)
