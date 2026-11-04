"""GW32 pinned dial: zero second resolution, mixed addresses refused, redirects not followed."""
from __future__ import annotations

import asyncio

import httpx
import pytest

from gateway_v2.dispatch.pinned_transport import PinnedRefused, pinned_token_endpoint
from gateway_v2.dispatch.url_guard import Refusal, resolve_checked


class _Recorder(httpx.AsyncBaseTransport):
    def __init__(self, status: int = 200, body: bytes = b"{}") -> None:
        self.seen: list[httpx.Request] = []
        self._status, self._body = status, body

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.seen.append(request)
        return httpx.Response(self._status, content=self._body)


def _resolver(answers: list[list[str]]):
    calls: list[str] = []

    async def resolve(host: str, port: int | None) -> list[str]:
        calls.append(host)
        return answers[min(len(calls) - 1, len(answers) - 1)]

    resolve.calls = calls  # type: ignore[attr-defined]
    return resolve


def test_dials_the_approved_ip_with_original_host_and_one_resolution() -> None:
    resolver = _resolver([["93.184.216.34"], ["169.254.169.254"]])
    inner = _Recorder()
    call = pinned_token_endpoint(resolver, inner=inner)
    status, _ = asyncio.run(call("https://idp.example.com/token", {"a": "b"}, 5.0, 1024))
    assert status == 200
    assert len(resolver.calls) == 1  # type: ignore[attr-defined]
    request = inner.seen[0]
    assert request.url.host == "93.184.216.34"
    assert request.headers["Host"] == "idp.example.com"
    assert request.extensions["sni_hostname"] == "idp.example.com"


def test_rebinding_second_answer_never_receives_a_byte() -> None:
    resolver = _resolver([["93.184.216.34"], ["169.254.169.254"]])
    inner = _Recorder()
    call = pinned_token_endpoint(resolver, inner=inner)
    asyncio.run(call("https://idp.example.com/token", {}, 5.0, 1024))
    assert all(r.url.host != "169.254.169.254" for r in inner.seen)


def test_mixed_public_and_private_answers_are_refused() -> None:
    resolver = _resolver([["93.184.216.34", "169.254.169.254"]])
    inner = _Recorder()
    call = pinned_token_endpoint(resolver, inner=inner)
    with pytest.raises(PinnedRefused):
        asyncio.run(call("https://idp.example.com/token", {}, 5.0, 1024))
    assert inner.seen == []


def test_redirect_is_returned_not_followed_and_oversize_raises() -> None:
    inner = _Recorder(status=302)
    call = pinned_token_endpoint(_resolver([["93.184.216.34"]]), inner=inner)
    status, _ = asyncio.run(call("https://idp.example.com/token", {}, 5.0, 1024))
    assert status == 302 and len(inner.seen) == 1
    big = pinned_token_endpoint(
        _resolver([["93.184.216.34"]]), inner=_Recorder(body=b"x" * 50)
    )
    with pytest.raises(ValueError):
        asyncio.run(big("https://idp.example.com/token", {}, 5.0, 10))


def test_resolve_checked_returns_refusal_value() -> None:
    result = asyncio.run(
        resolve_checked("https://x.example.com/", resolver=_resolver([["10.0.0.1"]]))
    )
    assert isinstance(result, Refusal)
