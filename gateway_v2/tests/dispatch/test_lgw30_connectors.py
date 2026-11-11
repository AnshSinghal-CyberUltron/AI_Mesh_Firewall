"""LGW30 connector-table unit tests (GW30, task 13.2).

# Feature: vector-store-adapter-gw30
# Validates: Requirements 2.2, 2.3, 3.1, 4.1, 4.2, 4.3

Supporting unit tests for the 13 named connectors and their factory
(design §Testing Strategy — connector-table unit tests; Components §connector
factory + capability table). These pin the properties the property tests do not
cover directly:

* The factory recognises **exactly** the 13 named stores, none omitted and none
  duplicated, and :func:`build_connector_table` maps all 13 (R4.1, R4.2).
* Each named connector's declared :class:`Capabilities` matches the design.md
  capability table exactly — ``filters`` / ``namespaces`` / ``hybrid_search`` per
  store, asserted explicitly per store (R3.1, R4.1).
* Each built connector ``isinstance``-satisfies the :class:`Vector_Adapter`
  protocol (R4.3).
* A ``store_id`` outside the 13 yields **no** connector instance and **no**
  capability value — ``None`` from both :func:`build_connector` and
  :func:`connector_capabilities`, never a silently-substituted default (R4.7 —
  the no-silent-fallback discipline at the factory boundary).
* :func:`build_connector_table` returns a **fresh**, read-only
  :class:`~types.MappingProxyType` each call (mutating attempt raises; two calls
  are distinct objects) — there is NO module-level mutable table (house
  convention / ``lint/check_no_module_mutable.py``).
* A :class:`Named_Vector_Connector` **delegates** each of the six operations to
  its injected :class:`Vector_Transport` — verified by driving a scripted
  :class:`Stub_Vector_Connector` as the transport and watching the scripted
  result flow through unchanged, in script order (R2.2, R2.3).

House idiom: ``from __future__ import annotations``; the expected capability
table is written out literally (a test asserts the product, never recomputes it
with the production helper); async delegation runs through ``asyncio.run``; no
``hypothesis``.

_Design: Testing Strategy (connector-table unit tests); Components §connector
factory + capability table._
_Requirements: 2.2, 2.3, 3.1, 4.1, 4.2, 4.3._
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable
from types import MappingProxyType

import pytest

from gateway_v2.dispatch.vector_adapter import (
    AdapterResult,
    Capabilities,
    CreateRequest,
    DeleteRequest,
    HealthRequest,
    ListRequest,
    QueryRequest,
    UpsertRequest,
    Vector_Adapter,
    VectorRecord,
)
from gateway_v2.dispatch.vector_connectors import (
    STORE_IDS,
    Named_Vector_Connector,
    Stub_Vector_Connector,
    build_connector,
    build_connector_table,
    connector_capabilities,
)
from gateway_v2.domain import posture

# The design.md capability table, written out LITERALLY (never recomputed with
# the production helper): store_id -> (filters, namespaces, hybrid_search). Every
# named store supports `filters`; only `pinecone` and `milvus` add `namespaces`;
# the nine hybrid-search stores are enumerated explicitly.
_EXPECTED_CAPABILITIES: dict[str, tuple[bool, bool, bool]] = {
    "pinecone": (True, True, True),
    "chroma": (True, False, False),
    "milvus": (True, True, True),
    "qdrant": (True, False, True),
    "weaviate": (True, False, True),
    "pgvector": (True, False, False),
    "elasticsearch": (True, False, True),
    "redis": (True, False, True),
    "mongodb_atlas": (True, False, True),
    "aws_opensearch_serverless": (True, False, True),
    "aws_bedrock_kb": (True, False, False),
    "gcp_vertex_vector": (True, False, False),
    "azure_ai_search": (True, False, True),
}

# A trivial, socket-free transport to inject into the named connectors. Its empty
# script means every delegated op would fail closed with PROVIDER_UNREACHABLE —
# that is fine for the structural (isinstance / table-shape) assertions, which
# never invoke an operation. The capabilities value is irrelevant: a named
# connector returns its OWN declared capabilities, never the transport's.
_IRRELEVANT_CAPS = Capabilities(filters=False, namespaces=False, hybrid_search=False)


def _run[T](coro: Awaitable[T]) -> T:
    """Drive one coroutine to completion (house idiom; no async test plugin)."""
    return asyncio.run(coro)  # type: ignore[arg-type]


def _stub_transport(
    script: tuple[AdapterResult, ...] = (),
) -> Stub_Vector_Connector:
    """A socket-free ``Stub_Vector_Connector`` usable as an injected transport."""
    return Stub_Vector_Connector(
        script=script,
        capabilities=_IRRELEVANT_CAPS,
        rng=random.Random(0),
    )


# --------------------------------------------------------------------------- #
# STORE_IDS: exactly 13 distinct recognised ids.
# --------------------------------------------------------------------------- #


def test_store_ids_are_exactly_thirteen_distinct() -> None:
    """``STORE_IDS`` names exactly 13 stores with no duplicates (R4.1, R4.2)."""
    assert len(STORE_IDS) == 13
    assert len(set(STORE_IDS)) == 13  # all distinct — none duplicated
    # The recognised id set is precisely the design table's 13 keys.
    assert set(STORE_IDS) == set(_EXPECTED_CAPABILITIES)


# --------------------------------------------------------------------------- #
# build_connector_table: maps all 13, none omitted / duplicated.
# --------------------------------------------------------------------------- #


def test_build_connector_table_maps_all_thirteen() -> None:
    """The table names exactly the 13 ids, none omitted and none duplicated (R4.1, R4.2)."""
    table = build_connector_table(_stub_transport())

    assert len(table) == 13
    assert set(table.keys()) == set(STORE_IDS)
    # Every recognised id is present (none omitted); the keys are a set so a
    # duplicate would be impossible, but the length pins "none duplicated" too.
    for store_id in STORE_IDS:
        assert store_id in table


def test_build_connector_table_values_are_vector_adapters() -> None:
    """Every built connector ``isinstance``-satisfies ``Vector_Adapter`` (R4.3)."""
    table = build_connector_table(_stub_transport())
    for store_id, conn in table.items():
        assert isinstance(conn, Vector_Adapter), store_id
        assert isinstance(conn, Named_Vector_Connector), store_id


# --------------------------------------------------------------------------- #
# Capability table matches design.md exactly, per store.
# --------------------------------------------------------------------------- #


def test_connector_capabilities_match_design_table_exactly() -> None:
    """Each store's ``capabilities()`` matches the design.md table flag-for-flag (R3.1)."""
    for store_id, (filters, namespaces, hybrid) in _EXPECTED_CAPABILITIES.items():
        caps = connector_capabilities(store_id)
        assert caps is not None, store_id
        # All three flags defined and each exactly as the design table declares.
        assert caps.filters is filters, store_id
        assert caps.namespaces is namespaces, store_id
        assert caps.hybrid_search is hybrid, store_id


