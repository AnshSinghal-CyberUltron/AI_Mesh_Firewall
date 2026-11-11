"""Pinecone serverless over the ``pinecone`` SDK — a real :class:`Vector_Transport` (GW30).

Pinecone's unit of storage is an *index*; a serverless account caps the index count, so this
transport maps every adapter ``collection`` onto a Pinecone **namespace** inside ONE dedicated
gate index (``PineconeConnectionParams.index_name``). ``create_collection`` ensures that index
exists and NEVER resets it (an existing index is the declared ``collection_exists``); the other
ops operate within the namespace derived from the request collection. The SDK resolves each
index's host from the serverless key, so ``PineconeConnectionParams`` carries NO host.
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
    records_from_scored,
    safe_name,
)
from gateway_v2.domain import posture

__all__ = ("PineconeConnectionParams", "PineconeTransport")


@dataclass(frozen=True, slots=True)
class PineconeConnectionParams:
    """Connection params for the Pinecone serverless transport.

    ``api_key`` is the serverless key (the SDK resolves each index's host from it, so there is
    NO host field — D3); ``index_name`` is the ONE dedicated gate index every adapter
    ``collection`` is namespaced inside (D4); ``cloud``/``region`` locate a newly-created
    serverless index. The key is held opaquely: this module adds no ``__repr__`` that prints it.
    """

    api_key: str
    index_name: str
    dimension: int
    cloud: str
    region: str


class PineconeTransport:
    """A real :class:`Vector_Transport` over Pinecone serverless (``pinecone`` SDK)."""

    __slots__ = ("_params",)

    def __init__(self, params: PineconeConnectionParams) -> None:
        self._params = params

    def _client(self) -> Any:
        try:
            from pinecone import Pinecone
        except ImportError as exc:  # pragma: no cover - optional client
            raise VectorTransportError("pinecone required: pip install pinecone") from exc
        try:
            return Pinecone(api_key=self._params.api_key)
        except Exception as exc:  # noqa: BLE001
            raise VectorTransportError(
                f"pinecone connect error: {type(exc).__name__}: {exc}"
            ) from exc

    def _index(self, client: Any) -> Any:
        # The SDK resolves the per-index host from the index name (D3). A missing/invalid index
        # makes this raise, which _wrap maps to provider_unreachable.
        return client.Index(name=self._params.index_name)

    @staticmethod
    def _namespace(collection: str) -> str:
        return safe_name(collection)

    @staticmethod
    def _wrap(exc: Exception, op: str) -> VectorTransportError:
        return VectorTransportError(
            f"pinecone {op} transport error: {type(exc).__name__}: {exc}"
        )

    async def query(self, req: QueryRequest) -> AdapterResult:
        try:
            client = self._client()
            index = self._index(client)
            resp = index.query(
                vector=list(req.vector),
                top_k=int(req.top_k),
                include_values=True,
                namespace=self._namespace(req.collection),
            )
            scored = [
                (
                    float(m["score"]),
                    str(m["id"]),
                    tuple(float(x) for x in (m.get("values") or ())),
                )
                for m in resp.get("matches", [])
            ]
        except Exception as exc:  # noqa: BLE001
            raise self._wrap(exc, "query") from exc
        return AdapterResult(
            code=posture.VECTOR_OK, records=records_from_scored(scored, int(req.top_k))
        )

    async def upsert(self, req: UpsertRequest) -> AdapterResult:
        try:
            client = self._client()
            index = self._index(client)
            index.upsert(
                vectors=[(r.id, list(r.vector)) for r in req.records],
                namespace=self._namespace(req.collection),
            )
        except Exception as exc:  # noqa: BLE001
            raise self._wrap(exc, "upsert") from exc
        return AdapterResult(code=posture.VECTOR_OK, count=len(req.records))

    async def delete(self, req: DeleteRequest) -> AdapterResult:
        try:
            client = self._client()
            index = self._index(client)
            index.delete(
                ids=list(req.ids), namespace=self._namespace(req.collection)
            )
        except Exception as exc:  # noqa: BLE001
            raise self._wrap(exc, "delete") from exc
        return AdapterResult(code=posture.VECTOR_OK, count=len(req.ids))

    async def list_collections(self, req: ListRequest) -> AdapterResult:
        try:
            client = self._client()
            index = self._index(client)
            stats = index.describe_index_stats()
            # One in-memory response: iterate its ``namespaces`` mapping (the org's own index).
            # This is not a store KEYS command, so it does not enumerate the keyspace.
            names = tuple(sorted(stats.get("namespaces", {})))
        except Exception as exc:  # noqa: BLE001
            raise self._wrap(exc, "list") from exc
        return AdapterResult(code=posture.VECTOR_OK, collections=names)

    async def create_collection(self, req: CreateRequest) -> AdapterResult:
        """Ensure the ONE gate index exists; an existing index is ``collection_exists``.

        NON-DESTRUCTIVE: an already-present index is never reset or deleted (R2.9).
        """
        client = self._client()
        name = self._params.index_name
        try:
            from pinecone import ServerlessSpec

            if client.has_index(name):
                return collection_exists()
            client.create_index(
                name=name,
                dimension=int(req.dimension),
                metric="cosine",
                spec=ServerlessSpec(
                    cloud=self._params.cloud, region=self._params.region
                ),
            )
        except Exception as exc:  # noqa: BLE001
            if "already exist" in str(exc).lower():
                return collection_exists()
            raise self._wrap(exc, "create") from exc
        return AdapterResult(code=posture.VECTOR_OK, status="created")

    async def health(self, req: HealthRequest) -> AdapterResult:
        try:
            self._client().list_indexes()
        except Exception as exc:  # noqa: BLE001
            raise self._wrap(exc, "health") from exc
        return AdapterResult(code=posture.VECTOR_OK, status="reachable")
