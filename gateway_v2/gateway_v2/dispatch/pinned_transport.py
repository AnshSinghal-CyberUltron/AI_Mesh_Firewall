"""Pinned dial for gateway-originated OAuth calls (GW32 design iteration 3, finding 1, HIGH).

A refresh token, client secret, code or PKCE verifier must never reach an address the gateway did
not approve. `resolve_checked` resolves and checks once; `PinnedTransport` then dials ONE of the
approved addresses while sending the original `Host` header and the original TLS server name, so
certificate verification still runs against the name. There is no second resolution at connect
time, and a 3xx is returned to the caller as a failure (never followed).
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING

from gateway_v2.dispatch.mcp_credentials import TokenEndpoint
from gateway_v2.dispatch.url_guard import Refusal, Resolver, resolve_checked

if TYPE_CHECKING:
    import httpx

__all__ = ("PinnedRefused", "PinnedTransport", "pinned_token_endpoint")


class PinnedRefused(Exception):
    """The destination was refused before any byte was sent. Carries no URL, no reason text."""


def _make_transport_class() -> type:
    import httpx

    class _Pinned(httpx.AsyncBaseTransport):
        def __init__(
            self, resolver: Resolver, allow_hosts: frozenset[str], inner: httpx.AsyncBaseTransport
        ) -> None:
            self._resolver, self._allow, self._inner = resolver, allow_hosts, inner

        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            url = request.url
            checked = await resolve_checked(
                str(url), resolver=self._resolver, allow_hosts=self._allow
            )
            if isinstance(checked, Refusal):
                raise PinnedRefused("destination refused")
            ip = checked[0]
            host = url.host
            pinned = request.url.copy_with(host=ip)
            request.url = pinned
            request.headers["Host"] = host if url.port is None else f"{host}:{url.port}"
            request.extensions = {**request.extensions, "sni_hostname": host}
            return await self._inner.handle_async_request(request)

        async def aclose(self) -> None:
            await self._inner.aclose()

    return _Pinned


def PinnedTransport(  # noqa: N802 - constructor-shaped factory for a lazily built httpx subclass
    resolver: Resolver,
    allow_hosts: frozenset[str] = frozenset(),
    inner: httpx.AsyncBaseTransport | None = None,
) -> httpx.AsyncBaseTransport:
    """An httpx transport that dials only an address `resolve_checked` approved."""
    import httpx

    cls = _make_transport_class()
    transport: httpx.AsyncBaseTransport = cls(
        resolver, allow_hosts, inner if inner is not None else httpx.AsyncHTTPTransport()
    )
    return transport


def pinned_token_endpoint(
    resolver: Resolver,
    allow_hosts: frozenset[str] = frozenset(),
    inner: httpx.AsyncBaseTransport | None = None,
) -> TokenEndpoint:
    """The production `TokenEndpoint`: POST a form through the pinned transport, no redirects,
    bounded read. Any failure raises; `CredentialService` classifies that as transient."""

    async def call(
        url: str, form: Mapping[str, str], timeout_s: float, max_bytes: int
    ) -> tuple[int, bytes]:
        import httpx

        transport = PinnedTransport(resolver, allow_hosts, inner)
        async with httpx.AsyncClient(
            transport=transport, timeout=timeout_s, follow_redirects=False
        ) as client:
            async with client.stream("POST", url, data=dict(form)) as response:
                chunks: list[bytes] = []
                total = 0
                async for chunk in response.aiter_bytes():
                    total += len(chunk)
                    if total > max_bytes:
                        raise ValueError("token endpoint reply over the bound")
                    chunks.append(chunk)
                return response.status_code, b"".join(chunks)

    return call
