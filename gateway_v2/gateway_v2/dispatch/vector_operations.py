"""GW30 capability-flag enforcement on the operation path (R3, R5).

This module owns the pure, stateless guard that enforces a connector's declared
:class:`~gateway_v2.dispatch.vector_adapter.Capabilities` against an incoming
request **before** the operation runs. It is deliberately a separate module from
``dispatch/vector_adapter.py`` (the protocol + value types) and
``dispatch/vector_credentials.py`` (the credential seam + scope guard) so the
three concerns do not collide.

What this module defines (task 9.1 scope — capability enforcement ONLY):

* :func:`exercised_capabilities` — a pure helper that derives, from a request's
  own fields (plus an explicit ``hybrid`` signal), the set of optional
  capabilities the request exercises: ``"filters"`` when a
  :class:`~gateway_v2.dispatch.vector_adapter.QueryRequest` carries a non-``None``
  ``filter``; ``"namespaces"`` when a ``query`` / ``upsert`` / ``delete`` request
  carries a non-``None`` ``namespace``; ``"hybrid_search"`` when the caller marks
  the request as a hybrid (dense + sparse/keyword) search.
* :func:`enforce_capabilities` — given an injected ``adapter`` and a request,
  returns an :class:`~gateway_v2.dispatch.vector_adapter.AdapterResult` carrying
  ``posture.UNSUPPORTED_OPERATION`` when the request exercises a capability the
  connector declares ``False``, or ``None`` when every exercised capability is
  supported (the caller then proceeds to run the operation).

**The invariant this guard signs for (R3.5, R5.2–R5.5).** When a request
exercises a capability a connector declares ``False`` — a ``filter`` against
``capabilities().filters is False``, a ``namespace`` against
``capabilities().namespaces is False``, or a hybrid search against
``capabilities().hybrid_search is False`` — the path returns the declared
``unsupported_operation`` code on a frozen :class:`AdapterResult`. It does **not**
silently ignore the option and it does **not** return any results: the returned
result leaves ``records`` at its ``None`` default (**absent**, never an empty
success), so a caller distinguishes an unsupported-capability outcome from a
clean zero-record search **by ``code`` alone** (R5.5). Where every exercised
capability is supported, the guard returns ``None`` and the operation proceeds
(R3.6).

**Mapping (design.md Components §Capabilities, Error Handling table).**

====================  ===========================  ========================
request field         capability flag              unsupported ->
====================  ===========================  ========================
``QueryRequest.filter`` (not ``None``)  ``filters``       ``unsupported_operation``
``*.namespace`` (not ``None``)          ``namespaces``    ``unsupported_operation``
hybrid-search request                   ``hybrid_search`` ``unsupported_operation``
====================  ===========================  ========================

Only ``QueryRequest`` carries a ``filter``; ``QueryRequest`` / ``UpsertRequest``
/ ``DeleteRequest`` carry an optional ``namespace``; ``ListRequest`` /
``CreateRequest`` / ``HealthRequest`` exercise none of the three optional
capabilities. The GW30 request value types carry no hybrid-search field, so a
hybrid request is signalled explicitly by the caller (GW31, which owns the
request shaping) through the ``hybrid`` parameter — keeping this guard honest
rather than guessing a hybrid intent from an unrelated field.

**Purity / layering.** Every function here is pure and stateless: it reads the
request fields and the injected ``adapter.capabilities()`` and returns a value,
with no clock, no randomness, no I/O, and no mutation. There is no module-level
mutable table and no capacity literal. The module imports only the sibling
``dispatch`` value types and the ``domain`` posture codes — the same single
downward ``domain`` edge the rest of ``dispatch`` uses — so it adds no new layer
edge.
"""

from __future__ import annotations

from gateway_v2.dispatch.vector_adapter import (
    AdapterResult,
    DeleteRequest,
    QueryRequest,
    UpsertRequest,
    Vector_Adapter,
)
from gateway_v2.domain import posture

__all__: tuple[str, ...] = (
    "enforce_capabilities",
    "exercised_capabilities",
)


