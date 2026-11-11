"""GW30 vector-store connectors — the in-process stub (task 7.1).

This module is the vector-store analogue of ``dispatch/provider.py``'s
``StubProviderClient``. :class:`Stub_Vector_Connector` is an in-process,
**socket-free** implementation of the :class:`~gateway_v2.dispatch.vector_adapter.Vector_Adapter`
protocol that local unit and property tests inject in place of a real per-store
SDK/HTTP client (R4.4).

It also owns the **13 named connectors and their factory** (task 13.1, R4):
:class:`Named_Vector_Connector` is the thin :class:`Vector_Adapter` each named
store presents over an **injected transport** (the :class:`Vector_Transport`
seam below, the analogue of ``dispatch/provider.py``'s injected
``ProviderClient``); the real per-store SDK/HTTP transport is a **deferred live
gate** — not built here — exercised only by L30-1/L30-2/L30-3. The capability
table that names the 13 stores and each one's declared capability flags is
exposed as **factory functions** (:func:`connector_capabilities`,
:func:`build_connector`, :func:`build_connector_table`) that build and return a
**fresh immutable** structure per call — there is NO module-level mutable table
(gated by ``lint/check_no_module_mutable.py``); the only module-level datum is
the frozen :data:`STORE_IDS` tuple of the 13 recognised ids.

The stub mirrors ``StubProviderClient`` exactly:

* A seeded ``random.Random`` is **injected** at construction and retained — it is
  NEVER created internally, so a test owns the single deterministic source and
  two stubs built from the same seed + same script are byte-identical (R4.5,
  R4.6). As with ``StubProviderClient`` the ``rng`` is retained for parity with
  the real connector's injected-randomness contract; the deterministic script
  replay below does not consume it.
* A scripted sequence of :class:`~gateway_v2.dispatch.vector_adapter.AdapterResult`
  is returned **strictly in script order** across the six operations: each
  awaited operation returns the next scripted result, regardless of which of the
  six it is, so the replay order is the script order the test declared. When the
  script is exhausted the stub fails closed with ``PROVIDER_UNREACHABLE`` rather
  than raising or fabricating an empty success.
* A :class:`~gateway_v2.dispatch.vector_adapter.Capabilities` value is provided
  through the constructor and returned verbatim by ``capabilities()``.

It opens no network socket, file handle, or other external I/O — the replay is a
pure walk over an immutable tuple. Because it supplies all six async operations
plus ``capabilities()``, ``isinstance(stub, Vector_Adapter)`` is ``True`` (the
``@runtime_checkable`` protocol check tasks 6/7 rely on).

**Layering.** ``dispatch`` sits above ``domain`` in the import-linter layer
order; this module imports only the standard library plus the sibling
``vector_adapter`` value types and ``domain.posture`` codes, so it adds no new
layer edge. There is NO module-level mutable table (gated by
``lint/check_no_module_mutable.py``) — the script is a per-instance tuple — and
NO capacity literal (all bounds live only in ``runtime/resources.py``).
"""

from __future__ import annotations

import random
from collections.abc import Mapping, Sequence
from types import MappingProxyType
from typing import Protocol, runtime_checkable

from gateway_v2.domain import posture

from .vector_adapter import (
    AdapterResult,
    Capabilities,
    CreateRequest,
    DeleteRequest,
    HealthRequest,
    ListRequest,
    QueryRequest,
    UpsertRequest,
    Vector_Adapter,
)

__all__ = (
    "STORE_IDS",
    "Named_Vector_Connector",
    "Stub_Vector_Connector",
    "Vector_Transport",
    "build_connector",
    "build_connector_table",
    "connector_capabilities",
)


