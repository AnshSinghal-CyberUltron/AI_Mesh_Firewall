"""In-process vector adapter for the dev profile and local runs. NOT a production connector.

It satisfies the GW30 ``Vector_Adapter`` protocol over a plain dict, so the RAG routes can be
exercised end to end with no external store. Like the shipped transports it returns records with
empty metadata and no vector, which is why GW31 reads chunk content from the ChunkStore. Data
lives in this one process and is lost on exit.
"""
from __future__ import annotations

from types import MappingProxyType

from gateway_v2.dispatch.rag_ranker import cosine
from gateway_v2.dispatch.vector_adapter import (
    AdapterResult,
    Capabilities,
    CreateRequest,
    DeleteRequest,
    HealthRequest,
    ListRequest,
    QueryRequest,
    UpsertRequest,
    VectorRecord,
)
from gateway_v2.domain import posture

__all__ = ("InMemoryVectorAdapter",)


class InMemoryVectorAdapter:
    def __init__(self) -> None:
        self._data: dict[str, dict[str, tuple[float, ...]]] = {}

    def capabilities(self) -> Capabilities:
        return Capabilities(filters=False, namespaces=False, hybrid_search=False)

    async def query(self, req: QueryRequest) -> AdapterResult:
        coll = self._data.get(req.collection)
        if coll is None:
            return AdapterResult(posture.PROVIDER_UNREACHABLE)
        scored = sorted(
            (-(c if (c := cosine(req.vector, vec)) is not None else -2.0), rid)
            for rid, vec in coll.items()
        )
        records = tuple(
            VectorRecord(rid, (), MappingProxyType({})) for _, rid in scored[: req.top_k]
        )
        return AdapterResult(posture.VECTOR_OK, records=records)

    async def upsert(self, req: UpsertRequest) -> AdapterResult:
        coll = self._data.get(req.collection)
        if coll is None:
            return AdapterResult(posture.PROVIDER_UNREACHABLE)
        for record in req.records:
            coll[record.id] = record.vector
        return AdapterResult(posture.VECTOR_OK, count=len(req.records))

    async def delete(self, req: DeleteRequest) -> AdapterResult:
        coll = self._data.get(req.collection, {})
        removed = sum(1 for rid in req.ids if coll.pop(rid, None) is not None)
        return AdapterResult(posture.VECTOR_OK, count=removed)

    async def list_collections(self, req: ListRequest) -> AdapterResult:
        return AdapterResult(posture.VECTOR_OK, collections=tuple(self._data))

    async def create_collection(self, req: CreateRequest) -> AdapterResult:
        if req.collection in self._data:
            return AdapterResult(posture.COLLECTION_EXISTS)
        self._data[req.collection] = {}
        return AdapterResult(posture.VECTOR_OK)

    async def health(self, req: HealthRequest) -> AdapterResult:
        return AdapterResult(posture.VECTOR_OK, status="reachable")
