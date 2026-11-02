"""Qdrant over ``qdrant-client`` — a real :class:`Vector_Transport` (GW30)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from gateway_v2.dispatch.vector_adapter import (
    AdapterResult,
    CreateRequest,
    DeleteRequest,
    HealthRequest,
    ListRequest,
    QueryRequest,
    UpsertRequest,
)
from gateway_v2.dispatch.vector_bounded_op import VectorTransportError
from gateway_v2.dispatch.vector_transports._common import (
    as_point_id,
    collection_exists,
    records_from_scored,
)
from gateway_v2.domain import posture

__all__ = ("QdrantConnectionParams", "QdrantTransport")

#: The HTTP status Qdrant answers when a create names an existing collection.
_CONFLICT = 409


@dataclass(frozen=True, slots=True)
class QdrantConnectionParams:
    host: str
    port: int
    dimension: int


class QdrantTransport:
    """A real :class:`Vector_Transport` over Qdrant (``qdrant-client``)."""

    __slots__ = ("_params",)

    def __init__(self, params: QdrantConnectionParams) -> None:
        self._params = params

    def _client(self) -> Any:
        try:
            from qdrant_client import QdrantClient
        except ImportError as exc:  # pragma: no cover - optional client
            raise VectorTransportError("qdrant-client required: pip install qdrant-client") from exc
        return QdrantClient(host=self._params.host, port=self._params.port, timeout=5)

    @staticmethod
    def _wrap(exc: Exception, op: str) -> VectorTransportError:
        return VectorTransportError(f"qdrant {op} transport error: {type(exc).__name__}: {exc}")

    async def query(self, req: QueryRequest) -> AdapterResult:
        try:
            client = self._client()
            hits = client.query_points(
                collection_name=req.collection,
                query=list(req.vector),
                limit=int(req.top_k),
                with_vectors=True,
            ).points
        except Exception as exc:  # noqa: BLE001
            raise self._wrap(exc, "query") from exc
        scored = [
            (
                float(h.score),
                str((h.payload or {}).get("rid", h.id)),
                tuple(float(x) for x in (h.vector or ())),
            )
            for h in hits
        ]
        return AdapterResult(
            code=posture.VECTOR_OK, records=records_from_scored(scored, int(req.top_k))
        )

    async def upsert(self, req: UpsertRequest) -> AdapterResult:
        try:
            from qdrant_client import models

            client = self._client()
            points = [
                models.PointStruct(
                    id=as_point_id(r.id), vector=list(r.vector), payload={"rid": r.id}
                )
                for r in req.records
            ]
            client.upsert(collection_name=req.collection, points=points, wait=True)
        except Exception as exc:  # noqa: BLE001
            raise self._wrap(exc, "upsert") from exc
        return AdapterResult(code=posture.VECTOR_OK, count=len(req.records))

    async def delete(self, req: DeleteRequest) -> AdapterResult:
        try:
            from qdrant_client import models

            client = self._client()
            client.delete(
                collection_name=req.collection,
                points_selector=models.PointIdsList(points=[as_point_id(i) for i in req.ids]),
                wait=True,
            )
        except Exception as exc:  # noqa: BLE001
            raise self._wrap(exc, "delete") from exc
        return AdapterResult(code=posture.VECTOR_OK, count=len(req.ids))

    async def list_collections(self, req: ListRequest) -> AdapterResult:
        try:
            client = self._client()
            names = tuple(sorted(c.name for c in client.get_collections().collections))
        except Exception as exc:  # noqa: BLE001
            raise self._wrap(exc, "list") from exc
        return AdapterResult(code=posture.VECTOR_OK, collections=names)

    async def create_collection(self, req: CreateRequest) -> AdapterResult:
        """Create the collection; Qdrant's 409 for an existing one is ``collection_exists``."""
        client = self._client()
        from qdrant_client import models
        from qdrant_client.http.exceptions import UnexpectedResponse

        try:
            client.create_collection(
                collection_name=req.collection,
                vectors_config=models.VectorParams(
                    size=int(req.dimension), distance=models.Distance.COSINE
                ),
            )
        except UnexpectedResponse as exc:
            if exc.status_code == _CONFLICT or "already exists" in str(exc).lower():
                return collection_exists()
            raise self._wrap(exc, "create") from exc
        except Exception as exc:  # noqa: BLE001
            if "already exists" in str(exc).lower():
                return collection_exists()
            raise self._wrap(exc, "create") from exc
        return AdapterResult(code=posture.VECTOR_OK, status="created")

    async def health(self, req: HealthRequest) -> AdapterResult:
        try:
            self._client().get_collections()
        except Exception as exc:  # noqa: BLE001
            raise self._wrap(exc, "health") from exc
        return AdapterResult(code=posture.VECTOR_OK, status="reachable")
