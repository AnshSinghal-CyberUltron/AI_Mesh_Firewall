"""The MCP broker client (GW17, task 3.1).

The only way the MCP surface reaches an upstream: ``POST {broker}/v1/sandbox/{slug}/rpc``
with the ``SandboxRpcRequest`` body the broker defines
(``services/mcp-broker/src/sandbox/routes.py:102-124``). The broker runs one sandbox per
org; the sandbox agent enforces ``allowed_hosts``, its own SSRF check and the stdio
allowlist. This module never dials an upstream; the broker URL is the only destination
it knows.

Retry rule (v1 ``mcp_sandbox_client.py:94-160``): a broker 503 is retried with bounded
backoff. ``tools/call`` is retried only when the request was provably never sent, so a
tool is never run twice. Every number comes from ``McpBounds``; no exception text leaves
this module, only a declared value-code.
"""

from __future__ import annotations

import json
import random
import re
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from gateway_v2.runtime.resources import McpBounds

if TYPE_CHECKING:
    import httpx

__all__ = (
    "BROKER_SLUG",
    "BrokerClient",
    "BrokerNotSent",
    "BrokerReply",
    "BrokerSent",
    "BrokerTimeout",
    "BrokerTooLarge",
    "McpServer",
    "Transport",
    "httpx_transport",
)

#: The org slug the broker accepts (``routes.py:32-45``).
BROKER_SLUG = re.compile(r"^(?!-)[A-Za-z0-9_.-]{1,64}(?<!-)$")

_BROKER_UNAVAILABLE = "mcp_broker_unavailable"
_UPSTREAM_ERROR = "mcp_upstream_error"
_RESULT_TOO_LARGE = "mcp_result_too_large"
_NO_POST_SEND_RETRY = frozenset({"tools/call"})
_STDIO = "stdio"
_OAUTH_ROLE = "forbidden_in_sandbox"
_OK = 200
_RETRYABLE = 503


class BrokerNotSent(Exception):
    """The request provably never left (connect or pool failure). Always retryable."""


class BrokerSent(Exception):
    """A transport error after the request may have been sent."""


class BrokerTimeout(Exception):
    """The broker did not answer within the timeout."""


class BrokerTooLarge(Exception):
    """The broker reply exceeded the result byte bound."""


#: ``(url, headers, body, timeout_s, max_bytes) -> (status, reply bytes)``.
Transport = Callable[
    [str, Mapping[str, str], bytes, float, int], Awaitable[tuple[int, bytes]]
]
Sleep = Callable[[float], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class McpServer:
    """What the broker needs about one registered server."""

    slug: str
    transport: str
    url: str = ""
    command: str = ""
    args: tuple[str, ...] = ()
    env: Mapping[str, str] | None = None


@dataclass(frozen=True, slots=True)
class BrokerReply:
    """One JSON-RPC envelope, or the value-code that replaced it."""

    envelope: object = None
    code: str | None = None


class BrokerClient:
    """Posts one ``SandboxRpcRequest`` per call. Holds no request-scoped state."""

    __slots__ = ("_base", "_bounds", "_key", "_rng", "_sleep", "_transport")

    def __init__(
        self,
        *,
        base_url: str,
        key: str,
        bounds: McpBounds,
        transport: Transport,
        rng: random.Random,
        sleep: Sleep,
    ) -> None:
        if not base_url or not key:
            raise ValueError("the broker client needs a base URL and a key")
        self._base = base_url.rstrip("/")
        self._key = key
        self._bounds = bounds
        self._transport = transport
        self._rng = rng
        self._sleep = sleep

    async def call(
        self, slug: str, server: McpServer, method: str, params: object, request_id: str
    ) -> BrokerReply:
        """Forward one method through the org's sandbox. Never raises."""
        if not BROKER_SLUG.match(slug):
            return BrokerReply(code=_BROKER_UNAVAILABLE)
        body = json.dumps(self._payload(server, method, params, request_id)).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "X-MCP-Broker-Key": self._key,
            "X-Request-ID": request_id,
        }
        url = f"{self._base}/v1/sandbox/{slug}/rpc"
        return await self._send(url, headers, body, method)

    def _payload(
        self, server: McpServer, method: str, params: object, request_id: str
    ) -> dict[str, object]:
        payload: dict[str, object] = {
            "server_slug": server.slug,
            "transport": server.transport,
            "method": method,
            "params": params if params is not None else {},
            "jsonrpc_id": request_id,
            "timeouts": {
                "init_seconds": self._bounds.broker_init_timeout_s,
                "method_seconds": self._bounds.broker_method_timeout_s,
            },
        }
        if server.transport == _STDIO:
            payload["stdio"] = {
                "command": server.command,
                "args": list(server.args),
                "env": dict(server.env or {}),
            }
        else:
            payload["upstream"] = {
                "url": server.url,
                "allowed_hosts": _hosts(server.url),
                "headers": {},
                "oauth_client_role": _OAUTH_ROLE,
            }
        return payload

    async def _send(
        self, url: str, headers: Mapping[str, str], body: bytes, method: str
    ) -> BrokerReply:
        timeout = max(self._bounds.broker_init_timeout_s, self._bounds.broker_method_timeout_s)
        attempts = self._bounds.broker_retry_max
        for attempt in range(attempts):
            last = attempt == attempts - 1
            try:
                status, reply = await self._transport(
                    url, headers, body, timeout, self._bounds.result_bytes
                )
            except BrokerNotSent:
                if last:
                    return BrokerReply(code=_BROKER_UNAVAILABLE)
                await self._backoff(attempt)
                continue
            except BrokerSent:
                if method in _NO_POST_SEND_RETRY or last:
                    return BrokerReply(code=_BROKER_UNAVAILABLE)
                await self._backoff(attempt)
                continue
            except BrokerTimeout:
                return BrokerReply(code=_BROKER_UNAVAILABLE)
            except BrokerTooLarge:
                return BrokerReply(code=_RESULT_TOO_LARGE)
            except Exception:  # noqa: BLE001 - an unknown transport failure is fail-closed
                return BrokerReply(code=_BROKER_UNAVAILABLE)
            if status == _RETRYABLE and not last:
                await self._backoff(attempt)
                continue
            return _reply(status, reply)
        return BrokerReply(code=_BROKER_UNAVAILABLE)

    async def _backoff(self, attempt: int) -> None:
        base = min(
            self._bounds.broker_backoff_cap_s,
            self._bounds.broker_backoff_base_s * (2**attempt),
        )
        await self._sleep(base + self._rng.uniform(0.0, base / 4))


