"""Milvus over ``pymilvus`` (``MilvusClient``) — a real :class:`Vector_Transport` (GW30)."""

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
    collection_exists,
    records_from_scored,
    safe_name,
)
from gateway_v2.domain import posture

__all__ = ("MilvusConnectionParams", "MilvusTransport")

#: VARCHAR primary-key width for the string record ids (r1/r2/r3 round-trip).
_ID_MAX_LENGTH = 512


@dataclass(frozen=True, slots=True)
class MilvusConnectionParams:
    host: str
    port: int
    dimension: int


class MilvusTransport:
    """A real :class:`Vector_Transport` over Milvus (``pymilvus`` ``MilvusClient``)."""

    __slots__ = ("_params",)

    def __init__(self, params: MilvusConnectionParams) -> None:
        self._params = params

    def _client(self) -> Any:
        try:
            from pymilvus import MilvusClient
        except ImportError as exc:  # pragma: no cover - optional client
            raise VectorTransportError("pymilvus required: pip install pymilvus") from exc
        p = self._params
        try:
            return MilvusClient(uri=f"http://{p.host}:{p.port}", timeout=5)
        except Exception as exc:  # noqa: BLE001
            raise VectorTransportError(
                f"milvus connect error: {type(exc).__name__}: {exc}"
            ) from exc

    @staticmethod
    def _coll(collection: str) -> str:
        return "gw30_" + safe_name(collection)

    @staticmethod
    def _wrap(exc: Exception, op: str) -> VectorTransportError:
        return VectorTransportError(f"milvus {op} transport error: {type(exc).__name__}: {exc}")

    async def query(self, req: QueryRequest) -> AdapterResult:
        try:
            client = self._client()
            hits = client.search(
                collection_name=self._coll(req.collection),
                data=[list(req.vector)],
                limit=int(req.top_k),
                output_fields=["id"],
                search_params={"metric_type": "COSINE"},
            )
            empty_vec: tuple[float, ...] = ()
            scored = [
                (
                    float(h["distance"]),
                    str(h.get("id", (h.get("entity") or {}).get("id"))),
                    empty_vec,
                )
                for group in hits
                for h in group
            ]
        except Exception as exc:  # noqa: BLE001
            raise self._wrap(exc, "query") from exc
        return AdapterResult(
            code=posture.VECTOR_OK, records=records_from_scored(scored, int(req.top_k))
        )

    async def upsert(self, req: UpsertRequest) -> AdapterResult:
        try:
            client = self._client()
            client.upsert(
                collection_name=self._coll(req.collection),
                data=[{"id": r.id, "vector": list(r.vector)} for r in req.records],
            )
        except Exception as exc:  # noqa: BLE001
            raise self._wrap(exc, "upsert") from exc
        return AdapterResult(code=posture.VECTOR_OK, count=len(req.records))

    async def delete(self, req: DeleteRequest) -> AdapterResult:
        try:
            client = self._client()
            client.delete(collection_name=self._coll(req.collection), ids=list(req.ids))
        except Exception as exc:  # noqa: BLE001
            raise self._wrap(exc, "delete") from exc
        return AdapterResult(code=posture.VECTOR_OK, count=len(req.ids))

    async def list_collections(self, req: ListRequest) -> AdapterResult:
        try:
            client = self._client()
            names = tuple(
                sorted(
                    n[len("gw30_") :]
                    for n in client.list_collections()
                    if n.startswith("gw30_")
                )
            )
        except Exception as exc:  # noqa: BLE001
            raise self._wrap(exc, "list") from exc
        return AdapterResult(code=posture.VECTOR_OK, collections=names)

    async def create_collection(self, req: CreateRequest) -> AdapterResult:
        """Create the collection; an existing one is ``collection_exists`` and is not touched."""
        client = self._client()
        name = self._coll(req.collection)
        try:
            if client.has_collection(name):
                return collection_exists()
            client.create_collection(
                collection_name=name,
                dimension=int(req.dimension),
                id_type="string",
                max_length=_ID_MAX_LENGTH,
                metric_type="COSINE",
                auto_id=False,
            )
        except Exception as exc:  # noqa: BLE001
            if "already exist" in str(exc).lower():
                return collection_exists()
            raise self._wrap(exc, "create") from exc
        return AdapterResult(code=posture.VECTOR_OK, status="created")

    async def health(self, req: HealthRequest) -> AdapterResult:
        try:
            self._client().list_collections()
        except Exception as exc:  # noqa: BLE001
            raise self._wrap(exc, "health") from exc
        return AdapterResult(code=posture.VECTOR_OK, status="reachable")
