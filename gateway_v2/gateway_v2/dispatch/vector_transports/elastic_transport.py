"""Elasticsearch 8 and OpenSearch 2 kNN — real :class:`Vector_Transport` clients (GW30)."""

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

__all__ = (
    "ElasticsearchConnectionParams",
    "ElasticsearchTransport",
    "OpenSearchConnectionParams",
    "OpenSearchTransport",
)

#: The error type both engines return when an index create names an existing index.
_ALREADY_EXISTS = "resource_already_exists_exception"


@dataclass(frozen=True, slots=True)
class ElasticsearchConnectionParams:
    host: str
    port: int
    dimension: int


@dataclass(frozen=True, slots=True)
class OpenSearchConnectionParams:
    host: str
    port: int
    dimension: int


class _EsLikeTransport:
    """Shared kNN logic for Elasticsearch and OpenSearch (near-identical wire shapes)."""

    __slots__ = ("_host", "_port", "_dim")

    def __init__(self, host: str, port: int, dimension: int) -> None:
        self._host = host
        self._port = port
        self._dim = dimension

    def _client(self) -> Any:  # pragma: no cover - overridden
        raise NotImplementedError

    def _store(self) -> str:  # pragma: no cover - overridden
        raise NotImplementedError

    @staticmethod
    def _index(collection: str) -> str:
        return "gw30_" + safe_name(collection).lower()

    def _wrap(self, exc: Exception, op: str) -> VectorTransportError:
        return VectorTransportError(
            f"{self._store()} {op} transport error: {type(exc).__name__}: {exc}"
        )

    def _create_index(self, req: CreateRequest, **spec: Any) -> AdapterResult:
        """Create atomically; the engine's already-exists error is ``collection_exists``."""
        try:
            self._client().indices.create(index=self._index(req.collection), **spec)
        except Exception as exc:  # noqa: BLE001
            if _ALREADY_EXISTS in str(exc):
                return collection_exists()
            raise self._wrap(exc, "create") from exc
        return AdapterResult(code=posture.VECTOR_OK, status="created")

    async def list_collections(self, req: ListRequest) -> AdapterResult:
        try:
            idx = self._client().indices.get(index="gw30_*", ignore_unavailable=True)
            names = tuple(sorted(k[len("gw30_") :] for k in idx))
        except Exception as exc:  # noqa: BLE001
            raise self._wrap(exc, "list") from exc
        return AdapterResult(code=posture.VECTOR_OK, collections=names)

    async def delete(self, req: DeleteRequest) -> AdapterResult:
        removed = 0
        try:
            client = self._client()
            for rid in req.ids:
                try:
                    client.delete(index=self._index(req.collection), id=rid, refresh=True)
                    removed += 1
                except Exception:  # noqa: BLE001 - missing doc is not a transport error
                    pass
        except Exception as exc:  # noqa: BLE001
            raise self._wrap(exc, "delete") from exc
        return AdapterResult(code=posture.VECTOR_OK, count=removed)

    async def health(self, req: HealthRequest) -> AdapterResult:
        try:
            self._client().cluster.health()
        except Exception as exc:  # noqa: BLE001
            raise self._wrap(exc, "health") from exc
        return AdapterResult(code=posture.VECTOR_OK, status="reachable")

    @staticmethod
    def _scored(resp: Any) -> list[tuple[float, str, tuple[float, ...]]]:
        return [
            (
                float(h["_score"]),
                str(h["_source"]["rid"]),
                tuple(float(x) for x in h["_source"]["embedding"]),
            )
            for h in resp["hits"]["hits"]
        ]


