"""LGW30 unsupported-operation-not-empty property test (GW30, task 9.2).

# Feature: vector-store-adapter-gw30, Property 3: Unsupported operation is not an empty success
# Validates: Requirements 3.2, 3.3, 3.4, 5.1, 5.2, 5.3

Property 3 (design §Correctness Properties → Property 3): *for all* capability
flag combinations and *for all* requests, the capability guard
``enforce_capabilities(adapter, req, hybrid=...)`` returns:

* an :class:`AdapterResult` carrying ``posture.UNSUPPORTED_OPERATION`` with
  ``records is None`` (ABSENT, never an empty ``()`` success) and ``ok is False``
  whenever the request exercises a capability the connector declares ``False`` —
  a ``filter`` against ``capabilities().filters is False`` (R3.2), a
  ``namespace`` against ``capabilities().namespaces is False`` (R3.3), or a
  hybrid search against ``capabilities().hybrid_search is False`` (R3.4);
* ``None`` (proceed — the caller runs the operation) whenever every exercised
  capability is supported, OR the request exercises no optional capability.

The heart of the property (R5.1 / R5.2 / R5.3 — the absent-vs-empty distinction):
the unsupported result is **never** equal to a clean zero-record success
(``AdapterResult(code=VECTOR_OK, records=())``). An unsupported-capability
outcome has ``records is None`` (absent — "no search happened"); a legitimate
empty search has ``records == ()`` (present-but-empty — "the search ran and
matched nothing"). A caller distinguishes them by ``code`` alone.

House idiom: a single seeded ``random.Random`` drives ``_ITERATIONS`` trials;
each trial draws a :class:`Capabilities` from all 8 flag combinations, a request
of a random type (query with/without filter + with/without namespace;
upsert/delete with/without namespace; list/create/health), and the ``hybrid``
flag, then checks the guard outcome against an independently-computed oracle. No
``hypothesis``. The guard is pure and synchronous so no ``asyncio.run`` is
needed; the injected ``Stub_Vector_Connector`` only supplies ``capabilities()``.

_Design: Testing Strategy (property tests); Correctness Properties
(unsupported-operation-not-empty); Components §capability enforcement._
_Requirements: 3.2, 3.3, 3.4, 5.1, 5.2, 5.3._
"""

from __future__ import annotations

import itertools
import random
from types import MappingProxyType

from gateway_v2.dispatch.vector_adapter import (
    AdapterResult,
    Capabilities,
    CreateRequest,
    DeleteRequest,
    HealthRequest,
    ListRequest,
    QueryRequest,
    UpsertRequest,
)
from gateway_v2.dispatch.vector_connectors import Stub_Vector_Connector
from gateway_v2.dispatch.vector_operations import enforce_capabilities
from gateway_v2.domain import posture

_ITERATIONS = 10_000

# All 8 combinations of the three defined capability flags.
_FLAG_COMBOS = tuple(itertools.product((False, True), repeat=3))

# The request kinds the generator picks across. query/upsert/delete carry an
# optional namespace; only query carries a filter; list/create/health exercise
# no optional capability at all.
_REQUEST_KINDS = (
    "query",
    "upsert",
    "delete",
    "list_collections",
    "create_collection",
    "health",
)

# A fixed, present-but-empty success to compare the unsupported result against —
# this is the value the property forbids the guard from ever producing on the
# unsupported path (the absent-vs-empty distinction, R5.1/R5.2/R5.3).
_EMPTY_SUCCESS = AdapterResult(code=posture.VECTOR_OK, records=())


def _build_adapter(caps: Capabilities, rng: random.Random) -> Stub_Vector_Connector:
    """A socket-free stub whose ``capabilities()`` returns ``caps`` verbatim.

    The guard reads ONLY ``capabilities()``; the empty script is never consumed
    because ``enforce_capabilities`` never runs an operation.
    """
    return Stub_Vector_Connector(script=(), capabilities=caps, rng=rng)


