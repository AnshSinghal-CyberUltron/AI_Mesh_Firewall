"""The ONE provider entry point. Requires a DispatchAuthorization minted by resolve/.

Pooled keepalive aiohttp client; connection limit from the ResourceContract's
per-process connection budget; x-request-id is forwarded; the harness's
x-synth-* test headers pass through. The firewall never originates a call.
"""

from __future__ import annotations

import time
from collections.abc import Sequence

import aiohttp

from rvproto.domain.decision import DISPATCHABLE, DispatchAuthorization
from rvproto.runtime.config import Settings
from rvproto.runtime.contract import Bounds
from rvproto.runtime.metrics import Registry


class UnauthorizedDispatch(PermissionError):
    pass


class ProviderClient:
    def __init__(self, s: Settings, bounds: Bounds, metrics: Registry) -> None:
        self.s = s
        self.bounds = bounds
        self.metrics = metrics
        self.url = f"{s.provider_url}/v1/chat/completions"
        self.session: aiohttp.ClientSession | None = None

    async def start(self) -> None:
        trace = aiohttp.TraceConfig()
        trace.on_connection_create_end.append(self._on_conn)
        trace.on_connection_queued_start.append(self._on_queued_start)
        trace.on_connection_queued_end.append(self._on_queued_end)
        connector = aiohttp.TCPConnector(
            limit=self.bounds.provider_connections,
            limit_per_host=self.bounds.provider_connections,
            keepalive_timeout=self.s.provider_timeout_s,
            ttl_dns_cache=None,
        )
        self.session = aiohttp.ClientSession(
            connector=connector,
            timeout=aiohttp.ClientTimeout(total=None, sock_read=self.s.provider_timeout_s,
                                          sock_connect=self.s.provider_timeout_s),
            auto_decompress=False,
            trace_configs=[trace],
            skip_auto_headers=("User-Agent", "Accept-Encoding"),
        )

    async def _on_conn(self, session: object, ctx: object, params: object) -> None:
        self.metrics.inc("provider_connections_opened")

    async def _on_queued_start(self, session: object, ctx: object, params: object) -> None:
        ctx.q0 = time.perf_counter_ns()  # type: ignore[attr-defined]

    async def _on_queued_end(self, session: object, ctx: object, params: object) -> None:
        self.metrics.observe("dispatch_pool_wait_ns", time.perf_counter_ns() - ctx.q0)  # type: ignore[attr-defined]

    async def send(
        self,
        auth: DispatchAuthorization,
        body: bytes,
        request_id: str,
        passthrough: Sequence[tuple[str, str]],
    ) -> aiohttp.ClientResponse:
        if not isinstance(auth, DispatchAuthorization) or auth.request_id != request_id:
            raise UnauthorizedDispatch("dispatch requires this request's DispatchAuthorization")
        if auth.disposition not in DISPATCHABLE:
            raise UnauthorizedDispatch(f"{auth.disposition} cannot be dispatched")
        assert self.session is not None
        headers = {"content-type": "application/json", "x-request-id": request_id}
        headers.update(passthrough)
        if self.s.provider_key:
            headers["authorization"] = f"Bearer {self.s.provider_key}"
        self.metrics.inc("provider_calls")
        return await self.session.post(self.url, data=body, headers=headers)

    async def close(self) -> None:
        if self.session is not None:
            await self.session.close()
