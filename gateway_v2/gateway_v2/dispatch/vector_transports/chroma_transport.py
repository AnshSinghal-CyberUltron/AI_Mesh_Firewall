"""Chroma over the ``chromadb`` HttpClient — a real :class:`Vector_Transport` (GW30)."""

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

__all__ = ("ChromaConnectionParams", "ChromaTransport")


@dataclass(frozen=True, slots=True)
class ChromaConnectionParams:
    host: str
    port: int
    dimension: int


class ChromaTransport:
    """A real :class:`Vector_Transport` over Chroma (``chromadb`` HttpClient)."""

    __slots__ = ("_params",)

    def __init__(self, params: ChromaConnectionParams) -> None:
        self._params = params

    def _client(self) -> Any:
        try:
            import chromadb
        except ImportError as exc:  # pragma: no cover
            raise VectorTransportError("chromadb required: pip install chromadb") from exc
        try:
            return chromadb.HttpClient(host=self._params.host, port=self._params.port)
        except Exception as exc:  # noqa: BLE001
            raise VectorTransportError(f"chroma connect error: {type(exc).__name__}") from exc

    @staticmethod
    def _name(collection: str) -> str:
        return "gw30_" + safe_name(collection)

    @staticmethod
    def _wrap(exc: Exception, op: str) -> VectorTransportError:
        return VectorTransportError(f"chroma {op} transport error: {type(exc).__name__}: {exc}")

    async def query(self, req: QueryRequest) -> AdapterResult:
        try:
            coll = self._client().get_collection(self._name(req.collection))
            res = coll.query(
                query_embeddings=[list(req.vector)],
                n_results=int(req.top_k),
                include=["embeddings", "distances"],
            )
            ids = res["ids"][0]
            embs = res["embeddings"][0]
            dists = res["distances"][0]
            scored = [
                (1.0 - float(dists[i]), str(ids[i]), tuple(float(x) for x in embs[i]))
                for i in range(len(ids))
            ]
        except Exception as exc:  # noqa: BLE001
            raise self._wrap(exc, "query") from exc
        return AdapterResult(
            code=posture.VECTOR_OK, records=records_from_scored(scored, int(req.top_k))
        )

    async def upsert(self, req: UpsertRequest) -> AdapterResult:
        try:
            coll = self._client().get_collection(self._name(req.collection))
            coll.upsert(
                ids=[r.id for r in req.records], embeddings=[list(r.vector) for r in req.records]
            )
        except Exception as exc:  # noqa: BLE001
            raise self._wrap(exc, "upsert") from exc
        return AdapterResult(code=posture.VECTOR_OK, count=len(req.records))

    async def delete(self, req: DeleteRequest) -> AdapterResult:
        try:
            coll = self._client().get_collection(self._name(req.collection))
            coll.delete(ids=list(req.ids))
        except Exception as exc:  # noqa: BLE001
            raise self._wrap(exc, "delete") from exc
        return AdapterResult(code=posture.VECTOR_OK, count=len(req.ids))

    async def list_collections(self, req: ListRequest) -> AdapterResult:
        try:
            cols = self._client().list_collections()
            names = tuple(
                sorted(
                    (c.name if hasattr(c, "name") else str(c))[len("gw30_") :]
                    for c in cols
                    if (c.name if hasattr(c, "name") else str(c)).startswith("gw30_")
                )
            )
        except Exception as exc:  # noqa: BLE001
            raise self._wrap(exc, "list") from exc
        return AdapterResult(code=posture.VECTOR_OK, collections=names)

    async def create_collection(self, req: CreateRequest) -> AdapterResult:
        """Create the collection; Chroma's unique-name error is ``collection_exists``."""
        try:
            self._client().create_collection(
                name=self._name(req.collection), metadata={"hnsw:space": "cosine"}
            )
        except Exception as exc:  # noqa: BLE001
            if "already exists" in str(exc).lower():
                return collection_exists()
            raise self._wrap(exc, "create") from exc
        return AdapterResult(code=posture.VECTOR_OK, status="created")

    async def health(self, req: HealthRequest) -> AdapterResult:
        try:
            self._client().heartbeat()
        except Exception as exc:  # noqa: BLE001
            raise self._wrap(exc, "health") from exc
        return AdapterResult(code=posture.VECTOR_OK, status="reachable")