def test_built_connector_capabilities_match_design_table_exactly() -> None:
    """A built connector returns its store's design-table flags verbatim (R3.1, R4.3)."""
    table = build_connector_table(_stub_transport())
    for store_id, (filters, namespaces, hybrid) in _EXPECTED_CAPABILITIES.items():
        caps = table[store_id].capabilities()
        assert caps.filters is filters, store_id
        assert caps.namespaces is namespaces, store_id
        assert caps.hybrid_search is hybrid, store_id


def test_every_store_supports_filters() -> None:
    """``filters`` is true for every one of the 13 named stores (design table)."""
    for store_id in STORE_IDS:
        caps = connector_capabilities(store_id)
        assert caps is not None
        assert caps.filters is True, store_id


def test_only_pinecone_and_milvus_support_namespaces() -> None:
    """``namespaces`` is true for exactly ``pinecone`` and ``milvus`` (design table)."""
    with_namespaces = {
        store_id
        for store_id in STORE_IDS
        if (caps := connector_capabilities(store_id)) is not None and caps.namespaces
    }
    assert with_namespaces == {"pinecone", "milvus"}


# --------------------------------------------------------------------------- #
# A store_id outside the 13 → None from both factory helpers (no silent default).
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "unknown",
    ("", "not_a_store", "PINECONE", "pinecone ", "faiss", "lancedb"),
)
def test_unknown_store_id_returns_none(unknown: str) -> None:
    """An id outside the 13 yields no connector and no capabilities (R4.7)."""
    assert build_connector(unknown, _stub_transport()) is None
    assert connector_capabilities(unknown) is None


