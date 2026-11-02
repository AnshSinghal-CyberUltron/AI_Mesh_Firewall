"""``pgvector`` Postgres over psycopg v3 — a real :class:`Vector_Transport` (GW30)."""

from __future__ import annotations

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
from gateway_v2.dispatch.vector_transports._common import (
    collection_exists,
    parse_pgvector_text,
    safe_name,
    vector_literal,
)
from gateway_v2.domain import posture

if TYPE_CHECKING:  # pragma: no cover - typing only
    import psycopg

__all__ = ("PgVectorConnectionParams", "PgVectorTransport")


@dataclass(frozen=True, slots=True)
class PgVectorConnectionParams:
    """libpq connection fields; supplied at construction and never serialized into a result."""

    host: str
    port: int
    user: str
    password: str
    dbname: str
    dimension: int


class PgVectorTransport:
    """A per-collection table ``gw30_{collection}`` with a native ``vector(dim)`` column.

    Each operation opens a short-lived autocommit connection and maps every psycopg/OS failure to
    :class:`VectorTransportError`, so ``run_bounded_operation`` yields ``PROVIDER_UNREACHABLE``.
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
            raise VectorTransportError(f"pgvector unreachable: {type(exc).__name__}") from exc

    @staticmethod
    def _table(collection: str) -> str:
        return f"gw30_{safe_name(collection)}"

    @staticmethod
    def _psycopg() -> Any:
        try:
            import psycopg
        except ImportError as exc:  # pragma: no cover - optional client
            raise VectorTransportError("psycopg required") from exc
        return psycopg

    async def query(self, req: QueryRequest) -> AdapterResult:
        """Run a real ``ORDER BY embedding <=> %s`` cosine query over the collection."""
        psycopg = self._psycopg()
        table = self._table(req.collection)
        try:
            with self._connect() as conn, conn.cursor() as cur:
                cur.execute(
                    f"SELECT id, embedding::text FROM {table} "  # noqa: S608 - table is sanitized
                    "ORDER BY embedding <=> %s LIMIT %s",
                    (vector_literal(req.vector), int(req.top_k)),
                )
                rows = cur.fetchall()
        except psycopg.OperationalError as exc:
            raise VectorTransportError(f"pgvector query transport error: {exc}") from exc
        except psycopg.Error as exc:
            # A missing table / dimension mismatch is a declared failure, never an empty search.
            raise VectorTransportError(f"pgvector query failed: {type(exc).__name__}") from exc
        records = tuple(
            VectorRecord(
                id=str(row[0]),
                vector=parse_pgvector_text(row[1]),
                metadata=MappingProxyType({}),
            )
            for row in rows
        )
        return AdapterResult(code=posture.VECTOR_OK, records=records)

    async def upsert(self, req: UpsertRequest) -> AdapterResult:
        """Insert-or-update the record set into the per-collection table."""
        psycopg = self._psycopg()
        table = self._table(req.collection)
        try:
            with self._connect() as conn, conn.cursor() as cur:
                for record in req.records:
                    cur.execute(
                        f"INSERT INTO {table} (id, embedding) VALUES (%s, %s) "  # noqa: S608
                        "ON CONFLICT (id) DO UPDATE SET embedding = EXCLUDED.embedding",
                        (record.id, vector_literal(record.vector)),
                    )
        except psycopg.OperationalError as exc:
            raise VectorTransportError(f"pgvector upsert transport error: {exc}") from exc
        except psycopg.Error as exc:
            raise VectorTransportError(f"pgvector upsert failed: {type(exc).__name__}") from exc
        return AdapterResult(code=posture.VECTOR_OK, count=len(req.records))

    async def delete(self, req: DeleteRequest) -> AdapterResult:
        """Delete the identified records from the per-collection table."""
        psycopg = self._psycopg()
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
        psycopg = self._psycopg()
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
        """Create the per-collection table; an existing table is ``collection_exists``."""
        psycopg = self._psycopg()
        table = self._table(req.collection)
        try:
            with self._connect() as conn, conn.cursor() as cur:
                cur.execute(
                    f"CREATE TABLE {table} "  # noqa: S608 - table is sanitized
                    f"(id TEXT PRIMARY KEY, embedding vector({int(req.dimension)}))"
                )
        except psycopg.errors.DuplicateTable:
            return collection_exists()
        except psycopg.OperationalError as exc:
            raise VectorTransportError(f"pgvector create transport error: {exc}") from exc
        except psycopg.Error as exc:
            raise VectorTransportError(f"pgvector create failed: {type(exc).__name__}") from exc
        return AdapterResult(code=posture.VECTOR_OK, status="created")

    async def health(self, req: HealthRequest) -> AdapterResult:
        """Probe reachability with a trivial ``SELECT 1``."""
        psycopg = self._psycopg()
        try:
            with self._connect() as conn, conn.cursor() as cur:
                cur.execute("SELECT 1")
                cur.fetchone()
        except psycopg.OperationalError as exc:
            raise VectorTransportError(f"pgvector health transport error: {exc}") from exc
        except psycopg.Error as exc:
            raise VectorTransportError(f"pgvector health failed: {type(exc).__name__}") from exc
        return AdapterResult(code=posture.VECTOR_OK, status="reachable")
