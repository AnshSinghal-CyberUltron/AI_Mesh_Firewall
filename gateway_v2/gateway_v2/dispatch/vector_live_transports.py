"""GW30 real per-store ``Vector_Transport`` implementations — the deferred live gate (task 16.1).

This module drops the REAL per-store wire clients in BEHIND the shipped
``@runtime_checkable``
:class:`~gateway_v2.dispatch.vector_connectors.Vector_Transport` protocol WITHOUT
changing that protocol, the :class:`~gateway_v2.dispatch.vector_adapter.AdapterResult`
value types, the registry, the credential seam, or the bounded-operation path. A
deployment (or the L30 live-gate harness) injects one of these transports
explicitly through the same inject-never-import-upward seam
``Stub_Vector_Connector`` uses offline, exactly as ``HttpProviderClient`` drops in
behind the ``ProviderClient`` protocol in GW17-18.

Two transports are implemented here — one per LIVE backing store available in the
local Docker stack:

* :class:`PgVectorTransport` — talks to a real ``pgvector/pgvector:pg16`` Postgres
  over ``psycopg`` v3, using the installed ``vector`` extension for the embedding
  column and native ``<=>`` cosine-distance ordering on ``query``.
* :class:`RedisVectorTransport` — talks to a real ``redis:7.4-alpine`` over
  ``redis.asyncio``. That image ships NO RediSearch / vector module, so vectors are
  stored as plain per-record hashes under a collection key prefix and ``query``
  computes cosine similarity in-client over the collection — an honest brute-force
  model for a module-free Redis (reported capability flags are the connector's
  design-table flags, independent of this storage model).

The other eleven named stores (``pinecone``, ``qdrant``, ``weaviate``, ``milvus``,
``elasticsearch``, ``mongodb_atlas``, ``aws_opensearch_serverless``,
``aws_bedrock_kb``, ``gcp_vertex_vector``, ``azure_ai_search``, ``chroma``) have NO
running backend / no credentials in the local environment; their real transports
remain the DEFERRED live gate the design records and are recorded as
``infra-blocked`` in the L30-1 evidence rather than fabricated.

**Transport-failure contract.** Every transport maps a connection refusal /
connect-or-read failure / transport error / upstream error to a raised
:class:`~gateway_v2.dispatch.vector_bounded_op.VectorTransportError` (or lets a
stdlib ``OSError`` propagate), so the shipped
:func:`~gateway_v2.dispatch.vector_bounded_op.run_bounded_operation` maps it to the
declared ``PROVIDER_UNREACHABLE`` code — never an empty success, never an unhandled
exception to the caller (R6.2, R6.3). A successful operation returns the uniform
:class:`AdapterResult` with the absent-vs-empty discipline intact.

**No credential material** crosses into a result, so the pre-emission leak guard and
the credential-scope guard (both already shipped) are unaffected; these transports
receive their connection parameters at construction and never serialize them.

**Lazy / optional SDK imports.** ``psycopg`` and ``redis`` are imported LAZILY inside
the transport methods (never at module load), with a ``pip install`` hint on
``ImportError`` — exactly the pattern ``dispatch/http_provider.py`` uses for
``httpx`` and ``edge/serve.py`` uses for ``uvicorn``. So
``import gateway_v2.dispatch.vector_live_transports`` on a tree without those
clients still succeeds; only constructing-and-driving a live transport needs them.

**Layering (import-linter ``layers`` contract).** ``dispatch`` sits above
``domain``; this module imports only the standard library, its sibling
``dispatch.vector_adapter`` / ``dispatch.vector_connectors`` / ``dispatch.vector_bounded_op``
value types, and ``gateway_v2.domain.posture`` — the same single downward ``domain``
edge the rest of ``dispatch`` takes. It adds no new layer edge, holds no capacity
literal (all bounds live only in ``runtime/resources.py`` and reach the operation
through ``run_bounded_operation``), and holds no module-level mutable table.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

from gateway_v2.dispatch.vector_adapter import (
    AdapterResult,
    CreateRequest,
    DeleteRequest,
    HealthRequest,
    ListRequest,
    QueryRequest,
    UpsertRequest,
    VectorRecord,
)
from gateway_v2.dispatch.vector_bounded_op import VectorTransportError
from gateway_v2.domain import posture

if TYPE_CHECKING:  # pragma: no cover - typing only, no hard runtime dependency
    import psycopg
    import redis.asyncio as redis_async

__all__ = (
    "ChromaConnectionParams",
    "ChromaTransport",
    "ElasticsearchConnectionParams",
    "ElasticsearchTransport",
    "HttpStoreConnectionParams",
    "MongoAtlasConnectionParams",
    "MongoAtlasTransport",
    "OpenSearchConnectionParams",
    "OpenSearchTransport",
    "PgVectorConnectionParams",
    "PgVectorTransport",
    "QdrantConnectionParams",
    "QdrantTransport",
    "RedisConnectionParams",
    "RedisVectorTransport",
    "WeaviateConnectionParams",
    "WeaviateTransport",
)


def _cosine_similarity(a: tuple[float, ...], b: tuple[float, ...]) -> float:
    """Pure cosine similarity between two equal-length vectors (0.0 on a zero norm)."""
    if len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


def _vector_literal(vector: tuple[float, ...]) -> str:
    """Render a vector as a pgvector text literal, e.g. ``[0.1,0.2,0.3]``."""
    return "[" + ",".join(repr(float(x)) for x in vector) + "]"


# --------------------------------------------------------------------------- #
# pgvector — real Postgres + the `vector` extension over psycopg v3.
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class PgVectorConnectionParams:
    """Connection parameters for :class:`PgVectorTransport` (no credential at rest here).

    These are the libpq connection fields the transport opens a short-lived
    connection with per operation; they are supplied at construction by the
    injecting edge/harness and are never serialized into a result, log, or audit
    field.
    """

    host: str
    port: int
    user: str
    password: str
    dbname: str
    dimension: int


class PgVectorTransport:
    """A real :class:`Vector_Transport` over a ``pgvector`` Postgres (psycopg v3).

    Each operation opens a short-lived autocommit connection, runs the operation
    against a per-collection table (``gw30_{collection}``) whose embedding column is
    a native ``vector(dim)``, and maps every psycopg/OS failure to
    :class:`VectorTransportError` so ``run_bounded_operation`` yields
    ``PROVIDER_UNREACHABLE``. It opens the real socket — this is the deferred live
    gate — and carries no capacity literal (bounds arrive through the bounded path).
    """

    __slots__ = ("_params",)

    def __init__(self, params: PgVectorConnectionParams) -> None:
        self._params = params

    def _connect(self) -> psycopg.Connection[Any]:
        """Open one autocommit psycopg connection, failing closed to a transport error."""
        try:
            import psycopg
        except ImportError as exc:  # pragma: no cover - optional client
            raise VectorTransportError(
                "psycopg is required for the pgvector live transport: pip install 'psycopg[binary]'"
            ) from exc
        p = self._params
        try:
            return psycopg.connect(
                host=p.host,
                port=p.port,
                user=p.user,
                password=p.password,
                dbname=p.dbname,
                autocommit=True,
                connect_timeout=5,
            )
        except psycopg.OperationalError as exc:
            # Connection refused / unreachable host / auth / timeout — the whole
            # point of L30-2: a transport failure, never an empty clean search.
            raise VectorTransportError(f"pgvector unreachable: {type(exc).__name__}") from exc

    @staticmethod
    def _table(collection: str) -> str:
        """A safe, deterministic table identifier for a collection (alnum/underscore only)."""
        safe = "".join(ch if (ch.isalnum() or ch == "_") else "_" for ch in collection)
        return f"gw30_{safe}"

    async def query(self, req: QueryRequest) -> AdapterResult:
        """Run a real ``ORDER BY embedding <=> %s`` cosine query over the collection."""
        try:
            import psycopg
        except ImportError as exc:  # pragma: no cover - optional client
            raise VectorTransportError("psycopg required") from exc
        table = self._table(req.collection)
        try:
            with self._connect() as conn, conn.cursor() as cur:
                cur.execute(
                    f"SELECT id, embedding::text FROM {table} "  # noqa: S608 - table is sanitized
                    "ORDER BY embedding <=> %s LIMIT %s",
                    (_vector_literal(req.vector), int(req.top_k)),
                )
                rows = cur.fetchall()
        except psycopg.OperationalError as exc:
            raise VectorTransportError(f"pgvector query transport error: {exc}") from exc
        except psycopg.Error as exc:
            # A missing collection table / dimension mismatch is a declared failure,
            # not an empty clean search: surface PROVIDER_UNREACHABLE via the bounded
            # path rather than an ok empty result.
            raise VectorTransportError(f"pgvector query failed: {type(exc).__name__}") from exc
        records = tuple(
            VectorRecord(
                id=str(row[0]),
                vector=_parse_pgvector_text(row[1]),
                metadata=MappingProxyType({}),
            )
            for row in rows
        )
        return AdapterResult(code=posture.VECTOR_OK, records=records)

    async def upsert(self, req: UpsertRequest) -> AdapterResult:
        """Insert-or-update the record set into the per-collection table."""
        try:
            import psycopg
        except ImportError as exc:  # pragma: no cover - optional client
            raise VectorTransportError("psycopg required") from exc
        table = self._table(req.collection)
        try:
            with self._connect() as conn, conn.cursor() as cur:
                for record in req.records:
                    cur.execute(
                        f"INSERT INTO {table} (id, embedding) VALUES (%s, %s) "  # noqa: S608
                        "ON CONFLICT (id) DO UPDATE SET embedding = EXCLUDED.embedding",
                        (record.id, _vector_literal(record.vector)),
                    )
        except psycopg.OperationalError as exc:
            raise VectorTransportError(f"pgvector upsert transport error: {exc}") from exc
        except psycopg.Error as exc:
            raise VectorTransportError(f"pgvector upsert failed: {type(exc).__name__}") from exc
        return AdapterResult(code=posture.VECTOR_OK, count=len(req.records))

    async def delete(self, req: DeleteRequest) -> AdapterResult:
        """Delete the identified records from the per-collection table."""
        try:
            import psycopg
        except ImportError as exc:  # pragma: no cover - optional client
            raise VectorTransportError("psycopg required") from exc
        table = self._table(req.collection)
        removed = 0
        try:
            with self._connect() as conn, conn.cursor() as cur:
                for record_id in req.ids:
                    cur.execute(f"DELETE FROM {table} WHERE id = %s", (record_id,))  # noqa: S608
                    removed += cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
        except psycopg.OperationalError as exc:
            raise VectorTransportError(f"pgvector delete transport error: {exc}") from exc
        except psycopg.Error as exc:
            raise VectorTransportError(f"pgvector delete failed: {type(exc).__name__}") from exc
        return AdapterResult(code=posture.VECTOR_OK, count=removed)

    async def list_collections(self, req: ListRequest) -> AdapterResult:
        """List the GW30 collection tables present in the database."""
        try:
            import psycopg
        except ImportError as exc:  # pragma: no cover - optional client
            raise VectorTransportError("psycopg required") from exc
        try:
            with self._connect() as conn, conn.cursor() as cur:
                cur.execute(
                    "SELECT tablename FROM pg_tables "
                    "WHERE tablename LIKE 'gw30_%' ORDER BY tablename"
                )
                rows = cur.fetchall()
        except psycopg.OperationalError as exc:
            raise VectorTransportError(f"pgvector list transport error: {exc}") from exc
        except psycopg.Error as exc:
            raise VectorTransportError(f"pgvector list failed: {type(exc).__name__}") from exc
        names = tuple(str(row[0])[len("gw30_") :] for row in rows)
        return AdapterResult(code=posture.VECTOR_OK, collections=names)

    async def create_collection(self, req: CreateRequest) -> AdapterResult:
        """Create the per-collection table with a native ``vector(dim)`` column."""
        try:
            import psycopg
        except ImportError as exc:  # pragma: no cover - optional client
            raise VectorTransportError("psycopg required") from exc
        table = self._table(req.collection)
        try:
            with self._connect() as conn, conn.cursor() as cur:
                cur.execute(
                    f"CREATE TABLE IF NOT EXISTS {table} "  # noqa: S608 - table is sanitized
                    f"(id TEXT PRIMARY KEY, embedding vector({int(req.dimension)}))"
                )
        except psycopg.OperationalError as exc:
            raise VectorTransportError(f"pgvector create transport error: {exc}") from exc
        except psycopg.Error as exc:
            raise VectorTransportError(f"pgvector create failed: {type(exc).__name__}") from exc
        return AdapterResult(code=posture.VECTOR_OK, status="created")

    async def health(self, req: HealthRequest) -> AdapterResult:
        """Probe reachability with a trivial ``SELECT 1``."""
        try:
            import psycopg
        except ImportError as exc:  # pragma: no cover - optional client
            raise VectorTransportError("psycopg required") from exc
        try:
            with self._connect() as conn, conn.cursor() as cur:
                cur.execute("SELECT 1")
                cur.fetchone()
        except psycopg.OperationalError as exc:
            raise VectorTransportError(f"pgvector health transport error: {exc}") from exc
        except psycopg.Error as exc:
            raise VectorTransportError(f"pgvector health failed: {type(exc).__name__}") from exc
        return AdapterResult(code=posture.VECTOR_OK, status="reachable")


def _parse_pgvector_text(text: str) -> tuple[float, ...]:
    """Parse a pgvector text literal ``[a,b,c]`` back into a float tuple."""
    stripped = text.strip().strip("[]")
    if not stripped:
        return ()
    return tuple(float(part) for part in stripped.split(","))


# --------------------------------------------------------------------------- #
# Redis — real redis:7.4-alpine over redis.asyncio (no RediSearch module).
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class RedisConnectionParams:
    """Connection parameters for :class:`RedisVectorTransport` (no credential at rest here)."""

    host: str
    port: int
    db: int = 0


class RedisVectorTransport:
    """A real :class:`Vector_Transport` over a module-free Redis (``redis.asyncio``).

    The ``redis:7.4-alpine`` image ships no RediSearch / vector module, so vectors
    are stored as per-record hashes under ``gw30:{collection}:{id}`` and ``query``
    reads the collection's members and computes cosine similarity in-client. This is
    an honest brute-force model for a module-free Redis; it opens the real socket
    (the deferred live gate) and maps every ``redis``/OS failure to
    :class:`VectorTransportError` so ``run_bounded_operation`` yields
    ``PROVIDER_UNREACHABLE``.
    """

    __slots__ = ("_params",)

    def __init__(self, params: RedisConnectionParams) -> None:
        self._params = params

    def _client(self) -> redis_async.Redis:
        """Build an async Redis client, failing closed to a transport error on import."""
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
            decode_responses=True,
        )

    @staticmethod
    def _member_key(collection: str, record_id: str) -> str:
        return f"gw30:{collection}:{record_id}"

    @staticmethod
    def _index_key(collection: str) -> str:
        return f"gw30:index:{collection}"

    @staticmethod
    def _wrap(exc: Exception, op: str) -> VectorTransportError:
        """Map any Redis/OS/transport failure to the declared transport error.

        The ``redis`` client's error hierarchy (``redis.exceptions.RedisError``,
        itself a subclass of ``Exception``) and ``OSError`` both reach here; both
        are a backing-store transport failure that must surface as
        ``PROVIDER_UNREACHABLE`` through the bounded path, never an empty success.
        """
        return VectorTransportError(f"redis {op} transport error: {type(exc).__name__}: {exc}")

    async def query(self, req: QueryRequest) -> AdapterResult:
        """Brute-force cosine query over the collection's members (module-free Redis)."""
        client = self._client()
        try:
            ids = await client.smembers(self._index_key(req.collection))
            scored: list[tuple[float, VectorRecord]] = []
            for raw_id in ids:
                record_id = raw_id.decode() if isinstance(raw_id, bytes) else str(raw_id)
                raw = await client.hget(self._member_key(req.collection, record_id), "embedding")
                if raw is None:
                    continue
                raw_text = raw.decode() if isinstance(raw, bytes) else str(raw)
                vector = _parse_pgvector_text(raw_text)
                score = _cosine_similarity(req.vector, vector)
                rec = VectorRecord(id=record_id, vector=vector, metadata=MappingProxyType({}))
                scored.append((score, rec))
        except Exception as exc:  # noqa: BLE001 - mapped to a declared code
            raise self._wrap(exc, "query") from exc
        finally:
            await client.aclose()
        scored.sort(key=lambda pair: pair[0], reverse=True)
        records = tuple(record for _, record in scored[: int(req.top_k)])
        return AdapterResult(code=posture.VECTOR_OK, records=records)

    async def upsert(self, req: UpsertRequest) -> AdapterResult:
        """Store each record as a hash and register it in the collection index set."""
        client = self._client()
        try:
            for record in req.records:
                await client.hset(
                    self._member_key(req.collection, record.id),
                    mapping={"embedding": _vector_literal(record.vector)},
                )
                await client.sadd(self._index_key(req.collection), record.id)
        except Exception as exc:  # noqa: BLE001 - mapped to a declared code
            raise self._wrap(exc, "upsert") from exc
        finally:
            await client.aclose()
        return AdapterResult(code=posture.VECTOR_OK, count=len(req.records))

    async def delete(self, req: DeleteRequest) -> AdapterResult:
        """Delete the identified record hashes and de-register them from the index."""
        client = self._client()
        removed = 0
        try:
            for record_id in req.ids:
                deleted = await client.delete(self._member_key(req.collection, record_id))
                await client.srem(self._index_key(req.collection), record_id)
                removed += int(deleted)
        except Exception as exc:  # noqa: BLE001 - mapped to a declared code
            raise self._wrap(exc, "delete") from exc
        finally:
            await client.aclose()
        return AdapterResult(code=posture.VECTOR_OK, count=removed)

    async def list_collections(self, req: ListRequest) -> AdapterResult:
        """List collections via the ``gw30:index:*`` registry keys."""
        client = self._client()
        names: list[str] = []
        try:
            async for key in client.scan_iter(match="gw30:index:*"):
                key_text = key.decode() if isinstance(key, bytes) else str(key)
                names.append(key_text[len("gw30:index:") :])
        except Exception as exc:  # noqa: BLE001 - mapped to a declared code
            raise self._wrap(exc, "list") from exc
        finally:
            await client.aclose()
        return AdapterResult(code=posture.VECTOR_OK, collections=tuple(sorted(names)))

    async def create_collection(self, req: CreateRequest) -> AdapterResult:
        """Register the collection index key (a module-free Redis has no schema to create)."""
        client = self._client()
        try:
            # Touch the index set so list_collections sees it; SADD of a sentinel then
            # removed keeps the key present without a phantom member.
            await client.sadd(self._index_key(req.collection), "__created__")
            await client.srem(self._index_key(req.collection), "__created__")
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


