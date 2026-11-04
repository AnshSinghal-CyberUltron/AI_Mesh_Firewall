"""GW31 async ingest: job model, bounded job store, worker queue, and the one ``write_batch``.

``write_batch`` is the single write order shared by the async worker and ``/v1/vector/upsert``:
(1) ensure the collection, (2) embed, (3) ``ChunkStore.put_many``, (4) ``adapter.upsert``.
A failure at (1) or (2) writes nothing. After (4) the batch's entries are rolled back ONLY on a
definite wrote-nothing code; every ambiguous outcome keeps them, because a vector with no entry
is dropped at retrieval and can turn a query into ``rag_chunk_store_unavailable``.

"Durably queued" here means recorded in the injected :class:`IngestJobStore` (Clarification C-7);
it does not mean the job survives process exit unless the store does.
"""
from __future__ import annotations

import asyncio
import dataclasses
from collections import OrderedDict
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from types import MappingProxyType
from typing import Protocol

from gateway_v2.dispatch.rag_chunks import ChunkEntry, ChunkStore
from gateway_v2.dispatch.rag_embedding import Embedder
from gateway_v2.dispatch.vector_adapter import (
    AdapterResult,
    CreateRequest,
    UpsertRequest,
    VectorRecord,
)
from gateway_v2.dispatch.vector_registry import VectorRegistry
from gateway_v2.domain import posture
from gateway_v2.domain.rag import TrustLevel

__all__ = (
    "WROTE_NOTHING_CODES",
    "InMemoryJobStore",
    "IngestDoc",
    "IngestJob",
    "IngestJobStore",
    "IngestQueue",
    "write_batch",
)

WROTE_NOTHING_CODES = frozenset(
    {
        posture.UNSUPPORTED_OPERATION,
        posture.CREDENTIAL_UNAVAILABLE,
        posture.CREDENTIAL_SCOPE_MISMATCH,
        posture.ORG_IDENTITY_INVALID,
        posture.VECTOR_CAPACITY_UNSET,
    }
)

STATE_ACCEPTED = "accepted"
STATE_RUNNING = "running"
STATE_STORED = "stored"
STATE_FAILED = "failed"


@dataclass(frozen=True, slots=True)
class IngestDoc:
    doc_id: str
    text: str
    source: str
    trust: TrustLevel


@dataclass(frozen=True, slots=True)
class IngestJob:
    job_id: str
    org_id: str
    project: str
    store_id: str
    wire: str
    state: str
    total: int
    code: str = ""
    stored: int = 0


class IngestJobStore(Protocol):
    async def put(self, job: IngestJob) -> bool: ...
    async def get(self, job_id: str) -> IngestJob | None: ...
    async def update(self, job: IngestJob) -> bool: ...
    async def remove(self, job_id: str) -> None: ...


class InMemoryJobStore:
    """Bounded. Evicts the oldest FINISHED job only; refuses a put when none can be evicted."""

    def __init__(self, max_entries: int) -> None:
        if max_entries < 1:
            raise ValueError("job store bound must be positive")
        self._max = max_entries
        self._jobs: OrderedDict[str, IngestJob] = OrderedDict()

    async def put(self, job: IngestJob) -> bool:
        if len(self._jobs) >= self._max:
            victim = next(
                (k for k, v in self._jobs.items() if v.state in (STATE_STORED, STATE_FAILED)),
                None,
            )
            if victim is None:
                return False
            del self._jobs[victim]
        self._jobs[job.job_id] = job
        return True

    async def get(self, job_id: str) -> IngestJob | None:
        return self._jobs.get(job_id)

    async def update(self, job: IngestJob) -> bool:
        if job.job_id not in self._jobs:
            return False
        self._jobs[job.job_id] = job
        return True

    async def remove(self, job_id: str) -> None:
        self._jobs.pop(job_id, None)


