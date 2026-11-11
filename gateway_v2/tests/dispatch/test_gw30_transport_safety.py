"""GW30 live transports: create never destroys data, and Redis uses server-side KNN.

Offline, socket-free. Each transport's store client is replaced by a fake, so these tests pin the
transport's own logic: R2.9 (an existing collection is the declared ``collection_exists``, never a
reset), and the Redis transport's query/upsert shape.
"""

from __future__ import annotations

import ast
import asyncio
from pathlib import Path
from types import MappingProxyType
from typing import Any

import pytest

from gateway_v2.dispatch import vector_transports as vt
from gateway_v2.dispatch.vector_adapter import (
    CreateRequest,
    QueryRequest,
    UpsertRequest,
    VectorRecord,
)
from gateway_v2.dispatch.vector_bounded_op import VectorTransportError
from gateway_v2.dispatch.vector_transports import redis_transport
from gateway_v2.domain import posture

_PACKAGE = Path(vt.__file__).parent
_DESTRUCTIVE_CALLS = frozenset(
    {
        "delete",
        "drop",
        "drop_collection",
        "delete_collection",
        "recreate_collection",
        "delete_index",
    }
)
_CREATE = CreateRequest(org_id="o", store_id="s", collection="c", dimension=4, metric="cosine")


def _create_bodies() -> list[tuple[str, ast.AST]]:
    bodies: list[tuple[str, ast.AST]] = []
    for path in sorted(_PACKAGE.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef) and node.name in {
                "create_collection",
                "_create_index",
            }:
                bodies.append((path.name, node))
    return bodies


def test_no_create_collection_calls_a_destructive_api() -> None:
    bodies = _create_bodies()
    assert len(bodies) >= 7, bodies
    for module, body in bodies:
        for node in ast.walk(body):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                assert node.func.attr not in _DESTRUCTIVE_CALLS, (module, node.func.attr)
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                assert "DROP" not in node.value.upper(), (module, node.value)
                assert "IF NOT EXISTS" not in node.value.upper(), (module, node.value)


class _Raises:
    """A fake store client whose every attribute call raises ``exc``."""

    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    def __getattr__(self, name: str) -> Any:
        def _call(*args: object, **kwargs: object) -> Any:
            raise self._exc

        return _call


def _with(cls: type, params: object, method: str, make: Any) -> Any:
    """An instance of ``cls`` whose client factory ``method`` returns ``make()``."""
    subclass = type(f"Fake{cls.__name__}", (cls,), {method: lambda self: make()})
    return subclass(params)


def _exists(transport: Any) -> str:
    return asyncio.run(transport.create_collection(_CREATE)).code  # type: ignore[no-any-return]


def test_elasticsearch_already_exists_is_collection_exists() -> None:
    fake = _Raises(RuntimeError("BadRequestError(400, 'resource_already_exists_exception')"))
    client = type("C", (), {"indices": fake})()
    t = _with(vt.ElasticsearchTransport, vt.ElasticsearchConnectionParams("h", 1, 4), "_client",
              lambda: client)
    assert _exists(t) == posture.COLLECTION_EXISTS


def test_chroma_already_exists_is_collection_exists() -> None:
    t = _with(vt.ChromaTransport, vt.ChromaConnectionParams("h", 1, 4), "_client",
              lambda: _Raises(ValueError("Collection gw30_c already exists")))
    assert _exists(t) == posture.COLLECTION_EXISTS


def test_mongo_already_exists_is_collection_exists() -> None:
    t = _with(vt.MongoAtlasTransport, vt.MongoAtlasConnectionParams("h", 1, 4), "_db",
              lambda: _Raises(RuntimeError("collection gw30_c already exists")))
    assert _exists(t) == posture.COLLECTION_EXISTS


def test_weaviate_already_exists_is_collection_exists() -> None:
    pytest.importorskip("weaviate")

    class _Client:
        collections = _Raises(RuntimeError("class name Gw30_c already exists"))

        def close(self) -> None:
            return None

    t = _with(vt.WeaviateTransport, vt.WeaviateConnectionParams("h", 1, 4), "_client", _Client)
    assert _exists(t) == posture.COLLECTION_EXISTS


def test_qdrant_conflict_is_collection_exists() -> None:
    pytest.importorskip("qdrant_client")
    from qdrant_client.http.exceptions import UnexpectedResponse

    conflict = UnexpectedResponse(409, "Conflict", b"{}", None)  # type: ignore[arg-type]
    t = _with(vt.QdrantTransport, vt.QdrantConnectionParams("h", 1, 4), "_client",
              lambda: _Raises(conflict))
    assert _exists(t) == posture.COLLECTION_EXISTS


def test_an_unrelated_create_error_is_a_transport_error_not_exists() -> None:
    t = _with(vt.ChromaTransport, vt.ChromaConnectionParams("h", 1, 4), "_client",
              lambda: _Raises(OSError("connection refused")))
    with pytest.raises(VectorTransportError):
        _exists(t)


# --------------------------------------------------------------------------- #
# Redis + RediSearch.
# --------------------------------------------------------------------------- #