def connection_params_repr(params: Mapping[str, object]) -> str:
    """Render connection params with any secret field redacted (defensive helper)."""
    safe = {
        k: ("<redacted>" if "pass" in k.lower() or "secret" in k.lower() else v)
        for k, v in params.items()
    }
    return repr(safe)


# =========================================================================== #
# GW30 follow-up (9-store local live gate): six more real Vector_Transport
# clients for the local Docker store stack (docker-compose.gw30-stores.yml).
#
# Each mirrors the PgVector/Redis idiom exactly: a frozen slotted
# ``*ConnectionParams``; lazy SDK import with a pip hint on ``ImportError``; every
# connection refusal / connect-or-read failure / transport error / upstream error
# mapped to :class:`VectorTransportError` so ``run_bounded_operation`` yields the
# declared ``PROVIDER_UNREACHABLE`` (never an empty success, never an unhandled
# exception); the uniform :class:`AdapterResult` with the absent-vs-empty
# discipline (a genuine zero-match query is ``VECTOR_OK`` + ``records=()``; a
# transport failure is ``PROVIDER_UNREACHABLE`` + ``records=None``); no credential
# in any result or log; no capacity literal; no module-level mutable table.
#
# The SDKs are synchronous; like the shipped PgVectorTransport they are called
# inside the async op (the live harness drives one op at a time), keeping the
# transport a thin, honest wire client rather than a re-architected async pool.
# =========================================================================== #