def exercised_capabilities(
    req: object,
    *,
    hybrid: bool = False,
) -> frozenset[str]:
    """Return the optional capabilities ``req`` exercises, from its own fields.

    Pure and deterministic. The returned set contains:

    * ``"filters"`` iff ``req`` is a :class:`QueryRequest` whose ``filter`` is not
      ``None`` (R3.2 / R3.5 — a filter on ``query`` exercises the ``filters``
      capability);
    * ``"namespaces"`` iff ``req`` is a ``query`` / ``upsert`` / ``delete``
      request whose ``namespace`` is not ``None`` (R3.3 — a namespace exercises
      the ``namespaces`` capability);
    * ``"hybrid_search"`` iff the caller marks the request as a hybrid (dense +
      sparse/keyword) search via ``hybrid=True`` (R3.4 — the request value types
      carry no hybrid field, so the caller signals the intent explicitly).

    A request exercising none of the three (a plain ``query`` with no filter and
    no namespace, a ``list_collections`` / ``create_collection`` / ``health``
    request) yields the empty set, so :func:`enforce_capabilities` returns
    ``None`` and the operation proceeds unconditionally.
    """
    exercised: set[str] = set()

    # filters — only a QueryRequest carries a filter; a non-None filter exercises it.
    if isinstance(req, QueryRequest) and req.filter is not None:
        exercised.add("filters")

    # namespaces — query / upsert / delete each carry an optional namespace.
    if (
        isinstance(req, (QueryRequest, UpsertRequest, DeleteRequest))
        and req.namespace is not None
    ):
        exercised.add("namespaces")

    # hybrid_search — no request field; signalled explicitly by the caller.
    if hybrid:
        exercised.add("hybrid_search")

    return frozenset(exercised)


def enforce_capabilities(
    adapter: Vector_Adapter,
    req: object,
    *,
    hybrid: bool = False,
) -> AdapterResult | None:
    """Enforce ``adapter``'s declared capabilities against ``req`` (R3.5, R5.2–R5.5).

    Reads ``adapter.capabilities()`` once and compares it against the capabilities
    ``req`` exercises (see :func:`exercised_capabilities`). Returns:

    * an :class:`AdapterResult` carrying ``posture.UNSUPPORTED_OPERATION`` — with
      ``records`` left at its ``None`` default (**absent**, never an empty
      success) and no success indicator set — when ``req`` exercises a capability
      the connector declares ``False``. The filter / namespace / hybrid option is
      **not** silently ignored and **no** results are returned (R3.5): the caller
      MUST NOT run the operation and returns this result as-is.
    * ``None`` when every capability ``req`` exercises is declared ``True`` (or it
      exercises none) — the caller then proceeds to run the operation (R3.6).

    Pure and stateless: no clock, no randomness, no I/O, no mutation. The checks
    are ordered (filters, then namespaces, then hybrid_search) for a deterministic
    outcome, and the first unsupported capability short-circuits — the uniform
    ``unsupported_operation`` code is identical whichever capability is
    unsupported, so the order affects only which capability a diagnostic would
    name, never the returned ``code`` (R5.1 — one distinct code, distinguishable
    from ``VECTOR_OK`` and every other error by the vocabulary alone).
    """
    caps = adapter.capabilities()
    exercised = exercised_capabilities(req, hybrid=hybrid)

    # Ordered, short-circuit on the first unsupported exercised capability. The
    # returned result carries only the code; ``records`` stays ``None`` (absent)
    # so the outcome is never an empty success (R5.4, R5.5).
    if "filters" in exercised and not caps.filters:
        return AdapterResult(code=posture.UNSUPPORTED_OPERATION)
    if "namespaces" in exercised and not caps.namespaces:
        return AdapterResult(code=posture.UNSUPPORTED_OPERATION)
    if "hybrid_search" in exercised and not caps.hybrid_search:
        return AdapterResult(code=posture.UNSUPPORTED_OPERATION)

    # Every exercised capability is supported (or none was exercised): proceed.
    return None