class Stub_Vector_Connector:
    """An in-process :class:`Vector_Adapter` for local tests — NO real socket.

    Driven by a seeded ``random.Random`` (``rng``, injected — never created here)
    and a scripted ``script`` of :class:`AdapterResult` replayed strictly in
    order across the six operations. ``capabilities()`` returns the injected
    :class:`Capabilities` verbatim. When the script is exhausted every further
    operation fails closed with ``PROVIDER_UNREACHABLE`` (``records is None``),
    never raising and never returning an empty success.

    It is a plain slotted class rather than a frozen dataclass because the replay
    cursor is mutable one-way state; the values it returns (``AdapterResult`` /
    ``Capabilities``) are the frozen slotted dataclasses. ``rng`` is retained for
    parity with the real connector's injected-randomness contract and so scripted
    callers share one deterministic source; the deterministic replay does not
    consume it.
    """

    __slots__ = ("_capabilities", "_cursor", "rng", "script")

    def __init__(
        self,
        *,
        script: Sequence[AdapterResult],
        capabilities: Capabilities,
        rng: random.Random,
    ) -> None:
        # Snapshot the script into an immutable tuple so a caller mutating the
        # passed-in sequence after construction cannot change what is replayed,
        # and so there is no shared mutable state between calls.
        self.script: tuple[AdapterResult, ...] = tuple(script)
        self._capabilities = capabilities
        self.rng = rng
        self._cursor = 0

    def _next(self) -> AdapterResult:
        """Return the next scripted result, or fail closed when exhausted.

        The cursor advances by exactly one per operation so results are returned
        strictly in script order across the six operations (R4.5). Once the
        script is exhausted the stub returns a declared ``PROVIDER_UNREACHABLE``
        failure (``records is None``) rather than raising or inventing an empty
        success — fail closed, outcome by ``code`` alone.
        """
        if self._cursor >= len(self.script):
            return AdapterResult(code=posture.PROVIDER_UNREACHABLE)
        result = self.script[self._cursor]
        self._cursor += 1
        return result

    async def query(self, req: QueryRequest) -> AdapterResult:
        """Replay the next scripted result for a ``query`` (R4.4)."""
        return self._next()

    async def upsert(self, req: UpsertRequest) -> AdapterResult:
        """Replay the next scripted result for an ``upsert`` (R4.4)."""
        return self._next()

    async def delete(self, req: DeleteRequest) -> AdapterResult:
        """Replay the next scripted result for a ``delete`` (R4.4)."""
        return self._next()

    async def list_collections(self, req: ListRequest) -> AdapterResult:
        """Replay the next scripted result for a ``list_collections`` (R4.4)."""
        return self._next()

    async def create_collection(self, req: CreateRequest) -> AdapterResult:
        """Replay the next scripted result for a ``create_collection`` (R4.4)."""
        return self._next()

    async def health(self, req: HealthRequest) -> AdapterResult:
        """Replay the next scripted result for a ``health`` probe (R4.4)."""
        return self._next()

    def capabilities(self) -> Capabilities:
        """Return the injected :class:`Capabilities` verbatim (R4.4)."""
        return self._capabilities


# --------------------------------------------------------------------------- #
# Vector_Transport — the injected per-store wire seam (deferred live gate).
# --------------------------------------------------------------------------- #


@runtime_checkable
class Vector_Transport(Protocol):
    """The per-store wire seam a named connector drives, **injected** (R4, Scope).

    The direct analogue of ``dispatch/provider.py``'s ``ProviderClient``: a
    named connector is a *thin* :class:`Vector_Adapter` that shapes an adapter
    request onto this transport and maps the transport's
    :class:`~gateway_v2.dispatch.vector_adapter.AdapterResult` back out. The real
    per-store SDK/HTTP client that satisfies this protocol is a **deferred live
    gate** — it opens the real socket and is wired in behind the adapter at the
    live-gate task (16.1), exactly as ``StubProviderClient`` defers the real
    socket. Offline unit and property tests inject the socket-free
    :class:`Stub_Vector_Connector`, which itself satisfies this protocol (it
    supplies the same six async operations), so a named connector can be driven
    entirely in-process.

    The protocol declares exactly the six vector operations — the connector adds
    only its declared :class:`Capabilities` and the uniform-result discipline on
    top, carrying no capacity literal and no module-level mutable state. Because
    it is ``@runtime_checkable`` a test can assert an injected transport
    satisfies it with ``isinstance``.

    **Layering.** The transport is injected from ``edge`` / ``dispatch`` and is
    never imported by any layer below ``dispatch`` — the same inject-never-
    import-upward discipline ``ProviderClient`` uses and ``lint-imports`` gates.
    """

    async def query(self, req: QueryRequest) -> AdapterResult: ...

    async def upsert(self, req: UpsertRequest) -> AdapterResult: ...

    async def delete(self, req: DeleteRequest) -> AdapterResult: ...

    async def list_collections(self, req: ListRequest) -> AdapterResult: ...

    async def create_collection(self, req: CreateRequest) -> AdapterResult: ...

    async def health(self, req: HealthRequest) -> AdapterResult: ...


