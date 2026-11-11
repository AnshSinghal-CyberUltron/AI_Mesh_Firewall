"""In-process vector adapter that behaves like the shipped GW30 transports: empty metadata."""

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


class FakeAdapter:
    """Collections must be created first. ``metadata`` is always empty; ``vector`` optional."""

    def __init__(
        self,
        *,
        return_vectors: bool = False,
        create_code: str = posture.VECTOR_OK,
        upsert_code: str | None = None,
        upsert_raises: bool = False,
        upsert_writes_then_fails: bool = False,
        short_count: bool = False,
    ) -> None:
        self.data: dict[str, dict[str, tuple[float, ...]]] = {}
        self.calls: list[str] = []
        self.return_vectors = return_vectors
        self.create_code = create_code
        self.upsert_code = upsert_code
        self.upsert_raises = upsert_raises
        self.upsert_writes_then_fails = upsert_writes_then_fails
        self.short_count = short_count

    def capabilities(self) -> Capabilities:
        return Capabilities(filters=False, namespaces=False, hybrid_search=False)

    async def query(self, req: QueryRequest) -> AdapterResult:
        self.calls.append("query")
        if req.collection not in self.data:
            return AdapterResult(posture.PROVIDER_UNREACHABLE)
        scored = []
        for rid, vec in self.data[req.collection].items():
            cos = cosine(req.vector, vec)
            scored.append((-(cos if cos is not None else -2.0), rid, vec))
        scored.sort()
        records = tuple(
            VectorRecord(rid, vec if self.return_vectors else (), MappingProxyType({}))
            for _, rid, vec in scored[: req.top_k]
        )
        return AdapterResult(posture.VECTOR_OK, records=records)

    async def upsert(self, req: UpsertRequest) -> AdapterResult:
        self.calls.append("upsert")
        if self.upsert_writes_then_fails:
            self.data.setdefault(req.collection, {}).update({r.id: r.vector for r in req.records})
            raise RuntimeError("boom after write")
        if self.upsert_raises:
            raise RuntimeError("boom")
        if self.upsert_code is not None:
            return AdapterResult(self.upsert_code)
        self.data.setdefault(req.collection, {}).update({r.id: r.vector for r in req.records})
        n = len(req.records) - (1 if self.short_count else 0)
        return AdapterResult(posture.VECTOR_OK, count=n)

    async def delete(self, req: DeleteRequest) -> AdapterResult:
        self.calls.append("delete")
        coll = self.data.get(req.collection, {})
        n = 0
        for rid in req.ids:
            n += 1 if coll.pop(rid, None) is not None else 0
        return AdapterResult(posture.VECTOR_OK, count=n)

    async def list_collections(self, req: ListRequest) -> AdapterResult:
        self.calls.append("list")
        return AdapterResult(posture.VECTOR_OK, collections=tuple(self.data))

    async def create_collection(self, req: CreateRequest) -> AdapterResult:
        self.calls.append("create")
        if self.create_code in (posture.VECTOR_OK, posture.COLLECTION_EXISTS):
            self.data.setdefault(req.collection, {})
        return AdapterResult(self.create_code)

    async def health(self, req: HealthRequest) -> AdapterResult:
        return AdapterResult(posture.VECTOR_OK, status="reachable")
