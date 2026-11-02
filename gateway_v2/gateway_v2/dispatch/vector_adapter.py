"""GW30 vector-store adapter value types (data models).

This module owns the frozen, slotted value vocabulary for the one-adapter-
interface-for-every-vector-store layer. It is the direct analogue of
``dispatch/provider.py`` (which owns ``UpstreamRequest`` / ``UpstreamEvent`` for
the SSE egress path): every value here is a ``@dataclass(frozen=True,
slots=True)`` so each is hashable, immutable, and allocation-bounded by its
slots. Nothing here carries a clock, randomness, or module-level mutable state.

What this module defines (task 4.1 scope — value types ONLY):

* :class:`VectorRecord` — the id + embedding + immutable metadata record carried
  inside a successful ``query`` result and supplied inside an ``UpsertRequest``.
* :class:`AdapterResult` — the single uniform value EVERY adapter operation
  returns. Its success-vs-failure outcome is distinguishable **by ``code``
  alone** (R1.6, R1.7, R5.4, R6.6): a legitimate zero-record success
  (``code == posture.VECTOR_OK`` with ``records == ()``) is never confusable
  with a failure (any non-OK code, ``records is None``).
* :class:`Capabilities` — exactly three defined booleans (``filters``,
  ``namespaces``, ``hybrid_search``), the mechanism by which heterogeneous
  stores present a uniform adapter (R3.1).
* The six request value types — one per protocol operation
  (:class:`QueryRequest`, :class:`UpsertRequest`, :class:`DeleteRequest`,
  :class:`ListRequest`, :class:`CreateRequest`, :class:`HealthRequest`). Each
  carries the request ``org_id``, the target ``store_id``, the ``collection``
  identifier where the operation applies to a collection, and that operation's
  payload as immutable tuples. **None of them carries credential material** —
  the credential is resolved by the connector from the injected
  ``Credential_Source``, never passed in by the caller (R2.1).

The ``Vector_Adapter`` protocol and ``VectorRegistry`` (tasks 6/8/9) are NOT
defined here yet; this task ships only the data models.

**Absent (``None``) vs empty (``()``) invariant** (the heart of R5.5 / R6.6).
On any non-OK ``code``, ``records is None`` — **absent**: "no search happened /
the operation failed". A successful ``query`` that genuinely matched nothing
returns ``code == posture.VECTOR_OK`` with ``records == ()`` — **present but
empty**: "the search ran and matched zero records". ``None`` and ``()`` are
therefore never interchangeable. ``count`` / ``collections`` / ``status`` follow
the same rule — present only on the relevant success, ``None`` otherwise.
``detail_ref`` is only ever a credential **reference id**, never secret material.

**Layering.** The posture codes are imported from ``gateway_v2.domain.posture``
(``domain`` sits at the bottom of the import-linter layer order and ``dispatch``
sits above it), matching the ``from gateway_v2.domain import posture`` idiom used
across ``dispatch`` / ``egress`` / ``edge``. This module imports only the
standard library plus that one ``domain`` edge, so it adds no new layer edge and
holds no capacity literal and no module-level mutable table.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from gateway_v2.domain import posture

__all__ = (
    "AdapterResult",
    "Capabilities",
    "CreateRequest",
    "DeleteRequest",
    "HealthRequest",
    "ListRequest",
    "QueryRequest",
    "UpsertRequest",
    "VectorRecord",
    "Vector_Adapter",
)


# --------------------------------------------------------------------------- #
# VectorRecord — the id + vector + immutable metadata record.
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class VectorRecord:
    """The id + embedding + metadata record (R1.3).

    Returned inside a successful ``query`` (:attr:`AdapterResult.records`) and
    supplied inside an :class:`UpsertRequest`. The embedding is an immutable
    tuple of floats; ``metadata`` is an immutable ``Mapping`` of string keys to
    scalar values, built by the caller from a ``MappingProxyType`` so the record
    is as immutable as its frozen dataclass promises (the same immutable-body
    idiom as ``UpstreamRequest.body``). A ``MappingProxyType`` satisfies the
    ``Mapping`` annotation and rejects item assignment, so the metadata cannot be
    mutated through the record.
    """

    id: str
    vector: tuple[float, ...]
    metadata: Mapping[str, str | int | float | bool]

    def __hash__(self) -> int:
        """Hash a canonical immutable view of the record (R1.6, R6.6).

        The dataclass-generated ``__eq__`` compares ``metadata`` by value, but a
        ``MappingProxyType``-backed ``Mapping`` is itself unhashable, so the
        auto-generated ``__hash__`` would raise ``TypeError`` and make every
        value carrying a :class:`VectorRecord` (an :class:`AdapterResult` or an
        :class:`UpsertRequest`) unhashable too. Hashing a canonical immutable
        view — the id, the vector tuple, and a ``frozenset`` of the metadata
        items — keeps ``eq``/``hash`` consistent (records equal under the
        generated ``__eq__`` hash equal, since equal metadata yields the same
        item frozenset) while preserving ``@dataclass(frozen=True, slots=True)``
        and the immutable ``MappingProxyType``-backed metadata field.
        """
        return hash((self.id, self.vector, frozenset(self.metadata.items())))


# --------------------------------------------------------------------------- #
# AdapterResult — the uniform operation result (outcome by code alone).
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class AdapterResult:
    """The single value EVERY ``Vector_Adapter`` operation returns (R1.6, R1.7).

    The success-vs-failure outcome is distinguishable **by ``code`` alone**
    (R5.4, R6.6), so a legitimate zero-record success is never confusable with
    any failure.

    Absent-vs-empty invariant (R5.5 / R6.6):

    * On any non-OK ``code``: ``records is None`` (ABSENT) — "no search happened
      / the operation failed".
    * A successful ``query`` that matched nothing: ``code == posture.VECTOR_OK``
      with ``records == ()`` (PRESENT but empty) — "the search ran and matched
      zero records".

    ``count`` is the applied/removed count for an ``upsert`` / ``delete``
    success; ``collections`` the names on a ``list_collections`` success;
    ``status`` the ``"reachable"`` / ``"degraded"`` string on a ``health``
    success. Each is present only on its relevant success and ``None`` otherwise.
    ``detail_ref`` is only ever a credential **reference id** — never secret
    material.
    """

    code: str
    records: tuple[VectorRecord, ...] | None = None
    count: int | None = None
    collections: tuple[str, ...] | None = None
    status: str | None = None
    detail_ref: str | None = None

    @property
    def ok(self) -> bool:
        """``True`` iff this is the single success outcome (``code == VECTOR_OK``)."""
        return self.code == posture.VECTOR_OK


# --------------------------------------------------------------------------- #
# Capabilities — the three defined per-connector capability flags (R3).
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class Capabilities:
    """The per-connector capability flags (R3.1).

    Exactly three booleans, each **defined** (no flag left undefined). A call
    exercising a capability a connector declares ``False`` returns
    ``unsupported_operation`` — never a silently-ignored option, never an empty
    result (enforced in task 9). Being plain ``bool`` fields with no defaults,
    every instance carries all three flags explicitly.
    """

    filters: bool
    namespaces: bool
    hybrid_search: bool


# --------------------------------------------------------------------------- #
# Request value types — one per operation (NO credential material, R2.1).
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class QueryRequest:
    """A ``query`` request (R2.1).

    Carries the query embedding plus ``top_k``; the optional ``filter`` is
    applied only where ``capabilities().filters`` is true and the optional
    ``namespace`` honoured only where ``capabilities().namespaces`` is true
    (otherwise the operation returns ``unsupported_operation``, task 9). The
    ``filter`` is an immutable ``Mapping`` (a ``MappingProxyType`` in practice)
    so the request stays immutable.
    """

    org_id: str
    store_id: str
    collection: str
    vector: tuple[float, ...]
    top_k: int
    filter: Mapping[str, str | int | float | bool] | None = None
    namespace: str | None = None


@dataclass(frozen=True, slots=True)
class UpsertRequest:
    """An ``upsert`` request (R2.1).

    The record set to write is carried as an immutable tuple of
    :class:`VectorRecord` so the request is immutable, matching the
    ``UpstreamRequest.body`` tuple idiom.
    """

    org_id: str
    store_id: str
    collection: str
    records: tuple[VectorRecord, ...]
    namespace: str | None = None


@dataclass(frozen=True, slots=True)
class DeleteRequest:
    """A ``delete`` request (R2.1).

    The record ids to remove are carried as an immutable tuple of ``str``.
    """

    org_id: str
    store_id: str
    collection: str
    ids: tuple[str, ...]
    namespace: str | None = None


@dataclass(frozen=True, slots=True)
class ListRequest:
    """A ``list_collections`` request (R2.1).

    Has no single collection target — it lists the collections of ``store_id``.
    """

    org_id: str
    store_id: str


@dataclass(frozen=True, slots=True)
class CreateRequest:
    """A ``create_collection`` request (R2.1).

    Carries the ``collection`` to create, its embedding ``dimension``, and the
    distance ``metric`` (e.g. ``"cosine"``).
    """

    org_id: str
    store_id: str
    collection: str
    dimension: int
    metric: str


@dataclass(frozen=True, slots=True)
class HealthRequest:
    """A ``health`` request (R2.1) — probe the reachability of ``store_id``."""

    org_id: str
    store_id: str


# --------------------------------------------------------------------------- #
# Vector_Adapter protocol (task 6.1, R1.1 / R1.2 / R1.3 / R1.4 / R1.5).
# --------------------------------------------------------------------------- #


@runtime_checkable
class Vector_Adapter(Protocol):
    """The one adapter interface every vector store presents (R1.1, R1.2).

    Mirrors ``dispatch/provider.py``'s ``ProviderClient``: it is the injectable
    seam the registry (task 8) and the RAG pipeline (GW31) read from. It declares
    **exactly** the six vector operations plus the ``capabilities`` accessor and
    nothing else, so a connector conforms iff it supplies all six operations and
    ``capabilities`` (R1.4, R1.5). The protocol is ``@runtime_checkable`` so a
    test can assert a connector satisfies it with ``isinstance`` and the registry
    can refuse a non-conforming connector with a declared code (R1.1, R1.2).

    Every operation returns the single uniform :class:`AdapterResult` whose
    success-vs-failure outcome is distinguishable **by ``code`` alone** (R1.3,
    R1.6, R1.7): no operation raises to the caller and none returns an empty
    success in place of a failure — those invariants are enforced on the
    connector op path (tasks 9 / 12), while this protocol only fixes the shape.

    **Layering.** The protocol lives in ``dispatch`` and is injected from
    ``edge`` / ``dispatch``; no layer below ``dispatch`` imports it (the same
    discipline as ``ProviderClient``), gated by ``lint-imports``.
    """

    async def query(self, req: QueryRequest) -> AdapterResult: ...

    async def upsert(self, req: UpsertRequest) -> AdapterResult: ...

    async def delete(self, req: DeleteRequest) -> AdapterResult: ...

    async def list_collections(self, req: ListRequest) -> AdapterResult: ...

    async def create_collection(self, req: CreateRequest) -> AdapterResult: ...

    async def health(self, req: HealthRequest) -> AdapterResult: ...

    def capabilities(self) -> Capabilities: ...
