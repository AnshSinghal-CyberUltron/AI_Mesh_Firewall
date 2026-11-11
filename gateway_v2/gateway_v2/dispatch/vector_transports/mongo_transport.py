"""MongoDB Atlas Local over ``pymongo`` — a real :class:`Vector_Transport` (GW30).

``query`` scores the collection in the client (``find({})``) because Atlas ``$vectorSearch``
needs a search index that builds asynchronously. That is O(collection) per query; it is an open
GW30 finding (docs/plans/2026-11-02-gw30-gate-fix.md), not a production shape.
"""

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
    cosine_similarity,
    records_from_scored,
    safe_name,
)
from gateway_v2.domain import posture

__all__ = ("MongoAtlasConnectionParams", "MongoAtlasTransport")


@dataclass(frozen=True, slots=True)
class MongoAtlasConnectionParams:
    host: str
    port: int
    dimension: int
    dbname: str = "gw30"


class MongoAtlasTransport:
    """A real :class:`Vector_Transport` over MongoDB Atlas Local (``pymongo``)."""

    __slots__ = ("_params",)

    def __init__(self, params: MongoAtlasConnectionParams) -> None:
        self._params = params

    def _db(self) -> Any:
        try:
            from pymongo import MongoClient
        except ImportError as exc:  # pragma: no cover
            raise VectorTransportError("pymongo required: pip install pymongo") from exc
        p = self._params
        client: Any = MongoClient(
            host=p.host,
            port=p.port,
            serverSelectionTimeoutMS=5000,
            connectTimeoutMS=5000,
            directConnection=True,
        )
        return client[p.dbname]

    @staticmethod
    def _coll(collection: str) -> str:
        return "gw30_" + safe_name(collection)

    @staticmethod
    def _wrap(exc: Exception, op: str) -> VectorTransportError:
        return VectorTransportError(
            f"mongodb_atlas {op} transport error: {type(exc).__name__}: {exc}"
        )

    async def query(self, req: QueryRequest) -> AdapterResult:
        try:
            coll = self._db()[self._coll(req.collection)]
            scored = []
            for doc in coll.find({}):
                vec = tuple(float(x) for x in doc.get("embedding", ()))
                scored.append((cosine_similarity(req.vector, vec), str(doc["_id"]), vec))
        except Exception as exc:  # noqa: BLE001
            raise self._wrap(exc, "query") from exc
        return AdapterResult(
            code=posture.VECTOR_OK, records=records_from_scored(scored, int(req.top_k))
        )

    async def upsert(self, req: UpsertRequest) -> AdapterResult:
        try:
            coll = self._db()[self._coll(req.collection)]
            for r in req.records:
                coll.replace_one(
                    {"_id": r.id}, {"_id": r.id, "embedding": list(r.vector)}, upsert=True
                )
        except Exception as exc:  # noqa: BLE001
            raise self._wrap(exc, "upsert") from exc
        return AdapterResult(code=posture.VECTOR_OK, count=len(req.records))

    async def delete(self, req: DeleteRequest) -> AdapterResult:
        try:
            coll = self._db()[self._coll(req.collection)]
            res = coll.delete_many({"_id": {"$in": list(req.ids)}})
            removed = int(res.deleted_count)
        except Exception as exc:  # noqa: BLE001
            raise self._wrap(exc, "delete") from exc
        return AdapterResult(code=posture.VECTOR_OK, count=removed)

    async def list_collections(self, req: ListRequest) -> AdapterResult:
        try:
            names = tuple(
                sorted(
                    n[len("gw30_") :]
                    for n in self._db().list_collection_names()
                    if n.startswith("gw30_")
                )
            )
        except Exception as exc:  # noqa: BLE001
            raise self._wrap(exc, "list") from exc
        return AdapterResult(code=posture.VECTOR_OK, collections=names)

    async def create_collection(self, req: CreateRequest) -> AdapterResult:
        """Create the collection; pymongo's exists error is ``collection_exists``."""
        try:
            self._db().create_collection(self._coll(req.collection))
        except Exception as exc:  # noqa: BLE001
            if "already exists" in str(exc).lower():
                return collection_exists()
            raise self._wrap(exc, "create") from exc
        return AdapterResult(code=posture.VECTOR_OK, status="created")

    async def health(self, req: HealthRequest) -> AdapterResult:
        try:
            self._db().command("ping")
        except Exception as exc:  # noqa: BLE001
            raise self._wrap(exc, "health") from exc
        return AdapterResult(code=posture.VECTOR_OK, status="reachable")
