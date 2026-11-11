"""Dev-profile audit sink. One writer per worker, off the request path.

The chat route calls :meth:`DevAudit.emit`, which is :meth:`AuditSink.emit`: it does
not await and it does not raise. :meth:`DevAudit.startup` starts :meth:`AuditSink.run`
on the lifespan, and shutdown cancels that task. The queue depth is the admission
bound the boot already derived. The memory knobs come from the environment.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Mapping
from typing import Any

import redis.asyncio as redis_asyncio

from gateway_v2.audit.budget import StreamBudget
from gateway_v2.audit.record import AuditRecord
from gateway_v2.audit.sink import AuditSink, SinkCounters
from gateway_v2.domain.audit_knobs import knobs_from_env
from gateway_v2.runtime.store_audit import ValkeyAuditStore


class DevAudit:
    """The sink plus the writer task the dev lifespan owns."""

    __slots__ = ("_client", "_owns_client", "_sink", "_task")

    def __init__(self, sink: AuditSink, client: Any, *, owns_client: bool) -> None:
        self._sink = sink
        self._client = client
        self._owns_client = owns_client
        self._task: asyncio.Task[None] | None = None

    def emit(self, record: AuditRecord) -> bool:
        """Enqueue one record. A full queue returns False and the chat still succeeds."""
        return self._sink.emit(record)

    def counters(self) -> SinkCounters:
        """The sink's live counters, read on each ``/metrics`` scrape."""
        return self._sink.counters()

    async def startup(self) -> None:
        """Start the writer. A second call does not start a second task."""
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._sink.run())

    async def shutdown(self) -> None:
        """Cancel the writer and close a client this object opened."""
        task = self._task
        self._task = None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        if self._owns_client:
            await self._client.aclose()


def build_dev_audit(
    *,
    redis_url: str,
    timeout_s: float,
    queue_depth: int,
    env: Mapping[str, str],
    client: Any | None = None,
) -> DevAudit:
    """Build the sink. ``client`` is for tests; the boot path opens its own."""
    owns_client = client is None
    if client is None:
        client = redis_asyncio.Redis.from_url(
            redis_url,
            socket_timeout=timeout_s,
            socket_connect_timeout=timeout_s,
        )
    sink = AuditSink(
        ValkeyAuditStore(client),
        StreamBudget(knobs_from_env(env), lambda: 1),
        queue_depth=queue_depth,
    )
    return DevAudit(sink, client, owns_client=owns_client)