# --------------------------------------------------------------------------- #
# The 13 named stores and their declared capability flags (design.md table).
# --------------------------------------------------------------------------- #

#: The exact 13 recognised vector-store ids, in the design table's order. A frozen
#: ``tuple`` (never a list/dict/set) so it is immutable and does NOT trip
#: ``lint/check_no_module_mutable.py``; it names which stores the factory
#: recognises, while the per-store capability flags are rebuilt fresh per call by
#: :func:`connector_capabilities` so there is no module-level capability table.
STORE_IDS: tuple[str, ...] = (
    "pinecone",
    "chroma",
    "milvus",
    "qdrant",
    "weaviate",
    "pgvector",
    "elasticsearch",
    "redis",
    "mongodb_atlas",
    "aws_opensearch_serverless",
    "aws_bedrock_kb",
    "gcp_vertex_vector",
    "azure_ai_search",
)


def connector_capabilities(store_id: str) -> Capabilities | None:
    """Return a **fresh** :class:`Capabilities` for ``store_id``, or ``None`` (R4.1, R4.7).

    The capability flags per the design.md table — ``filters`` / ``namespaces`` /
    ``hybrid_search`` — are materialised here as a brand-new frozen
    :class:`Capabilities` value **built per call**, so the capability table is a
    function rather than a module-level mutable dict (``lint/check_no_module_mutable.py``).
    Every one of the 13 stores supports ``filters``; only ``pinecone`` and
    ``milvus`` additionally support ``namespaces``; ``pinecone``, ``milvus``,
    ``qdrant``, ``weaviate``, ``elasticsearch``, ``redis``, ``mongodb_atlas``,
    ``aws_opensearch_serverless`` and ``azure_ai_search`` support
    ``hybrid_search``.

    A ``store_id`` outside :data:`STORE_IDS` returns ``None`` (the store is not
    one of the 13; the caller treats it as unsupported, R4.7). The three flags on
    a returned value are always all defined (plain ``bool`` fields, no default).
    """
    if store_id not in STORE_IDS:
        return None
    # Built fresh per call — no module-level capability table. ``namespaces`` and
    # ``hybrid_search`` are the only flags that vary across the 13; ``filters`` is
    # true for every named store (design.md capability table).
    namespaces = store_id in ("pinecone", "milvus")
    hybrid_search = store_id in (
        "pinecone",
        "milvus",
        "qdrant",
        "weaviate",
        "elasticsearch",
        "redis",
        "mongodb_atlas",
        "aws_opensearch_serverless",
        "azure_ai_search",
    )
    return Capabilities(filters=True, namespaces=namespaces, hybrid_search=hybrid_search)


# --------------------------------------------------------------------------- #
# Named_Vector_Connector — the thin adapter over the injected transport.
# --------------------------------------------------------------------------- #


