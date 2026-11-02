"""Redis with the search module (RediSearch) — a real :class:`Vector_Transport` (GW30).

Each collection is a RediSearch index over hashes under ``gw30:{collection}:``; the embedding is
a FLOAT32 ``VECTOR`` field with COSINE distance. ``query`` is ONE server-side ``FT.SEARCH`` KNN
call, so its cost does not grow with a client-side walk of the collection, and
``list_collections`` reads the index list (``FT._LIST``), not the keyspace.

A Redis without the search module answers ``FT.*`` with an unknown-command error, which maps to
:class:`VectorTransportError` (``provider_unreachable``). There is no brute-force fallback.
"""

from __future__ import annotations

import struct
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

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

if TYPE_CHECKING:  # pragma: no cover - typing only
    import redis.asyncio as redis_async

__all__ = ("RedisConnectionParams", "RedisVectorTransport")

_INDEX_PREFIX = "gw30idx:"
_KEY_PREFIX = "gw30:"
_EMBEDDING = b"embedding"
_RID = b"rid"
_DIST = b"dist"


@dataclass(frozen=True, slots=True)
class RedisConnectionParams:
    """Connection parameters for :class:`RedisVectorTransport` (no credential at rest here)."""

    host: str
    port: int
    db: int = 0


def _pack(vector: Sequence[float]) -> bytes:
    return struct.pack(f"<{len(vector)}f", *vector)


def _unpack(blob: bytes) -> tuple[float, ...]:
    return struct.unpack(f"<{len(blob) // 4}f", blob)


def _text(value: object) -> str:
    return value.decode() if isinstance(value, bytes) else str(value)


async def _command(client: redis_async.Redis, *args: object) -> Any:
    """``execute_command`` through one typed boundary (redis-py leaves it untyped)."""
    execute: Any = client.execute_command
    return await execute(*args)


def _pairs(reply: Any) -> dict[str, Any]:
    """A RESP3 map or a RESP2 flat ``[name, value, ...]`` list, keyed by text name."""
    if isinstance(reply, Mapping):
        return {_text(key): value for key, value in reply.items()}
    return {_text(reply[i]): reply[i + 1] for i in range(0, len(reply) - 1, 2)}


def _hits(reply: Any) -> list[dict[str, Any]]:
    """The returned fields of each ``FT.SEARCH`` hit, for a RESP3 map or a RESP2 list."""
    if isinstance(reply, Mapping):
        results = _pairs(reply).get("results", ())
        return [_pairs(_pairs(hit).get("extra_attributes", {})) for hit in results]
    return [_pairs(reply[position + 1]) for position in range(1, len(reply) - 1, 2)]


