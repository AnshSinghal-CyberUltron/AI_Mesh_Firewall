"""Weaviate over ``weaviate-client`` v4 — a real :class:`Vector_Transport` (GW30)."""

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

__all__ = ("WeaviateConnectionParams", "WeaviateTransport")


@dataclass(frozen=True, slots=True)
class WeaviateConnectionParams:
    host: str
    port: int
    dimension: int
    grpc_port: int = 50051


class WeaviateTransport:
    """A real :class:`Vector_Transport` over Weaviate (``weaviate-client`` v4)."""

    __slots__ = ("_params",)

    def __init__(self, params: WeaviateConnectionParams) -> None:
        self._params = params

    def _client(self) -> Any:
        try:
            import weaviate
        except ImportError as exc:  # pragma: no cover - optional client
            raise VectorTransportError(
                "weaviate-client required: pip install weaviate-client"
            ) from exc
        try:
            return weaviate.connect_to_local(
                host=self._params.host,
                port=self._params.port,
                grpc_port=self._params.grpc_port,
            )
        except Exception as exc:  # noqa: BLE001
            raise VectorTransportError(f"weaviate connect error: {type(exc).__name__}") from exc

    @staticmethod
    def _cls(collection: str) -> str:
        return "Gw30_" + "".join(c if c.isalnum() else "_" for c in collection)

    @staticmethod
    def _wrap(exc: Exception, op: str) -> VectorTransportError:
        return VectorTransportError(f"weaviate {op} transport error: {type(exc).__name__}: {exc}")

    async def query(self, req: QueryRequest) -> AdapterResult:
        client = self._client()
        try:
            coll = client.collections.get(self._cls(req.collection))
            res = coll.query.near_vector(
                near_vector=list(req.vector),
                limit=int(req.top_k),
                include_vector=True,
                return_metadata=["distance"],
            )
            scored = []
            for o in res.objects:
                rid = str(o.properties.get("rid"))
                vec = tuple(float(x) for x in (o.vector.get("default") if o.vector else []))
                dist = (
                    float(o.metadata.distance)
                    if o.metadata and o.metadata.distance is not None
                    else 1.0
                )
                scored.append((1.0 - dist, rid, vec))
        except Exception as exc:  # noqa: BLE001
            raise self._wrap(exc, "query") from exc
        finally:
            client.close()
        return AdapterResult(
            code=posture.VECTOR_OK, records=records_from_scored(scored, int(req.top_k))
        )

    async def upsert(self, req: UpsertRequest) -> AdapterResult:
        client = self._client()
        try:
            coll = client.collections.get(self._cls(req.collection))
            with coll.batch.dynamic() as batch:
                for r in req.records:
                    batch.add_object(
                        properties={"rid": r.id}, vector=list(r.vector), uuid=as_point_id(r.id)
                    )
        except Exception as exc:  # noqa: BLE001
            raise self._wrap(exc, "upsert") from exc
        finally:
            client.close()
        return AdapterResult(code=posture.VECTOR_OK, count=len(req.records))

    async def delete(self, req: DeleteRequest) -> AdapterResult:
        client = self._client()
        removed = 0
        try:
            coll = client.collections.get(self._cls(req.collection))
            for rid in req.ids:
                coll.data.delete_by_id(as_point_id(rid))
                removed += 1
        except Exception as exc:  # noqa: BLE001
            raise self._wrap(exc, "delete") from exc
        finally:
            client.close()
        return AdapterResult(code=posture.VECTOR_OK, count=removed)

    async def list_collections(self, req: ListRequest) -> AdapterResult:
        client = self._client()
        try:
            names = tuple(sorted(client.collections.list_all()))
        except Exception as exc:  # noqa: BLE001
            raise self._wrap(exc, "list") from exc
        finally:
            client.close()
        return AdapterResult(code=posture.VECTOR_OK, collections=names)

    async def create_collection(self, req: CreateRequest) -> AdapterResult:
        """Create the class; an existing one is ``collection_exists`` and is not touched."""
        client = self._client()
        try:
            from weaviate.classes.config import Configure, DataType, Property

            client.collections.create(
                name=self._cls(req.collection),
                properties=[Property(name="rid", data_type=DataType.TEXT)],
                vectorizer_config=Configure.Vectorizer.none(),
            )
        except Exception as exc:  # noqa: BLE001
            if "already exists" in str(exc).lower():
                return collection_exists()
            raise self._wrap(exc, "create") from exc
        finally:
            client.close()
        return AdapterResult(code=posture.VECTOR_OK, status="created")

    async def health(self, req: HealthRequest) -> AdapterResult:
        client = self._client()
        try:
            ok = client.is_ready()
        except Exception as exc:  # noqa: BLE001
            raise self._wrap(exc, "health") from exc
        finally:
            client.close()
        return AdapterResult(code=posture.VECTOR_OK, status="reachable" if ok else "degraded")