class _FakeRedis:
    """Records commands; answers FT.INFO / FT.SEARCH / FT.CREATE from canned replies."""

    def __init__(self, *, dim: int = 4, search: list[Any] | None = None, create: Any = None):
        self.commands: list[tuple[object, ...]] = []
        self.writes = 0
        self._dim = dim
        self._search = search or [0]
        self._create = create

    async def execute_command(self, *args: object) -> Any:
        self.commands.append(args)
        if args[0] == "FT.INFO":
            return [b"attributes", [[b"identifier", b"embedding", b"dim", self._dim]]]
        if args[0] == "FT.SEARCH":
            return self._search
        if args[0] == "FT.CREATE" and self._create is not None:
            raise self._create
        return b"OK"

    def pipeline(self, transaction: bool) -> Any:
        fake = self

        class _Pipe:
            async def __aenter__(self) -> Any:
                return self

            async def __aexit__(self, *exc: object) -> None:
                return None

            def hset(self, key: str, mapping: object) -> None:
                fake.writes += 1

            async def execute(self) -> None:
                return None

        return _Pipe()

    async def aclose(self) -> None:
        return None


def _redis(fake: _FakeRedis) -> Any:
    return _with(vt.RedisVectorTransport, vt.RedisConnectionParams("h", 1), "_client", lambda: fake)


def test_redis_vector_bytes_round_trip() -> None:
    vector = (0.5, -1.25, 3.0, 0.0)
    assert redis_transport._unpack(redis_transport._pack(vector)) == vector


def test_redis_query_is_one_server_side_knn_search() -> None:
    hit = [
        b"rid", b"r1",
        b"embedding", redis_transport._pack((1.0, 0.0, 0.0, 0.0)),
        b"dist", b"0",
    ]
    fake = _FakeRedis(search=[1, b"gw30:c:r1", hit])
    req = QueryRequest(org_id="o", store_id="s", collection="c", vector=(1.0, 0, 0, 0), top_k=7)
    result = asyncio.run(_redis(fake).query(req))
    assert result.code == posture.VECTOR_OK
    assert result.records is not None and [r.id for r in result.records] == ["r1"]
    assert [c[0] for c in fake.commands] == ["FT.SEARCH"]
    search = fake.commands[0]
    assert "KNN $k @embedding $vec AS dist" in str(search[2])
    assert search[search.index("LIMIT") + 2] == "7"
    assert "DIALECT" in search


def test_redis_query_parses_the_resp3_map_reply() -> None:
    """redis-py 8 against redis-stack 7.4 answers FT.SEARCH as a map (captured live)."""
    reply = {
        b"attributes": [],
        b"total_results": 1,
        b"format": b"STRING",
        b"results": [
            {
                b"id": b"gw30:c:r1",
                b"extra_attributes": {
                    b"dist": b"0",
                    b"rid": b"r1",
                    b"embedding": redis_transport._pack((1.0, 0.0, 0.0, 0.0)),
                },
                b"values": [],
            }
        ],
        b"warning": [],
    }
    fake = _FakeRedis(search=reply)  # type: ignore[arg-type]
    req = QueryRequest(org_id="o", store_id="s", collection="c", vector=(1.0, 0, 0, 0), top_k=3)
    result = asyncio.run(_redis(fake).query(req))
    assert result.records is not None and [r.id for r in result.records] == ["r1"]
    assert result.records[0].vector == (1.0, 0.0, 0.0, 0.0)


def test_redis_dimension_reads_the_resp3_info_map() -> None:
    info = {
        b"attributes": [
            {b"identifier": b"rid", b"type": b"TAG"},
            {b"identifier": b"embedding", b"type": b"VECTOR", b"dim": 4},
        ]
    }
    assert redis_transport._pairs(info)["attributes"][1][b"dim"] == 4
    fields = [redis_transport._pairs(a) for a in redis_transport._pairs(info)["attributes"]]
    assert [f.get("dim") for f in fields] == [None, 4]


def test_redis_upsert_with_a_wrong_dimension_writes_nothing() -> None:
    fake = _FakeRedis(dim=4)
    records = (
        VectorRecord(id="a", vector=(1.0, 0.0, 0.0, 0.0), metadata=MappingProxyType({})),
        VectorRecord(id="b", vector=(1.0, 0.0), metadata=MappingProxyType({})),
    )
    req = UpsertRequest(org_id="o", store_id="s", collection="c", records=records)
    with pytest.raises(VectorTransportError):
        asyncio.run(_redis(fake).upsert(req))
    assert fake.writes == 0


def test_redis_existing_index_is_collection_exists() -> None:
    from redis.exceptions import ResponseError

    fake = _FakeRedis(create=ResponseError("Index already exists"))
    assert _exists(_redis(fake)) == posture.COLLECTION_EXISTS


def test_redis_without_the_search_module_fails_closed() -> None:
    from redis.exceptions import ResponseError

    fake = _FakeRedis(create=ResponseError("unknown command 'FT.CREATE'"))
    with pytest.raises(VectorTransportError):
        _exists(_redis(fake))