class RedisVectorTransport:
    """A real :class:`Vector_Transport` over Redis + RediSearch (``redis.asyncio``)."""

    __slots__ = ("_params",)

    def __init__(self, params: RedisConnectionParams) -> None:
        self._params = params

    def _client(self) -> redis_async.Redis:
        """An async client returning raw bytes (vector blobs are binary)."""
        try:
            import redis.asyncio as redis_async
        except ImportError as exc:  # pragma: no cover - optional client
            raise VectorTransportError(
                "redis is required for the redis live transport: pip install redis"
            ) from exc
        p = self._params
        return redis_async.Redis(
            host=p.host,
            port=p.port,
            db=p.db,
            socket_connect_timeout=5,
            socket_timeout=5,
            decode_responses=False,
        )

    @staticmethod
    def _index(collection: str) -> str:
        return f"{_INDEX_PREFIX}{safe_name(collection)}"

    @staticmethod
    def _prefix(collection: str) -> str:
        return f"{_KEY_PREFIX}{safe_name(collection)}:"

    @staticmethod
    def _wrap(exc: Exception, op: str) -> VectorTransportError:
        return VectorTransportError(f"redis {op} transport error: {type(exc).__name__}: {exc}")

    async def _dimension(self, client: redis_async.Redis, collection: str) -> int:
        """The collection's vector dimension from ``FT.INFO``; a missing index raises."""
        info = _pairs(await _command(client, "FT.INFO", self._index(collection)))
        for attribute in info.get("attributes", ()):
            fields = _pairs(attribute)
            if _text(fields.get("identifier", "")) == _EMBEDDING.decode():
                return int(_text(fields["dim"]))
        raise VectorTransportError(f"redis index for {collection!r} has no embedding field")

    async def query(self, req: QueryRequest) -> AdapterResult:
        """One server-side KNN search; a missing index is a declared failure, never empty."""
        client = self._client()
        top_k = int(req.top_k)
        try:
            reply = await _command(
                client,
                "FT.SEARCH",
                self._index(req.collection),
                "*=>[KNN $k @embedding $vec AS dist]",
                "PARAMS", "4", "k", str(top_k), "vec", _pack(req.vector),
                "SORTBY", "dist", "ASC",
                "LIMIT", "0", str(top_k),
                "RETURN", "3", "rid", "embedding", "dist",
                "DIALECT", "2",
            )
        except Exception as exc:  # noqa: BLE001 - mapped to a declared code
            raise self._wrap(exc, "query") from exc
        finally:
            await client.aclose()
        scored: list[tuple[float, str, tuple[float, ...]]] = []
        for fields in _hits(reply):
            similarity = 1.0 - float(_text(fields[_DIST.decode()]))
            vector = _unpack(fields[_EMBEDDING.decode()])
            scored.append((similarity, _text(fields[_RID.decode()]), vector))
        return AdapterResult(code=posture.VECTOR_OK, records=records_from_scored(scored, top_k))

    async def upsert(self, req: UpsertRequest) -> AdapterResult:
        """Write the whole batch in one MULTI after checking every vector's dimension."""
        client = self._client()
        try:
            dimension = await self._dimension(client, req.collection)
            if any(len(record.vector) != dimension for record in req.records):
                raise VectorTransportError(
                    f"redis upsert refused: a vector does not have dimension {dimension}"
                )
            prefix = self._prefix(req.collection)
            async with client.pipeline(transaction=True) as pipe:
                for record in req.records:
                    pipe.hset(
                        prefix + record.id,
                        mapping={_RID: record.id.encode(), _EMBEDDING: _pack(record.vector)},
                    )
                await pipe.execute()
        except VectorTransportError:
            raise
        except Exception as exc:  # noqa: BLE001 - mapped to a declared code
            raise self._wrap(exc, "upsert") from exc
        finally:
            await client.aclose()
        return AdapterResult(code=posture.VECTOR_OK, count=len(req.records))

    async def delete(self, req: DeleteRequest) -> AdapterResult:
        """Delete the identified record hashes; the count is what Redis actually removed."""
        client = self._client()
        try:
            await self._dimension(client, req.collection)
            prefix = self._prefix(req.collection)
            removed = int(await client.delete(*(prefix + record_id for record_id in req.ids)))
        except VectorTransportError:
            raise
        except Exception as exc:  # noqa: BLE001 - mapped to a declared code
            raise self._wrap(exc, "delete") from exc
        finally:
            await client.aclose()
        return AdapterResult(code=posture.VECTOR_OK, count=removed)

    async def list_collections(self, req: ListRequest) -> AdapterResult:
        """List collections from the index list (``FT._LIST``), never the keyspace."""
        client = self._client()
        try:
            indexes = await _command(client, "FT._LIST")
        except Exception as exc:  # noqa: BLE001 - mapped to a declared code
            raise self._wrap(exc, "list") from exc
        finally:
            await client.aclose()
        names = sorted(
            name[len(_INDEX_PREFIX) :]
            for name in (_text(index) for index in indexes)
            if name.startswith(_INDEX_PREFIX)
        )
        return AdapterResult(code=posture.VECTOR_OK, collections=tuple(names))

    async def create_collection(self, req: CreateRequest) -> AdapterResult:
        """``FT.CREATE`` the collection index; an existing index is ``collection_exists``."""
        client = self._client()
        from redis.exceptions import ResponseError

        try:
            await _command(
                client,
                "FT.CREATE", self._index(req.collection),
                "ON", "HASH", "PREFIX", "1", self._prefix(req.collection),
                "SCHEMA", "rid", "TAG",
                "embedding", "VECTOR", "FLAT", "6",
                "TYPE", "FLOAT32", "DIM", str(int(req.dimension)), "DISTANCE_METRIC", "COSINE",
            )
        except ResponseError as exc:
            if "already exists" in str(exc).lower():
                return collection_exists()
            raise self._wrap(exc, "create") from exc
        except Exception as exc:  # noqa: BLE001 - mapped to a declared code
            raise self._wrap(exc, "create") from exc
        finally:
            await client.aclose()
        return AdapterResult(code=posture.VECTOR_OK, status="created")

    async def health(self, req: HealthRequest) -> AdapterResult:
        """Probe reachability with a real ``PING``."""
        client = self._client()
        try:
            await client.ping()
        except Exception as exc:  # noqa: BLE001 - mapped to a declared code
            raise self._wrap(exc, "health") from exc
        finally:
            await client.aclose()
        return AdapterResult(code=posture.VECTOR_OK, status="reachable")