async def write_batch(
    registry: VectorRegistry,
    chunk_store: ChunkStore,
    embedder: Embedder,
    *,
    store_id: str,
    org_id: str,
    wire: str,
    docs: tuple[IngestDoc, ...],
    timeout_s: float,
) -> tuple[str, int]:
    """Returns ``(code, stored)``. ``stored`` is non-zero only on an observed, matching OK."""
    adapter = registry.select(store_id)
    if isinstance(adapter, AdapterResult):
        return adapter.code, 0
    try:
        created = await asyncio.wait_for(
            adapter.create_collection(
                CreateRequest(org_id, store_id, wire, embedder.dimension, "cosine")
            ),
            timeout_s,
        )
    except Exception:  # noqa: BLE001
        return posture.PROVIDER_UNREACHABLE, 0
    if created.code not in (posture.VECTOR_OK, posture.COLLECTION_EXISTS):
        return created.code, 0
    try:
        vectors = await embedder.embed(org_id, tuple(d.text for d in docs))
    except Exception:  # noqa: BLE001
        vectors = None
    if (
        vectors is None
        or len(vectors) != len(docs)
        or any(len(v) != embedder.dimension for v in vectors)
    ):
        return posture.RAG_EMBEDDER_UNAVAILABLE, 0
    entries = {
        d.doc_id: ChunkEntry(d.text, v, d.source, d.trust)
        for d, v in zip(docs, vectors, strict=True)
    }
    if not await chunk_store.put_many(wire, entries):
        return posture.VECTOR_CAPACITY_EXCEEDED, 0
    records = tuple(
        VectorRecord(d.doc_id, v, MappingProxyType({})) for d, v in zip(docs, vectors, strict=True)
    )
    try:
        result = await asyncio.wait_for(
            adapter.upsert(UpsertRequest(org_id, store_id, wire, records)), timeout_s
        )
    except Exception:  # noqa: BLE001 - ambiguous: the adapter may have written; keep entries
        return posture.PROVIDER_UNREACHABLE, 0
    if not result.ok:
        if result.code in WROTE_NOTHING_CODES:
            await chunk_store.delete_many(wire, tuple(entries))
        return result.code, 0
    if result.count != len(records):
        return posture.PROVIDER_UNREACHABLE, 0
    return posture.VECTOR_OK, len(records)


Spawn = Callable[[Coroutine[object, object, None]], object]


class IngestQueue:
    """Non-blocking submit; a bounded semaphore limits running jobs."""

    def __init__(
        self,
        *,
        registry: VectorRegistry,
        chunk_store: ChunkStore,
        embedder: Embedder,
        job_store: IngestJobStore,
        spawn: Spawn,
        depth: int,
        concurrency: int,
        timeout_s: float,
    ) -> None:
        self._registry = registry
        self._chunks = chunk_store
        self._embedder = embedder
        self._jobs = job_store
        self._spawn = spawn
        self._depth = depth
        self._sem = asyncio.Semaphore(concurrency)
        self._timeout_s = timeout_s
        self._pending = 0

    @property
    def pending(self) -> int:
        return self._pending

    def submit(self, job: IngestJob, docs: tuple[IngestDoc, ...]) -> bool:
        """``False`` when the queue is full. Never blocks."""
        if self._pending >= self._depth:
            return False
        self._pending += 1
        self._spawn(self._run(job, docs))
        return True

    async def _run(self, job: IngestJob, docs: tuple[IngestDoc, ...]) -> None:
        try:
            async with self._sem:
                await self._jobs.update(dataclasses.replace(job, state=STATE_RUNNING))
                code, stored = await write_batch(
                    self._registry,
                    self._chunks,
                    self._embedder,
                    store_id=job.store_id,
                    org_id=job.org_id,
                    wire=job.wire,
                    docs=docs,
                    timeout_s=self._timeout_s,
                )
                done = code == posture.VECTOR_OK and stored == len(docs)
                final = dataclasses.replace(
                    job,
                    state=STATE_STORED if done else STATE_FAILED,
                    code="" if done else code,
                    stored=stored if done else 0,
                )
                await self._jobs.update(final)
        except Exception:  # noqa: BLE001 - a lost job is reported failed, never stored
            await self._jobs.update(
                dataclasses.replace(job, state=STATE_FAILED, code=posture.PROVIDER_UNREACHABLE)
            )
        finally:
            self._pending -= 1