def _random_request(
    kind: str, rng: random.Random
) -> tuple[object, bool, bool, bool]:
    """Build a request of ``kind`` and report which capabilities it exercises.

    Returns ``(req, uses_filter, uses_namespace, uses_hybrid)`` where the three
    booleans form the oracle's view of what the request exercises — computed from
    the request's own fields (plus a randomly-chosen ``hybrid`` flag), entirely
    independently of :func:`enforce_capabilities`.

    ``hybrid`` is only ever meaningful on a ``query`` (the only operation a hybrid
    search applies to); for other kinds it stays ``False`` so the oracle matches
    a realistic caller.
    """
    namespace = f"ns-{rng.randint(0, 9)}" if rng.random() < 0.5 else None

    if kind == "query":
        use_filter = rng.random() < 0.5
        filt = (
            MappingProxyType({"field": rng.randint(0, 9)}) if use_filter else None
        )
        hybrid = rng.random() < 0.5
        req: object = QueryRequest(
            org_id="org",
            store_id="pinecone",
            collection="c",
            vector=(0.0,),
            top_k=1,
            filter=filt,
            namespace=namespace,
        )
        return req, use_filter, namespace is not None, hybrid

    if kind == "upsert":
        return (
            UpsertRequest(
                org_id="org",
                store_id="pinecone",
                collection="c",
                records=(),
                namespace=namespace,
            ),
            False,
            namespace is not None,
            False,
        )

    if kind == "delete":
        return (
            DeleteRequest(
                org_id="org",
                store_id="pinecone",
                collection="c",
                ids=(),
                namespace=namespace,
            ),
            False,
            namespace is not None,
            False,
        )

    if kind == "list_collections":
        return ListRequest(org_id="org", store_id="pinecone"), False, False, False

    if kind == "create_collection":
        return (
            CreateRequest(
                org_id="org",
                store_id="pinecone",
                collection="c",
                dimension=1,
                metric="cosine",
            ),
            False,
            False,
            False,
        )

    # health
    return HealthRequest(org_id="org", store_id="pinecone"), False, False, False


def test_enforce_capabilities_unsupported_is_absent_never_empty_success() -> None:
    """Property 3: unsupported → UNSUPPORTED_OPERATION, ``records is None``, never ``()``.

    For every drawn (capabilities, request, hybrid) the guard's outcome must match
    an independently-computed oracle:

    * if the request exercises any capability the connector declares ``False`` →
      an :class:`AdapterResult` with ``code == UNSUPPORTED_OPERATION``,
      ``records is None`` (ABSENT), ``ok is False``, and NOT equal to a
      zero-record ``VECTOR_OK`` success (R3.2/R3.3/R3.4, R5.1/R5.2/R5.3);
    * otherwise → ``None`` (the caller proceeds).

    _Validates: Requirements 3.2, 3.3, 3.4, 5.1, 5.2, 5.3._
    """
    rng = random.Random(0xCA9AB1E)

    saw_unsupported = False
    saw_proceed = False

    for _ in range(_ITERATIONS):
        filters, namespaces, hybrid_search = rng.choice(_FLAG_COMBOS)
        caps = Capabilities(
            filters=filters,
            namespaces=namespaces,
            hybrid_search=hybrid_search,
        )
        adapter = _build_adapter(caps, rng)

        kind = rng.choice(_REQUEST_KINDS)
        req, uses_filter, uses_namespace, uses_hybrid = _random_request(kind, rng)

        # Oracle: the request is unsupported iff it exercises any capability the
        # connector declares False.
        expected_unsupported = (
            (uses_filter and not filters)
            or (uses_namespace and not namespaces)
            or (uses_hybrid and not hybrid_search)
        )

        result = enforce_capabilities(adapter, req, hybrid=uses_hybrid)

        if expected_unsupported:
            saw_unsupported = True
            assert result is not None, (
                f"expected an unsupported result, got None: caps={caps!r} "
                f"kind={kind} filter={uses_filter} ns={uses_namespace} "
                f"hybrid={uses_hybrid}"
            )
            # The declared code, and nothing else.
            assert result.code == posture.UNSUPPORTED_OPERATION
            # ABSENT, never an empty success — the whole point of the property.
            assert result.records is None
            assert result.ok is False
            # It is NOT a clean zero-record VECTOR_OK success: absent (None),
            # never the present-but-empty tuple () a legitimate search returns.
            assert result != _EMPTY_SUCCESS
            assert result.records != ()
            # No other success-only field leaked onto the failure value.
            assert result.count is None
            assert result.collections is None
            assert result.status is None
        else:
            saw_proceed = True
            assert result is None, (
                f"expected proceed (None), got {result!r}: caps={caps!r} "
                f"kind={kind} filter={uses_filter} ns={uses_namespace} "
                f"hybrid={uses_hybrid}"
            )

    # The generator must have exercised BOTH arms of the property — otherwise a
    # degenerate run could pass vacuously.
    assert saw_unsupported, "no unsupported case was generated"
    assert saw_proceed, "no proceed case was generated"


def test_empty_success_is_distinct_from_unsupported_absent() -> None:
    """The absent-vs-empty distinction is a real inequality (R5.1/R5.2/R5.3).

    A zero-record ``VECTOR_OK`` success (``records == ()``, present-but-empty) is
    never equal to an unsupported result (``records is None``, absent), and only
    the former is ``ok``. This pins the distinction the property above relies on.

    _Validates: Requirements 5.1, 5.2, 5.3._
    """
    empty_success = AdapterResult(code=posture.VECTOR_OK, records=())
    unsupported = AdapterResult(code=posture.UNSUPPORTED_OPERATION)

    assert empty_success.records == ()
    assert empty_success.ok is True

    assert unsupported.records is None
    assert unsupported.ok is False

    assert empty_success != unsupported
    assert empty_success.code != unsupported.code