class ElasticsearchTransport(_EsLikeTransport):
    """A real :class:`Vector_Transport` over Elasticsearch 8 (dense_vector kNN)."""

    def __init__(self, params: ElasticsearchConnectionParams) -> None:
        super().__init__(params.host, params.port, params.dimension)

    def _store(self) -> str:
        return "elasticsearch"

    def _client(self) -> Any:
        try:
            from elasticsearch import Elasticsearch
        except ImportError as exc:  # pragma: no cover
            raise VectorTransportError(
                "elasticsearch client required: pip install elasticsearch"
            ) from exc
        return Elasticsearch(f"http://{self._host}:{self._port}", request_timeout=5)

    async def query(self, req: QueryRequest) -> AdapterResult:
        try:
            resp = self._client().search(
                index=self._index(req.collection),
                knn={
                    "field": "embedding",
                    "query_vector": list(req.vector),
                    "k": int(req.top_k),
                    "num_candidates": max(int(req.top_k) * 4, 10),
                },
                size=int(req.top_k),
            )
            scored = self._scored(resp)
        except Exception as exc:  # noqa: BLE001
            raise self._wrap(exc, "query") from exc
        return AdapterResult(
            code=posture.VECTOR_OK, records=records_from_scored(scored, int(req.top_k))
        )

    async def upsert(self, req: UpsertRequest) -> AdapterResult:
        try:
            es = self._client()
            for r in req.records:
                es.index(
                    index=self._index(req.collection),
                    id=r.id,
                    document={"rid": r.id, "embedding": list(r.vector)},
                    refresh=True,
                )
        except Exception as exc:  # noqa: BLE001
            raise self._wrap(exc, "upsert") from exc
        return AdapterResult(code=posture.VECTOR_OK, count=len(req.records))

    async def create_collection(self, req: CreateRequest) -> AdapterResult:
        mappings = {
            "properties": {
                "rid": {"type": "keyword"},
                "embedding": {
                    "type": "dense_vector",
                    "dims": int(req.dimension),
                    "index": True,
                    "similarity": "cosine",
                },
            }
        }
        return self._create_index(req, mappings=mappings)


class OpenSearchTransport(_EsLikeTransport):
    """A real :class:`Vector_Transport` over OpenSearch 2 (knn_vector)."""

    def __init__(self, params: OpenSearchConnectionParams) -> None:
        super().__init__(params.host, params.port, params.dimension)

    def _store(self) -> str:
        return "aws_opensearch_serverless"

    def _client(self) -> Any:
        try:
            from opensearchpy import OpenSearch
        except ImportError as exc:  # pragma: no cover
            raise VectorTransportError("opensearch-py required: pip install opensearch-py") from exc
        return OpenSearch(
            hosts=[{"host": self._host, "port": self._port}], use_ssl=False, timeout=5
        )

    async def query(self, req: QueryRequest) -> AdapterResult:
        try:
            resp = self._client().search(
                index=self._index(req.collection),
                body={
                    "size": int(req.top_k),
                    "query": {
                        "knn": {"embedding": {"vector": list(req.vector), "k": int(req.top_k)}}
                    },
                },
            )
            scored = self._scored(resp)
        except Exception as exc:  # noqa: BLE001
            raise self._wrap(exc, "query") from exc
        return AdapterResult(
            code=posture.VECTOR_OK, records=records_from_scored(scored, int(req.top_k))
        )

    async def upsert(self, req: UpsertRequest) -> AdapterResult:
        try:
            os_ = self._client()
            for r in req.records:
                os_.index(
                    index=self._index(req.collection),
                    id=r.id,
                    body={"rid": r.id, "embedding": list(r.vector)},
                    refresh=True,
                )
        except Exception as exc:  # noqa: BLE001
            raise self._wrap(exc, "upsert") from exc
        return AdapterResult(code=posture.VECTOR_OK, count=len(req.records))

    async def create_collection(self, req: CreateRequest) -> AdapterResult:
        body = {
            "settings": {"index.knn": True},
            "mappings": {
                "properties": {
                    "rid": {"type": "keyword"},
                    "embedding": {"type": "knn_vector", "dimension": int(req.dimension)},
                }
            },
        }
        return self._create_index(req, body=body)