def test_unknown_store_id_absent_from_table() -> None:
    """The table never carries an id outside the 13 recognised stores (R4.1, R4.7)."""
    table = build_connector_table(_stub_transport())
    assert "faiss" not in table
    assert "" not in table
    assert "PINECONE" not in table


# --------------------------------------------------------------------------- #
# build_connector_table: fresh read-only MappingProxyType each call.
# --------------------------------------------------------------------------- #


def test_build_connector_table_is_read_only_mapping_proxy() -> None:
    """The table is a ``MappingProxyType`` and mutation raises (no module mutable)."""
    table = build_connector_table(_stub_transport())
    assert isinstance(table, MappingProxyType)
    with pytest.raises(TypeError):
        table["pinecone"] = _stub_transport()  # type: ignore[index]
    with pytest.raises(TypeError):
        del table["pinecone"]  # type: ignore[attr-defined]


def test_build_connector_table_returns_fresh_object_each_call() -> None:
    """Two calls return DISTINCT mapping objects — no shared module-level table."""
    transport = _stub_transport()
    first = build_connector_table(transport)
    second = build_connector_table(transport)
    # Distinct objects: the factory builds a fresh mapping each call.
    assert first is not second
    # Same recognised key set, independently built.
    assert set(first) == set(second) == set(STORE_IDS)
    # The connector instances are freshly built too, not shared across calls.
    assert first["pinecone"] is not second["pinecone"]


# --------------------------------------------------------------------------- #
# Named_Vector_Connector delegates each op to the injected transport.
# --------------------------------------------------------------------------- #


def _ok_records() -> AdapterResult:
    rec = VectorRecord(id="r1", vector=(0.1, 0.2), metadata=MappingProxyType({"k": 1}))
    return AdapterResult(code=posture.VECTOR_OK, records=(rec,))


def test_named_connector_delegates_each_op_to_injected_transport() -> None:
    """Each of the six ops flows through the injected transport, in script order (R2.2, R2.3).

    A ``Stub_Vector_Connector`` is injected as the ``Vector_Transport``; its
    scripted results are returned strictly in order across the six operations.
    Driving the named connector's six ops in a fixed order must surface exactly
    that scripted sequence, proving the connector delegates (it holds no results
    of its own) and does not reorder, drop, or fabricate.
    """
    # One distinctive scripted result per operation, in the call order below.
    script = (
        _ok_records(),                                            # query
        AdapterResult(code=posture.VECTOR_OK, count=7),           # upsert
        AdapterResult(code=posture.VECTOR_OK, count=3),           # delete
        AdapterResult(code=posture.VECTOR_OK, collections=("a", "b")),  # list
        AdapterResult(code=posture.VECTOR_OK, status="reachable"),      # create
        AdapterResult(code=posture.VECTOR_OK, status="degraded"),       # health
    )
    transport = _stub_transport(script)
    conn = Named_Vector_Connector(
        store_id="pinecone",
        capabilities=connector_capabilities("pinecone"),  # type: ignore[arg-type]
        transport=transport,
    )

    q = _run(conn.query(QueryRequest(
        org_id="o", store_id="pinecone", collection="c", vector=(0.0,), top_k=1
    )))
    u = _run(conn.upsert(UpsertRequest(
        org_id="o", store_id="pinecone", collection="c", records=()
    )))
    d = _run(conn.delete(DeleteRequest(
        org_id="o", store_id="pinecone", collection="c", ids=()
    )))
    lst = _run(conn.list_collections(ListRequest(org_id="o", store_id="pinecone")))
    cr = _run(conn.create_collection(CreateRequest(
        org_id="o", store_id="pinecone", collection="c", dimension=1, metric="cosine"
    )))
    h = _run(conn.health(HealthRequest(org_id="o", store_id="pinecone")))

    # The scripted result for each op flows through unchanged, in script order.
    assert (q, u, d, lst, cr, h) == script


def test_named_connector_returns_own_capabilities_not_transport() -> None:
    """``capabilities()`` returns the connector's declared flags, not the transport's (R4.3)."""
    # Transport advertises nothing; the connector must still report pinecone's flags.
    transport = _stub_transport()
    conn = Named_Vector_Connector(
        store_id="pinecone",
        capabilities=connector_capabilities("pinecone"),  # type: ignore[arg-type]
        transport=transport,
    )
    caps = conn.capabilities()
    assert (caps.filters, caps.namespaces, caps.hybrid_search) == (True, True, True)
    assert caps != transport.capabilities()
