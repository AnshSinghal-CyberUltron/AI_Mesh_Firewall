"""GW31 retrieval over an adapter that drops metadata (the shipped GW30 behaviour)."""

from __future__ import annotations

import asyncio

import pytest

from gateway_v2.dispatch.rag_chunks import ChunkEntry, InMemoryChunkStore
from gateway_v2.dispatch.rag_embedding import DeterministicHashEmbedder
from gateway_v2.dispatch.rag_ingest import IngestDoc, write_batch
from gateway_v2.dispatch.rag_retrieval import listed_name, retrieve
from gateway_v2.dispatch.vector_registry import VectorRegistry
from gateway_v2.domain import posture
from gateway_v2.domain.rag import TrustLevel
from tests.dispatch.gw31_fakes import FakeAdapter

WIRE = "oorg_sdefault_sdocs"


def _setup(adapter: FakeAdapter):  # type: ignore[no-untyped-def]
    return (
        VectorRegistry({"s": adapter}),
        InMemoryChunkStore(100, 10**6, 8),
        DeterministicHashEmbedder(16),
    )


async def _ingest(reg, store, emb, texts):  # type: ignore[no-untyped-def]
    docs = tuple(IngestDoc(f"d{i}", t, "src", TrustLevel.MEDIUM) for i, t in enumerate(texts))
    return await write_batch(
        reg, store, emb, store_id="s", org_id="org", wire=WIRE, docs=docs, timeout_s=5.0
    )


async def _query(reg, store, emb, text, top_k=5):  # type: ignore[no-untyped-def]
    vec = (await emb.embed("org", (text,)))[0]
    return await retrieve(
        reg,
        store,
        store_id="s",
        org_id="org",
        project="default",
        wire=WIRE,
        vector=vec,
        top_k=top_k,
        timeout_s=5.0,
    )


@pytest.mark.parametrize("return_vectors", [False, True])
def test_context_populated_without_metadata_or_vectors(return_vectors: bool) -> None:
    async def go() -> None:
        reg, store, emb = _setup(FakeAdapter(return_vectors=return_vectors))
        assert await _ingest(reg, store, emb, ["alpha beta gamma", "delta epsilon"]) == (
            posture.VECTOR_OK,
            2,
        )
        out = await _query(reg, store, emb, "alpha beta gamma")
        assert out.ok and {c.chunk.text for c in out.chunks} == {
            "alpha beta gamma",
            "delta epsilon",
        }
        assert all(c.chunk.org_id == "org" and c.chunk.project == "default" for c in out.chunks)

    asyncio.run(go())


def test_all_miss_is_declared_error_partial_miss_drops_and_counts() -> None:
    async def go() -> None:
        adapter = FakeAdapter()
        reg, store, emb = _setup(adapter)
        await _ingest(reg, store, emb, ["one two", "three four"])
        await store.delete_many(WIRE, ("d0",))
        part = await _query(reg, store, emb, "one two")
        assert part.ok and len(part.chunks) == 1 and part.dropped_unscorable == 1
        await store.delete_many(WIRE, ("d1",))
        lost = await _query(reg, store, emb, "one two")
        assert lost.code == posture.RAG_CHUNK_STORE_UNAVAILABLE and lost.chunks == ()

    asyncio.run(go())


def test_empty_adapter_result_is_success() -> None:
    async def go() -> None:
        reg, store, emb = _setup(FakeAdapter())
        await reg.select("s").create_collection(
            __import__("gateway_v2.dispatch.vector_adapter", fromlist=["x"]).CreateRequest(
                "org", "s", WIRE, 16, "cosine"
            )
        )  # type: ignore[union-attr]
        out = await _query(reg, store, emb, "anything")
        assert out.ok and out.chunks == ()

    asyncio.run(go())


def test_store_unconfigured_and_adapter_failure_propagate() -> None:
    async def go() -> None:
        reg, store, emb = _setup(FakeAdapter())
        vec = (await emb.embed("org", ("x",)))[0]
        miss = await retrieve(
            reg,
            store,
            store_id="nope",
            org_id="org",
            project="default",
            wire=WIRE,
            vector=vec,
            top_k=3,
            timeout_s=5.0,
        )
        assert miss.code == posture.STORE_NOT_CONFIGURED
        down = await _query(reg, store, emb, "x")  # collection never created
        assert down.code == posture.PROVIDER_UNREACHABLE

    asyncio.run(go())


class _LostStore(InMemoryChunkStore):
    async def get_many(self, collection, ids):  # type: ignore[no-untyped-def]
        return None


def test_unavailable_chunk_store_is_declared_error() -> None:
    async def go() -> None:
        adapter = FakeAdapter()
        reg, store, emb = _setup(adapter)
        await _ingest(reg, store, emb, ["a b"])
        lost = _LostStore(10, 10**6, 8)
        vec = (await emb.embed("org", ("a b",)))[0]
        out = await retrieve(
            reg,
            lost,
            store_id="s",
            org_id="org",
            project="default",
            wire=WIRE,
            vector=vec,
            top_k=3,
            timeout_s=5.0,
        )
        assert out.code == posture.RAG_CHUNK_STORE_UNAVAILABLE

    asyncio.run(go())


def test_listed_name() -> None:
    assert listed_name("Gw30_oa_sb_sc") == "oa_sb_sc"
    assert listed_name("gw30_oa_sb_sc") == "oa_sb_sc"
    assert listed_name("oa_sb_sc") == "oa_sb_sc"
    assert listed_name("BAD-name") is None and listed_name("") is None


def test_chunk_entry_shape() -> None:
    assert ChunkEntry("t", (1.0,), "s", TrustLevel.LOW).trust is TrustLevel.LOW