def _hosts(url: str) -> list[str]:
    try:
        host = urlparse(url).hostname
    except ValueError:
        return []
    return [host] if host else []


def _reply(status: int, raw: bytes) -> BrokerReply:
    """Map one broker HTTP reply to an envelope or a value-code."""
    if status == 502:
        return BrokerReply(code=_UPSTREAM_ERROR)
    if status != _OK:
        return BrokerReply(code=_BROKER_UNAVAILABLE)
    try:
        decoded = json.loads(raw)
    except (UnicodeDecodeError, ValueError):
        return BrokerReply(code=_UPSTREAM_ERROR)
    envelope = _strip_meta(decoded)
    if not _is_envelope(envelope):
        return BrokerReply(code=_UPSTREAM_ERROR)
    return BrokerReply(envelope=envelope)


def _strip_meta(value: object) -> object:
    if isinstance(value, dict):
        return {key: item for key, item in value.items() if key != "_meta"}
    if isinstance(value, list):
        return [_strip_meta(item) for item in value]
    return value


def _is_envelope(value: object) -> bool:
    if isinstance(value, list):
        return bool(value) and all(_is_envelope(item) for item in value)
    return isinstance(value, dict) and ("result" in value or "error" in value or "method" in value)


def httpx_transport(factory: Callable[[], httpx.AsyncClient] | None = None) -> Transport:
    """The default transport: lazy ``httpx``, bounded streaming read, typed failures.

    ``factory`` supplies a shared client; without it each call opens and closes one.
    """

    async def post(
        url: str, headers: Mapping[str, str], body: bytes, timeout_s: float, max_bytes: int
    ) -> tuple[int, bytes]:
        try:
            import httpx
        except ImportError as exc:  # pragma: no cover - only without the conformance extra
            raise BrokerNotSent("httpx is not installed") from exc
        client = factory() if factory is not None else httpx.AsyncClient(timeout=timeout_s)
        try:
            async with client.stream("POST", url, headers=dict(headers), content=body) as resp:
                chunks: list[bytes] = []
                total = 0
                async for chunk in resp.aiter_bytes():
                    total += len(chunk)
                    if total > max_bytes:
                        raise BrokerTooLarge("broker reply over the result bound")
                    chunks.append(chunk)
                return resp.status_code, b"".join(chunks)
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout) as exc:
            raise BrokerNotSent("not sent") from exc
        except httpx.TimeoutException as exc:
            raise BrokerTimeout("timeout") from exc
        except httpx.HTTPError as exc:
            raise BrokerSent("transport error") from exc
        finally:
            if factory is None:
                await client.aclose()

    return post
