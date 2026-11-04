"""GW31 write_batch order, rollback rule, and the async queue."""

from __future__ import annotations

import asyncio

from gateway_v2.dispatch.rag_chunks import InMemoryChunkStore
from gateway_v2.dispatch.rag_embedding import DeterministicHashEmbedder
from gateway_v2.dispatch.rag_ingest import (
    IngestDoc,
    IngestJob,
    IngestQueue,
    InMemoryJobStore,
    write_batch,
)
from gateway_v2.dispatch.rag_retrieval import retrieve
from gateway_v2.dispatch.vector_registry import VectorRegistry
from gateway_v2.domain import posture
from gateway_v2.domain.rag import TrustLevel
from tests.dispatch.gw31_fakes import FakeAdapter

WIRE = "oorg_sdefault_sdocs"
DOCS = (IngestDoc("d0", "alpha beta", "s", TrustLevel.MEDIUM),)


def _parts(adapter: FakeAdapter, entries: int = 100):  # type: ignore[no-untyped-def]
    return (
        VectorRegistry({"s": adapter}),
        InMemoryChunkStore(entries, 10**6, 8),
        DeterministicHashEmbedder(16),
    )


def _write(reg, store, emb, docs=DOCS):  # type: ignore[no-untyped-def]
    return write_batch(
        reg, store, emb, store_id="s", org_id="org", wire=WIRE, docs=docs, timeout_s=5.0
    )


def test_failed_ensure_leaves_chunk_store_empty_and_no_upsert() -> None:
    async def go() -> None:
        adapter = FakeAdapter(create_code=posture.PROVIDER_UNREACHABLE)
        reg, store, emb = _parts(adapter)
        for _ in range(3):
            assert (await _write(reg, store, emb))[0] == posture.PROVIDER_UNREACHABLE
        assert len(store) == 0 and "upsert" not in adapter.calls

    asyncio.run(go())


def test_collection_exists_is_success() -> None:
    async def go() -> None:
        reg, store, emb = _parts(FakeAdapter(create_code=posture.COLLECTION_EXISTS))
        assert await _write(reg, store, emb) == (posture.VECTOR_OK, 1)

    asyncio.run(go())


def test_wrote_nothing_code_rolls_back_ambiguous_keeps() -> None:
    async def go() -> None:
        reg, store, emb = _parts(FakeAdapter(upsert_code=posture.UNSUPPORTED_OPERATION))
        assert (await _write(reg, store, emb))[0] == posture.UNSUPPORTED_OPERATION
        assert len(store) == 0
        reg, store, emb = _parts(FakeAdapter(upsert_code=posture.VECTOR_CAPACITY_EXCEEDED))
        assert (await _write(reg, store, emb))[0] == posture.VECTOR_CAPACITY_EXCEEDED
        assert len(store) == 1

    asyncio.run(go())


def test_writes_then_raises_keeps_entries_and_query_returns_documents() -> None:
    async def go() -> None:
        adapter = FakeAdapter(upsert_writes_then_fails=True)
        reg, store, emb = _parts(adapter)
        code, stored = await _write(reg, store, emb)
        assert code == posture.PROVIDER_UNREACHABLE and stored == 0
        vec = (await emb.embed("org", ("alpha beta",)))[0]
        out = await retrieve(
            reg,
            store,
            store_id="s",
            org_id="org",
            project="default",
            wire=WIRE,
            vector=vec,
            top_k=3,
            timeout_s=5.0,
        )
        assert out.ok and len(out.chunks) == 1

    asyncio.run(go())


def test_short_count_and_raise_never_report_stored() -> None:
    async def go() -> None:
        for adapter in (FakeAdapter(short_count=True), FakeAdapter(upsert_raises=True)):
            reg, store, emb = _parts(adapter)
            code, stored = await _write(reg, store, emb)
            assert code != posture.VECTOR_OK and stored == 0

    asyncio.run(go())


def test_chunk_store_full_fails_without_adapter_upsert() -> None:
    async def go() -> None:
        adapter = FakeAdapter()
        reg, store, emb = _parts(adapter, entries=0 + 1)
        two = DOCS + (IngestDoc("d1", "x y", "s", TrustLevel.LOW),)
        assert (await _write(reg, store, emb, two))[0] == posture.VECTOR_CAPACITY_EXCEEDED
        assert "upsert" not in adapter.calls

    asyncio.run(go())


def _queue(adapter, depth=2):  # type: ignore[no-untyped-def]
    reg, store, emb = _parts(adapter)
    jobs = InMemoryJobStore(10)
    tasks: list[asyncio.Task[None]] = []

    def spawn(coro):  # type: ignore[no-untyped-def]
        tasks.append(asyncio.ensure_future(coro))

    return (
        IngestQueue(
            registry=reg,
            chunk_store=store,
            embedder=emb,
            job_store=jobs,
            spawn=spawn,
            depth=depth,
            concurrency=1,
            timeout_s=5.0,
        ),
        jobs,
        tasks,
    )


def _job(i: int = 0) -> IngestJob:
    return IngestJob(f"j{i}", "org", "default", "s", WIRE, "accepted", 1)


# Feature: gw31-rag-pipeline, Property 8: stored only on an observed OK with matching count
def test_queue_runs_job_to_stored_and_bounds_depth() -> None:
    async def go() -> None:
        q, jobs, tasks = _queue(FakeAdapter())
        await jobs.put(_job(0))
        await jobs.put(_job(1))
        await jobs.put(_job(2))
        assert q.submit(_job(0), DOCS) and q.submit(_job(1), DOCS)
        assert not q.submit(_job(2), DOCS)
        await asyncio.gather(*tasks)
        assert (await jobs.get("j0")).state == "stored" and (await jobs.get("j0")).stored == 1  # type: ignore[union-attr]
        assert q.pending == 0

    asyncio.run(go())


def test_queue_failed_job_reports_code() -> None:
    async def go() -> None:
        q, jobs, tasks = _queue(FakeAdapter(upsert_raises=True))
        await jobs.put(_job(0))
        q.submit(_job(0), DOCS)
        await asyncio.gather(*tasks)
        job = await jobs.get("j0")
        assert job is not None and job.state == "failed" and job.stored == 0 and job.code

    asyncio.run(go())


def test_job_store_bounded_evicts_finished_only() -> None:
    async def go() -> None:
        s = InMemoryJobStore(1)
        assert await s.put(_job(0))
        assert not await s.put(_job(1))  # unfinished job is never evicted
        await s.update(IngestJob("j0", "org", "default", "s", WIRE, "stored", 1))
        assert await s.put(_job(1))

    asyncio.run(go())