def _records_from_scored(
    scored: list[tuple[float, str, tuple[float, ...]]],
    top_k: int,
) -> tuple[VectorRecord, ...]:
    """Build the uniform record tuple from (score, id, vector), highest score first.

    A single deterministic ordering (descending cosine, then id) so every
    connector yields the SAME result-id order for the SAME input -- the L30-1
    "identical result sets" contract across heterogeneous engines.
    """
    scored.sort(key=lambda t: (-t[0], t[1]))
    return tuple(
        VectorRecord(id=rid, vector=vec, metadata=MappingProxyType({}))
        for _, rid, vec in scored[:top_k]
    )


@dataclass(frozen=True, slots=True)
class HttpStoreConnectionParams:
    """Generic host/port connection params for an HTTP-reachable local store."""

    host: str
    port: int
    dimension: int


# --------------------------------------------------------------------------- #
# Qdrant -- qdrant-client against qdrant/qdrant.
# --------------------------------------------------------------------------- #


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
            code=posture.VECTOR_OK, records=_records_from_scored(scored, int(req.top_k))
        )

    async def upsert(self, req: UpsertRequest) -> AdapterResult:
        try:
            from qdrant_client import models

            client = self._client()
            points = [
                models.PointStruct(
                    id=_as_point_id(r.id), vector=list(r.vector), payload={"rid": r.id}
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
                points_selector=models.PointIdsList(points=[_as_point_id(i) for i in req.ids]),
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
        try:
            from qdrant_client import models

            client = self._client()
            client.recreate_collection(
                collection_name=req.collection,
                vectors_config=models.VectorParams(
                    size=int(req.dimension), distance=models.Distance.COSINE
                ),
            )
        except Exception as exc:  # noqa: BLE001
            raise self._wrap(exc, "create") from exc
        return AdapterResult(code=posture.VECTOR_OK, status="created")

    async def health(self, req: HealthRequest) -> AdapterResult:
        try:
            self._client().get_collections()
        except Exception as exc:  # noqa: BLE001
            raise self._wrap(exc, "health") from exc
        return AdapterResult(code=posture.VECTOR_OK, status="reachable")


def _as_point_id(record_id: str) -> Any:
    """Qdrant point ids must be int or UUID; map a string id deterministically to a UUID."""
    import uuid

    return str(uuid.uuid5(uuid.NAMESPACE_URL, record_id))


# --------------------------------------------------------------------------- #
# Weaviate -- weaviate-client v4 against semitechnologies/weaviate.
# --------------------------------------------------------------------------- #


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
            code=posture.VECTOR_OK, records=_records_from_scored(scored, int(req.top_k))
        )

    async def upsert(self, req: UpsertRequest) -> AdapterResult:
        client = self._client()
        try:
            coll = client.collections.get(self._cls(req.collection))
            with coll.batch.dynamic() as batch:
                for r in req.records:
                    batch.add_object(
                        properties={"rid": r.id}, vector=list(r.vector), uuid=_as_point_id(r.id)
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
                coll.data.delete_by_id(_as_point_id(rid))
                removed += 1
        except Exception as exc:  # noqa: BLE001
            raise self._wrap(exc, "delete") from exc
        finally:
            client.close()
        return AdapterResult(code=posture.VECTOR_OK, count=removed)

    async def list_collections(self, req: ListRequest) -> AdapterResult:
        client = self._client()
        try:
            names = tuple(sorted(client.collections.list_all().keys()))
        except Exception as exc:  # noqa: BLE001
            raise self._wrap(exc, "list") from exc
        finally:
            client.close()
        return AdapterResult(code=posture.VECTOR_OK, collections=names)

    async def create_collection(self, req: CreateRequest) -> AdapterResult:
        client = self._client()
        try:
            from weaviate.classes.config import Configure, DataType, Property

            name = self._cls(req.collection)
            if client.collections.exists(name):
                client.collections.delete(name)
            client.collections.create(
                name=name,
                properties=[Property(name="rid", data_type=DataType.TEXT)],
                vectorizer_config=Configure.Vectorizer.none(),
            )
        except Exception as exc:  # noqa: BLE001
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


# --------------------------------------------------------------------------- #
# Elasticsearch / OpenSearch -- dense_vector / knn_vector kNN.
# --------------------------------------------------------------------------- #


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

    # subclasses provide _client() and the index mapping + knn query bodies
    def _client(self) -> Any:  # pragma: no cover - overridden
        raise NotImplementedError

    def _store(self) -> str:  # pragma: no cover - overridden
        raise NotImplementedError

    @staticmethod
    def _index(collection: str) -> str:
        return (
            "gw30_" + "".join(c if (c.isalnum() or c == "_") else "_" for c in collection).lower()
        )

    def _wrap(self, exc: Exception, op: str) -> VectorTransportError:
        return VectorTransportError(
            f"{self._store()} {op} transport error: {type(exc).__name__}: {exc}"
        )


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
            es = self._client()
            resp = es.search(
                index=self._index(req.collection),
                knn={
                    "field": "embedding",
                    "query_vector": list(req.vector),
                    "k": int(req.top_k),
                    "num_candidates": max(int(req.top_k) * 4, 10),
                },
                size=int(req.top_k),
            )
            scored = [
                (
                    float(h["_score"]),
                    str(h["_source"]["rid"]),
                    tuple(float(x) for x in h["_source"]["embedding"]),
                )
                for h in resp["hits"]["hits"]
            ]
        except Exception as exc:  # noqa: BLE001
            raise self._wrap(exc, "query") from exc
        return AdapterResult(
            code=posture.VECTOR_OK, records=_records_from_scored(scored, int(req.top_k))
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

    async def delete(self, req: DeleteRequest) -> AdapterResult:
        removed = 0
        try:
            es = self._client()
            for rid in req.ids:
                try:
                    es.delete(index=self._index(req.collection), id=rid, refresh=True)
                    removed += 1
                except Exception:  # noqa: BLE001 - missing doc is not a transport error
                    pass
        except Exception as exc:  # noqa: BLE001
            raise self._wrap(exc, "delete") from exc
        return AdapterResult(code=posture.VECTOR_OK, count=removed)

    async def list_collections(self, req: ListRequest) -> AdapterResult:
        try:
            es = self._client()
            idx = es.indices.get(index="gw30_*", ignore_unavailable=True)
            names = tuple(sorted(k[len("gw30_") :] for k in idx))
        except Exception as exc:  # noqa: BLE001
            raise self._wrap(exc, "list") from exc
        return AdapterResult(code=posture.VECTOR_OK, collections=names)

    async def create_collection(self, req: CreateRequest) -> AdapterResult:
        try:
            es = self._client()
            name = self._index(req.collection)
            if es.indices.exists(index=name):
                es.indices.delete(index=name)
            es.indices.create(
                index=name,
                mappings={
                    "properties": {
                        "rid": {"type": "keyword"},
                        "embedding": {
                            "type": "dense_vector",
                            "dims": int(req.dimension),
                            "index": True,
                            "similarity": "cosine",
                        },
                    }
                },
            )
        except Exception as exc:  # noqa: BLE001
            raise self._wrap(exc, "create") from exc
        return AdapterResult(code=posture.VECTOR_OK, status="created")

    async def health(self, req: HealthRequest) -> AdapterResult:
        try:
            self._client().cluster.health()
        except Exception as exc:  # noqa: BLE001
            raise self._wrap(exc, "health") from exc
        return AdapterResult(code=posture.VECTOR_OK, status="reachable")


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
            os_ = self._client()
            resp = os_.search(
                index=self._index(req.collection),
                body={
                    "size": int(req.top_k),
                    "query": {
                        "knn": {"embedding": {"vector": list(req.vector), "k": int(req.top_k)}}
                    },
                },
            )
            scored = [
                (
                    float(h["_score"]),
                    str(h["_source"]["rid"]),
                    tuple(float(x) for x in h["_source"]["embedding"]),
                )
                for h in resp["hits"]["hits"]
            ]
        except Exception as exc:  # noqa: BLE001
            raise self._wrap(exc, "query") from exc
        return AdapterResult(
            code=posture.VECTOR_OK, records=_records_from_scored(scored, int(req.top_k))
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

    async def delete(self, req: DeleteRequest) -> AdapterResult:
        removed = 0
        try:
            os_ = self._client()
            for rid in req.ids:
                try:
                    os_.delete(index=self._index(req.collection), id=rid, refresh=True)
                    removed += 1
                except Exception:  # noqa: BLE001
                    pass
        except Exception as exc:  # noqa: BLE001
            raise self._wrap(exc, "delete") from exc
        return AdapterResult(code=posture.VECTOR_OK, count=removed)

    async def list_collections(self, req: ListRequest) -> AdapterResult:
        try:
            os_ = self._client()
            idx = os_.indices.get(index="gw30_*", ignore_unavailable=True)
            names = tuple(sorted(k[len("gw30_") :] for k in idx))
        except Exception as exc:  # noqa: BLE001
            raise self._wrap(exc, "list") from exc
        return AdapterResult(code=posture.VECTOR_OK, collections=names)

    async def create_collection(self, req: CreateRequest) -> AdapterResult:
        try:
            os_ = self._client()
            name = self._index(req.collection)
            if os_.indices.exists(index=name):
                os_.indices.delete(index=name)
            os_.indices.create(
                index=name,
                body={
                    "settings": {"index.knn": True},
                    "mappings": {
                        "properties": {
                            "rid": {"type": "keyword"},
                            "embedding": {"type": "knn_vector", "dimension": int(req.dimension)},
                        }
                    },
                },
            )
        except Exception as exc:  # noqa: BLE001
            raise self._wrap(exc, "create") from exc
        return AdapterResult(code=posture.VECTOR_OK, status="created")

    async def health(self, req: HealthRequest) -> AdapterResult:
        try:
            self._client().cluster.health()
        except Exception as exc:  # noqa: BLE001
            raise self._wrap(exc, "health") from exc
        return AdapterResult(code=posture.VECTOR_OK, status="reachable")


# --------------------------------------------------------------------------- #
# Chroma -- chromadb HttpClient.
# --------------------------------------------------------------------------- #


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
        return "gw30_" + "".join(c if (c.isalnum() or c == "_") else "_" for c in collection)

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
            code=posture.VECTOR_OK, records=_records_from_scored(scored, int(req.top_k))
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
        try:
            client = self._client()
            name = self._name(req.collection)
            try:
                client.delete_collection(name)
            except Exception:  # noqa: BLE001 - absent is fine
                pass
            client.create_collection(name=name, metadata={"hnsw:space": "cosine"})
        except Exception as exc:  # noqa: BLE001
            raise self._wrap(exc, "create") from exc
        return AdapterResult(code=posture.VECTOR_OK, status="created")

    async def health(self, req: HealthRequest) -> AdapterResult:
        try:
            self._client().heartbeat()
        except Exception as exc:  # noqa: BLE001
            raise self._wrap(exc, "health") from exc
        return AdapterResult(code=posture.VECTOR_OK, status="reachable")


# --------------------------------------------------------------------------- #
# MongoDB Atlas Local -- pymongo; brute-force cosine (Atlas $vectorSearch needs an
# index build, so for the live gate we score in-client, an honest module-free model).
# --------------------------------------------------------------------------- #


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
        return "gw30_" + "".join(c if (c.isalnum() or c == "_") else "_" for c in collection)

    @staticmethod
    def _wrap(exc: Exception, op: str) -> VectorTransportError:
        return VectorTransportError(
            f"mongodb_atlas {op} transport error: {type(exc).__name__}: {exc}"
        )

    async def query(self, req: QueryRequest) -> AdapterResult:
        try:
            from pymongo.errors import PyMongoError

            coll = self._db()[self._coll(req.collection)]
            scored = []
            for doc in coll.find({}):
                vec = tuple(float(x) for x in doc.get("embedding", ()))
                scored.append((_cosine_similarity(req.vector, vec), str(doc["_id"]), vec))
        except PyMongoError as exc:
            raise self._wrap(exc, "query") from exc
        except Exception as exc:  # noqa: BLE001
            raise self._wrap(exc, "query") from exc
        return AdapterResult(
            code=posture.VECTOR_OK, records=_records_from_scored(scored, int(req.top_k))
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
            db = self._db()
            names = tuple(
                sorted(
                    n[len("gw30_") :] for n in db.list_collection_names() if n.startswith("gw30_")
                )
            )
        except Exception as exc:  # noqa: BLE001
            raise self._wrap(exc, "list") from exc
        return AdapterResult(code=posture.VECTOR_OK, collections=names)

    async def create_collection(self, req: CreateRequest) -> AdapterResult:
        try:
            db = self._db()
            name = self._coll(req.collection)
            if name in db.list_collection_names():
                db[name].drop()
            db.create_collection(name)
        except Exception as exc:  # noqa: BLE001
            raise self._wrap(exc, "create") from exc
        return AdapterResult(code=posture.VECTOR_OK, status="created")

    async def health(self, req: HealthRequest) -> AdapterResult:
        try:
            self._db().command("ping")
        except Exception as exc:  # noqa: BLE001
            raise self._wrap(exc, "health") from exc
        return AdapterResult(code=posture.VECTOR_OK, status="reachable")
