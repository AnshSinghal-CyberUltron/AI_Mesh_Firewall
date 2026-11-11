"""LGW30 ``Vector_Adapter`` protocol-conformance unit tests (GW30, task 6.2).

Pins the ``@runtime_checkable`` protocol contract of
``dispatch/vector_adapter.Vector_Adapter`` (task 6.1):

* A class supplying all six Adapter_Operations (``query`` / ``upsert`` /
  ``delete`` / ``list_collections`` / ``create_collection`` / ``health``) plus
  the ``capabilities`` accessor is ``isinstance(conn, Vector_Adapter)`` true —
  the shipped ``Stub_Vector_Connector`` is the canonical such class (R1.1, R1.2).
* A class missing **any one** of the six operations, or ``capabilities``, is NOT
  an instance of the protocol (one negative case per missing member) — the
  ``@runtime_checkable`` structural check is exactly how the registry rejects a
  non-conforming connector with a declared code (R1.1, R1.2).
* All six operations are declared on the protocol, and a conforming stub call
  returns an :class:`AdapterResult` (R1.4, R1.5 — the uniform result shape every
  operation yields).

These are supporting unit tests (concrete examples / boundaries), the companion
to the seeded property tests; no ``hypothesis``. The :class:`Stub_Vector_Connector`
is built with an injected ``random.Random`` (never created internally), a short
scripted ``AdapterResult`` sequence, and a :class:`Capabilities` value, per its
constructor contract.

_Design: Testing Strategy (protocol conformance); Components §Vector_Adapter._
_Requirements: 1.1, 1.2, 1.4, 1.5._
"""

from __future__ import annotations

import asyncio
import random

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
)
from gateway_v2.dispatch.vector_connectors import Stub_Vector_Connector
from gateway_v2.domain import posture

# A seeded source owned by the test — the stub never creates its own.
_SEED = 1730

# The six operations the protocol declares, plus the capability accessor, in a
# single place so the "declares all six" and "missing one is non-conforming"
# assertions stay in lock-step with the protocol surface.
_SIX_OPERATIONS = (
    "query",
    "upsert",
    "delete",
    "list_collections",
    "create_collection",
    "health",
)
_PROTOCOL_MEMBERS = (*_SIX_OPERATIONS, "capabilities")


def _capabilities() -> Capabilities:
    """A fully-defined capability value for the conforming fixtures."""
    return Capabilities(filters=True, namespaces=False, hybrid_search=True)


def _stub(script: tuple[AdapterResult, ...] = ()) -> Stub_Vector_Connector:
    """A conforming :class:`Stub_Vector_Connector` with an injected ``rng``.

    The ``rng`` is a seeded ``random.Random`` owned by the test (never created
    inside the stub), the ``script`` is a short scripted ``AdapterResult``
    sequence, and ``capabilities`` is a fully-defined :class:`Capabilities`.
    """
    return Stub_Vector_Connector(
        script=script,
        capabilities=_capabilities(),
        rng=random.Random(_SEED),
    )


# --------------------------------------------------------------------------- #
# Conforming connector is an instance of the protocol.
# --------------------------------------------------------------------------- #


def test_full_connector_is_instance_of_protocol() -> None:
    """A class with all six ops + ``capabilities`` is a ``Vector_Adapter`` (R1.1, R1.2)."""
    stub = _stub()
    assert isinstance(stub, Vector_Adapter)


def test_protocol_declares_exactly_the_six_operations() -> None:
    """All six Adapter_Operations are declared on the protocol (R1.1, R1.4)."""
    for operation in _SIX_OPERATIONS:
        assert hasattr(Vector_Adapter, operation), (
            f"Vector_Adapter is missing the declared operation {operation!r}"
        )
    # ``capabilities`` is the seventh declared member alongside the six ops.
    assert hasattr(Vector_Adapter, "capabilities")


# --------------------------------------------------------------------------- #
# A class missing any one member is NOT an instance (one negative case each).
# --------------------------------------------------------------------------- #


def _make_partial_class(missing: str) -> type:
    """Build a connector-shaped class that omits exactly ``missing``.

    Every other protocol member is present (as a trivial callable), so the only
    reason ``isinstance`` can fail is the one omitted member — isolating the
    structural check to a single missing operation / accessor per case.
    """

    namespace: dict[str, object] = {}
    for member in _PROTOCOL_MEMBERS:
        if member == missing:
            continue
        if member == "capabilities":

            def _capabilities_impl(self: object) -> Capabilities:
                return Capabilities(filters=False, namespaces=False, hybrid_search=False)

            namespace[member] = _capabilities_impl
        else:

            async def _op_impl(self: object, req: object) -> AdapterResult:
                return AdapterResult(code=posture.VECTOR_OK, records=())

            namespace[member] = _op_impl

    return type(f"Partial_Missing_{missing}", (), namespace)


def test_class_missing_any_single_member_is_not_an_instance() -> None:
    """Omitting any one of the six ops or ``capabilities`` breaks conformance (R1.1, R1.2)."""
    for missing in _PROTOCOL_MEMBERS:
        partial_cls = _make_partial_class(missing)
        instance = partial_cls()
        assert not isinstance(instance, Vector_Adapter), (
            f"a connector missing {missing!r} was wrongly treated as conforming"
        )


def test_empty_class_is_not_an_instance() -> None:
    """A class supplying none of the members is non-conforming (R1.2)."""

    class _NotAConnector:
        pass

    assert not isinstance(_NotAConnector(), Vector_Adapter)


# --------------------------------------------------------------------------- #
# A conforming operation returns an AdapterResult (uniform result shape).
# --------------------------------------------------------------------------- #


def test_every_operation_returns_an_adapter_result() -> None:
    """Each of the six conforming ops returns an :class:`AdapterResult` (R1.4, R1.5)."""
    # One scripted OK result per operation, replayed in script order.
    script = tuple(
        AdapterResult(code=posture.VECTOR_OK, records=()) for _ in _SIX_OPERATIONS
    )
    stub = _stub(script)

    calls = (
        stub.query(QueryRequest("org-a", "pinecone", "c1", (0.1, 0.2), top_k=3)),
        stub.upsert(UpsertRequest("org-a", "pinecone", "c1", records=())),
        stub.delete(DeleteRequest("org-a", "pinecone", "c1", ids=("r1",))),
        stub.list_collections(ListRequest("org-a", "pinecone")),
        stub.create_collection(
            CreateRequest("org-a", "pinecone", "c1", dimension=2, metric="cosine")
        ),
        stub.health(HealthRequest("org-a", "pinecone")),
    )

    async def _drive() -> list[AdapterResult]:
        return [await call for call in calls]

    results = asyncio.run(_drive())
    assert len(results) == len(_SIX_OPERATIONS)
    for result in results:
        assert isinstance(result, AdapterResult)
        assert result.ok is True


def test_capabilities_returns_a_capabilities_value() -> None:
    """The ``capabilities`` accessor returns the injected :class:`Capabilities` (R1.3)."""
    stub = _stub()
    caps = stub.capabilities()
    assert isinstance(caps, Capabilities)
    assert caps == _capabilities()
