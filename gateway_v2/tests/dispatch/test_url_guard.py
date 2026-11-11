# Validates: Requirements 6.4, 6.5, 11.1
"""SSRF matrix for the gateway-side MCP upstream check (LGW17-3)."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence

import pytest

from gateway_v2.dispatch.url_guard import check_remote, check_stdio, parse_allow_hosts

_STDIO_ALLOWED = frozenset({"npx", "node", "python", "python3", "uvx", "uv"})


def _resolver(table: Mapping[str, Sequence[str]]):  # type: ignore[no-untyped-def]
    async def resolve(host: str, port: int | None) -> Sequence[str]:
        del port
        if host not in table:
            raise OSError("NXDOMAIN")
        return table[host]

    return resolve


_DNS = {
    "localhost": ("127.0.0.1",),
    "public.example": ("93.184.216.34",),
    "rebind.example": ("93.184.216.34", "169.254.169.254"),
    "intra.example": ("10.1.2.3",),
    "stub.internal": ("172.20.0.9",),
    "meta.internal": ("169.254.169.254",),
    "loop.internal": ("127.0.0.1",),
}

#: (url, refused?) — the SSRF matrix written to the GW17 evidence package.
SSRF_MATRIX: tuple[tuple[str, bool], ...] = (
    ("http://127.0.0.1:9000/mcp", True),
    ("http://localhost/mcp", True),
    ("http://[::1]/mcp", True),
    ("http://10.0.0.5/mcp", True),
    ("http://192.168.1.1/mcp", True),
    ("http://169.254.169.254/latest/meta-data", True),
    ("http://169.254.1.1/", True),
    ("http://[fd00:ec2::254]/", True),
    ("http://[::ffff:127.0.0.1]/", True),
    ("http://0.0.0.0/", True),
    ("http://intra.example/mcp", True),
    ("http://rebind.example/mcp", True),
    ("http://nxdomain.example/mcp", True),
    ("ftp://public.example/", True),
    ("http://user@public.example/", True),
    ("http://169.254.169.254:8000@public.example/", True),
    ("https://public.example/mcp", False),
    ("wss://public.example/ws", False),
    ("http://93.184.216.34/mcp", False),
)


@pytest.mark.parametrize(("url", "refused"), SSRF_MATRIX)
def test_ssrf_matrix(url: str, refused: bool) -> None:
    reason = asyncio.run(check_remote(url, resolver=_resolver(_DNS)))
    assert (reason is not None) is refused, (url, reason)


def test_allowlisted_host_may_be_private_but_never_metadata_or_loopback() -> None:
    allow = parse_allow_hosts("stub.internal, meta.internal ,loop.internal")
    resolver = _resolver(_DNS)

    def check(url: str) -> str | None:
        return asyncio.run(check_remote(url, resolver=resolver, allow_hosts=allow))

    assert check("http://stub.internal/mcp") is None
    assert check("http://meta.internal/") is not None
    assert check("http://loop.internal/") is not None


def test_empty_allowlist_allows_nothing_internal() -> None:
    assert parse_allow_hosts("") == frozenset()
    assert parse_allow_hosts(" , ") == frozenset()
    reason = asyncio.run(
        check_remote("http://stub.internal/", resolver=_resolver(_DNS), allow_hosts=frozenset())
    )
    assert reason is not None


def test_resolver_that_raises_is_a_refusal() -> None:
    async def broken(host: str, port: int | None) -> Sequence[str]:
        raise RuntimeError("resolver crashed")

    assert asyncio.run(check_remote("https://public.example/", resolver=broken)) is not None


@pytest.mark.parametrize(
    ("command", "refused"),
    (
        ("npx", False),
        ("python3", False),
        ("/usr/bin/npx", True),
        ("./npx", True),
        ("bash", True),
        ("", True),
    ),
)
def test_stdio_allowlist(command: str, refused: bool) -> None:
    assert (check_stdio(command, _STDIO_ALLOWED) is not None) is refused
