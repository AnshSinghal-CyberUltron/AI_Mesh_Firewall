"""GW31 retrieval: registry select, adapter query, ChunkStore resolution.

Only ``record.id`` is read from each returned :class:`VectorRecord`. The shipped GW30 transports
return empty metadata and not every one returns a vector, so chunk text, source, trust and the
relevance vector come from the :class:`ChunkStore`. A lost ChunkStore (``get_many`` unavailable,
or every returned id absent) is a declared refusal, never an empty success.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass

from gateway_v2.dispatch.rag_chunks import ChunkStore
from gateway_v2.dispatch.vector_adapter import AdapterResult, QueryRequest
from gateway_v2.dispatch.vector_registry import VectorRegistry
from gateway_v2.domain import posture
from gateway_v2.domain.rag import Chunk, ScoredChunk

__all__ = ("Retrieval", "listed_name", "retrieve")

_STRIPPED_PREFIX = "gw30_"


@dataclass(frozen=True, slots=True)
class Retrieval:
    code: str
    chunks: tuple[ScoredChunk, ...] = ()
    dropped_unscorable: int = 0

    @property
    def ok(self) -> bool:
        return self.code == posture.VECTOR_OK


def listed_name(raw: str) -> str | None:
    """Normalise a ``list_collections`` name: strip a leading ``Gw30_``/``gw30_`` (Weaviate)."""
    name = raw[len(_STRIPPED_PREFIX) :] if raw.lower().startswith(_STRIPPED_PREFIX) else raw
    if not name or any(ch not in "abcdefghijklmnopqrstuvwxyz0123456789_" for ch in name):
        return None
    return name


async def retrieve(
    registry: VectorRegistry,
    chunk_store: ChunkStore,
    *,
    store_id: str,
    org_id: str,
    project: str,
    wire: str,
    vector: tuple[float, ...],
    top_k: int,
    timeout_s: float,
) -> Retrieval:
    adapter = registry.select(store_id)
    if isinstance(adapter, AdapterResult):
        return Retrieval(adapter.code)
    request = QueryRequest(
        org_id=org_id, store_id=store_id, collection=wire, vector=vector, top_k=top_k
    )
    try:
        result = await asyncio.wait_for(adapter.query(request), timeout_s)
    except Exception:  # noqa: BLE001 - an adapter must not raise; if it does, fail closed
        return Retrieval(posture.PROVIDER_UNREACHABLE)
    if not result.ok:
        return Retrieval(result.code)
    if result.records is None:
        return Retrieval(posture.RAG_RETRIEVAL_INVALID)
    if not result.records:
        return Retrieval(posture.VECTOR_OK)
    ids = tuple(dict.fromkeys(record.id for record in result.records))
    try:
        found = await chunk_store.get_many(wire, ids)
    except Exception:  # noqa: BLE001
        return Retrieval(posture.RAG_CHUNK_STORE_UNAVAILABLE)
    if found is None or not found:
        return Retrieval(posture.RAG_CHUNK_STORE_UNAVAILABLE)
    chunks = tuple(
        ScoredChunk(
            Chunk(rid, e.text, e.vector, org_id, project, e.source, e.trust), 0.0, None
        )
        for rid in ids
        if (e := found.get(rid)) is not None
    )
    return Retrieval(posture.VECTOR_OK, chunks, len(ids) - len(chunks))