class Named_Vector_Connector:
    """A thin :class:`Vector_Adapter` for one named store over an injected transport (R4).

    One of the 13 named connectors. It is deliberately *thin*: it holds its
    ``store_id``, its declared :class:`Capabilities`, and the **injected**
    :class:`Vector_Transport`, and delegates each of the six operations straight
    to the transport while returning its own capability flags from
    ``capabilities()``. The real per-store wire behaviour (connection, bounds,
    transport-outcome mapping) lives behind the transport — a deferred live gate
    (task 16.1) offline-stood-in by :class:`Stub_Vector_Connector` — so the
    connector itself opens no socket and carries no capacity literal.

    Because it supplies all six async operations plus ``capabilities()``,
    ``isinstance(conn, Vector_Adapter)`` is ``True`` (R4.3) — the registry (task
    8) accepts it and the capability-enforcement path (task 9) reads its flags. It
    is a plain slotted class (not a frozen dataclass) only so the injected
    transport can be any ``Vector_Transport`` implementation; it holds no mutable
    state of its own and performs no mutation.
    """

    __slots__ = ("_capabilities", "_transport", "store_id")

    def __init__(
        self,
        *,
        store_id: str,
        capabilities: Capabilities,
        transport: Vector_Transport,
    ) -> None:
        """Bind the ``store_id``, declared :class:`Capabilities`, and injected transport."""
        self.store_id = store_id
        self._capabilities = capabilities
        self._transport = transport

    async def query(self, req: QueryRequest) -> AdapterResult:
        """Delegate a ``query`` to the injected transport (R4)."""
        return await self._transport.query(req)

    async def upsert(self, req: UpsertRequest) -> AdapterResult:
        """Delegate an ``upsert`` to the injected transport (R4)."""
        return await self._transport.upsert(req)

    async def delete(self, req: DeleteRequest) -> AdapterResult:
        """Delegate a ``delete`` to the injected transport (R4)."""
        return await self._transport.delete(req)

    async def list_collections(self, req: ListRequest) -> AdapterResult:
        """Delegate a ``list_collections`` to the injected transport (R4)."""
        return await self._transport.list_collections(req)

    async def create_collection(self, req: CreateRequest) -> AdapterResult:
        """Delegate a ``create_collection`` to the injected transport (R4)."""
        return await self._transport.create_collection(req)

    async def health(self, req: HealthRequest) -> AdapterResult:
        """Delegate a ``health`` probe to the injected transport (R4)."""
        return await self._transport.health(req)

    def capabilities(self) -> Capabilities:
        """Return this named store's declared :class:`Capabilities` verbatim (R4.3)."""
        return self._capabilities


# --------------------------------------------------------------------------- #
# Factory — build a connector / the whole table (no module-level mutable).
# --------------------------------------------------------------------------- #


def build_connector(
    store_id: str,
    transport: Vector_Transport,
) -> Named_Vector_Connector | None:
    """Build the named connector for ``store_id`` over ``transport``, or ``None`` (R4.1, R4.7).

    A **factory**: it looks up the store's declared capability flags
    (:func:`connector_capabilities`, built fresh per call) and returns a new
    :class:`Named_Vector_Connector` bound to that capability value and the
    injected ``transport``. A ``store_id`` outside the 13 recognised
    :data:`STORE_IDS` is rejected as unsupported and returns **no** connector
    instance (``None``), never a silently-substituted default (R4.7). The returned
    connector ``isinstance``-satisfies :class:`Vector_Adapter` (R4.3).
    """
    caps = connector_capabilities(store_id)
    if caps is None:
        return None
    return Named_Vector_Connector(
        store_id=store_id,
        capabilities=caps,
        transport=transport,
    )


def build_connector_table(
    transport: Vector_Transport,
) -> Mapping[str, Vector_Adapter]:
    """Build the whole ``store_id -> Vector_Adapter`` table over ``transport`` (R4.1, R4.2).

    A **factory** returning a **fresh**, read-only
    :class:`~types.MappingProxyType` each call — never a module-level mutable
    dict (``lint/check_no_module_mutable.py``). The table names **exactly** the 13
    :data:`STORE_IDS`, none omitted and none duplicated (``STORE_IDS`` is a tuple
    of distinct ids), each mapped to a thin :class:`Named_Vector_Connector` over
    the single injected ``transport``. This is the mapping ``edge`` / ``dispatch``
    hands to :class:`~gateway_v2.dispatch.vector_registry.VectorRegistry`; the
    registry wraps it in its own read-only view, so no caller can mutate the set
    of registered connectors. Every value ``isinstance``-satisfies
    :class:`Vector_Adapter` (R4.3).
    """
    table: dict[str, Vector_Adapter] = {}
    for store_id in STORE_IDS:
        connector = build_connector(store_id, transport)
        # connector is non-None for every id in STORE_IDS (connector_capabilities
        # returns a value for each); the guard keeps mypy --strict happy and makes
        # the never-None invariant explicit.
        if connector is not None:
            table[store_id] = connector
    return MappingProxyType(table)
