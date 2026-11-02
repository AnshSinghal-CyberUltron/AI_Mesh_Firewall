"""GW30 bounded connector-operation path — fail-closed over the Resource_Contract (task 12.1).

This module owns :func:`run_bounded_operation`, the single wrapper every connector
operation runs through before its :class:`~gateway_v2.dispatch.vector_adapter.AdapterResult`
reaches a caller. It binds the three GW30 bounds from the ``ResourceContract`` — the
per-operation timeout and the result-size ceilings (count + bytes) — and maps every
failure mode to exactly one declared ``Vector_Posture_Code`` on a frozen
:class:`AdapterResult`. It NEVER raises to the caller and NEVER returns an empty success
in place of a failure: a provider-unreachable outcome is a declared code with
``records is None`` (absent), never a clean empty search (R6.2, R6.3, R6.6).

What this module defines (task 12.1 scope — the bounded operation path ONLY):

* :data:`VectorBounds` — the structural protocol of the three ``ResourceContract``
  accessors this path reads (``vector_operation_timeout_s`` /
  ``vector_max_result_count`` / ``vector_max_result_bytes``). The real carrier is
  ``runtime/resources.py``'s ``VectorBoundsMixin`` (mixed into ``ResourceContract``),
  satisfied structurally — so this module holds NO capacity literal and reads every
  bound only through the injected contract (R11.1, R11.2, R11.4).
* :class:`VectorTransportError` — the declared transport-failure signal a connector's
  injected transport raises for a connection refusal / connect-or-read failure /
  transport error / upstream 5xx. The bounded path maps it (and the standard-library
  ``OSError`` / ``asyncio.TimeoutError`` families) to ``PROVIDER_UNREACHABLE``.
* :func:`result_size_within_bounds` — the pure mid-op size guard: an
  :class:`AdapterResult` whose record count exceeds ``vector_max_result_count()`` or
  whose measured record-payload bytes exceed ``vector_max_result_bytes()`` is over a
  bound.
* :func:`run_bounded_operation` — the async wrapper: bind the bounds (fail closed on an
  unset/unserviceable bound), run the connector operation under the derived timeout,
  map transport failures and the timeout to ``PROVIDER_UNREACHABLE``, enforce the
  result-size ceilings on a success (discarding the partial result on a breach), and
  return a frozen :class:`AdapterResult` on every path.

**The invariants this path signs for.**

* **Timeout, fail-closed (R6.4, R6.5, R11.5).** The operation runs under
  ``asyncio.wait_for(op(), timeout=vector_operation_timeout_s())``. An operation that
  does not complete within the derived bound is cancelled (``wait_for`` cancels the
  awaited coroutine), its resources released, and the path returns
  ``PROVIDER_UNREACHABLE`` — never an unbounded wait, never a partial/empty success.
* **Transport failure, fail-closed (R6.2, R6.3).** A connection refusal, connect-or-read
  failure, transport error, or upstream 5xx raised by the operation (as
  :class:`VectorTransportError`, ``OSError``, or ``asyncio.TimeoutError``) maps to
  ``PROVIDER_UNREACHABLE`` with ``records is None`` — never surfaced as an exception and
  never an empty success.
* **Size bound, discard the partial (R11.3).** A success whose record count or measured
  payload bytes exceeds a ``ResourceContract`` ceiling aborts before the result is
  returned, DISCARDS the accumulated records (the returned result carries no records —
  ``records is None``), and returns ``VECTOR_CAPACITY_EXCEEDED`` — never a truncated or
  empty success labelled OK.
* **Unset bound, refuse to start (R6.5, R11.6).** A required bound unset at bind time
  (``CapacityUnset`` from the contract) returns ``VECTOR_CAPACITY_UNSET`` and the
  operation is never started — no default literal, no unbounded wait. A derived bound
  below the serviceable minimum (``CapacityUnavailable``) returns
  ``PROVIDER_UNREACHABLE`` (refuse to start rather than issue an unbounded or
  unserviceable operation), matching the design's "no timeout supplied → refuse to
  start, return ``provider_unreachable``" row.

**Purity / layering.** ``dispatch`` sits ABOVE ``runtime`` in the import-linter layer
order, so reading the ``ResourceContract`` bounds is an allowed downward edge (the same
edge ``dispatch/mcp_broker.py`` takes for ``McpBounds``); the bounds are nonetheless
read through the structural :data:`VectorBounds` protocol and the contract is INJECTED,
so this path is pure with respect to its injected dependencies — no module-level mutable
table, no clock read from the wall clock (the only clock is ``asyncio``'s own timeout
mechanism, the house idiom for a bounded await), and no capacity literal. Every bound
comes from the injected contract; every failure is a declared posture code on a frozen
:class:`AdapterResult`.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Protocol, runtime_checkable

from gateway_v2.dispatch.vector_adapter import AdapterResult, VectorRecord
from gateway_v2.domain import posture
from gateway_v2.runtime.errors import CapacityUnavailable, CapacityUnset

__all__ = (
    "VectorBounds",
    "VectorTransportError",
    "result_size_within_bounds",
    "run_bounded_operation",
)


# --------------------------------------------------------------------------- #
# VectorBounds — the structural shape of the three ResourceContract accessors.
# --------------------------------------------------------------------------- #


@runtime_checkable
class VectorBounds(Protocol):
    """The three ``ResourceContract`` accessors the bounded path reads (R11.1, R11.2).

    The real carrier is ``runtime/resources.py``'s ``VectorBoundsMixin`` (mixed into
    ``ResourceContract``); it is read here through this structural protocol so the
    bounded path holds NO capacity literal and derives every bound ONLY from the
    injected contract. Each accessor fails closed exactly as the contract declares:
    ``vector_operation_timeout_s`` raises ``CapacityUnset`` when its SLO signal is unset
    and ``CapacityUnavailable`` on a non-positive bound; the two result-size accessors
    raise ``CapacityUnavailable`` below one.
    """

    def vector_operation_timeout_s(self) -> float: ...

    def vector_max_result_count(self) -> int: ...

    def vector_max_result_bytes(self) -> int: ...


# --------------------------------------------------------------------------- #
# VectorTransportError — the declared transport-failure signal.
# --------------------------------------------------------------------------- #


class VectorTransportError(Exception):
    """A connector transport failure: refusal / connect-or-read / transport / upstream 5xx.

    A connector's injected transport raises this (or a standard-library ``OSError`` /
    ``asyncio.TimeoutError``) when the backing store refuses the connection, the
    connect-or-read fails, the transport errors, or the store answers an upstream 5xx.
    :func:`run_bounded_operation` catches it and returns ``PROVIDER_UNREACHABLE`` — a
    transport failure is NEVER an empty success and NEVER an exception reaching the
    caller (R6.2, R6.3).
    """


# --------------------------------------------------------------------------- #
# result_size_within_bounds — the pure mid-op size guard (R11.3).
# --------------------------------------------------------------------------- #


def _record_payload_bytes(record: VectorRecord) -> int:
    """Return an upper-bound byte measure of one ``VectorRecord`` payload.

    Pure and deterministic: the id's UTF-8 length, eight bytes per embedding float, and
    the UTF-8 length of each metadata key plus a fixed scalar-value width. It never
    DECODES or materialises an external payload — it measures the record already present
    in an :class:`AdapterResult` — so it has no I/O and no side effect, and it is a
    conservative (never under-counting) measure so a result that would exceed the byte
    ceiling is caught before it is returned.
    """
    total = len(record.id.encode("utf-8"))
    total += 8 * len(record.vector)
    for key, value in record.metadata.items():
        total += len(key.encode("utf-8"))
        total += len(str(value).encode("utf-8"))
    return total


def result_size_within_bounds(
    records: tuple[VectorRecord, ...],
    *,
    max_count: int,
    max_bytes: int,
) -> bool:
    """Return ``True`` iff ``records`` fits both the count and byte ceilings (R11.3).

    Pure and stateless. A record count above ``max_count`` or a summed record-payload
    byte measure above ``max_bytes`` is over a bound (``False``). The byte sum is
    accumulated incrementally and short-circuits the instant it crosses ``max_bytes``,
    so an over-large result is detected without measuring every remaining record. Both
    ceilings come from the injected contract; this guard holds no literal of its own.
    """
    if len(records) > max_count:
        return False
    running = 0
    for record in records:
        running += _record_payload_bytes(record)
        if running > max_bytes:
            return False
    return True


# --------------------------------------------------------------------------- #
# run_bounded_operation — the async bounded wrapper (R6.2–R6.5, R11.3, R11.5, R11.6).
# --------------------------------------------------------------------------- #


async def run_bounded_operation(
    operation: Callable[[], Awaitable[AdapterResult]],
    contract: VectorBounds,
) -> AdapterResult:
    """Run ``operation`` under the ``ResourceContract`` bounds, fail-closed (R6, R11).

    ``operation`` is a zero-arg coroutine factory producing the connector's
    :class:`AdapterResult` over its injected transport; ``contract`` is the injected
    :class:`VectorBounds` (a ``ResourceContract``). The wrapper, in order:

    1. **Binds the per-operation timeout** from ``contract.vector_operation_timeout_s()``.
       A ``CapacityUnset`` (no SLO signal supplied) returns ``VECTOR_CAPACITY_UNSET`` and
       the operation is NEVER started — no default literal, no unbounded wait (R6.5,
       R11.6). A ``CapacityUnavailable`` (a non-positive/unserviceable derived bound)
       returns ``PROVIDER_UNREACHABLE`` — refuse to start rather than issue an
       unserviceable operation (the design's "no usable timeout → provider_unreachable"
       row).
    2. **Binds the result-size ceilings** from ``vector_max_result_count()`` /
       ``vector_max_result_bytes()``. A ``CapacityUnset`` returns
       ``VECTOR_CAPACITY_UNSET`` (refuse to start); a ``CapacityUnavailable`` returns
       ``PROVIDER_UNREACHABLE``.
    3. **Runs the operation under the timeout** via ``asyncio.wait_for``. A
       :class:`asyncio.TimeoutError` (the op ran past the derived bound — ``wait_for``
       has already cancelled the awaited coroutine and released it) maps to
       ``PROVIDER_UNREACHABLE`` (R6.4, R11.5). A :class:`VectorTransportError` or an
       ``OSError`` (connection refusal / connect-or-read failure / transport error /
       upstream 5xx) maps to ``PROVIDER_UNREACHABLE`` (R6.2, R6.3). In every case
       ``records is None`` — a transport failure is distinguishable by ``code`` ALONE
       from a clean zero-record success (R6.6).
    4. **Enforces the size ceilings on a success.** When the operation returns an
       ``ok`` result carrying records, the record count and measured payload bytes are
       checked against the bound; a breach ABORTS before the result is returned,
       DISCARDS the accumulated records, and returns ``VECTOR_CAPACITY_EXCEEDED`` with
       ``records is None`` — never a truncated or empty success labelled OK (R11.3). A
       success within the bounds (including a legitimate zero-record success,
       ``records == ()``) is returned unchanged; a non-``ok`` result the operation
       itself produced (e.g. a connector-declared ``unsupported_operation``) is also
       returned unchanged — the size guard only inspects a success carrying records.

    The wrapper NEVER raises to the caller: every path returns a frozen
    :class:`AdapterResult` carrying a declared ``Vector_Posture_Code``.
    """
    # 1 + 2. Bind every bound BEFORE starting the operation. A required bound unset at
    # bind time refuses to start with VECTOR_CAPACITY_UNSET (no default literal, no
    # unbounded wait); an unserviceable derived bound refuses to start with
    # PROVIDER_UNREACHABLE (never an unbounded or unserviceable operation).
    try:
        timeout_s = contract.vector_operation_timeout_s()
        max_count = contract.vector_max_result_count()
        max_bytes = contract.vector_max_result_bytes()
    except CapacityUnset:
        return AdapterResult(code=posture.VECTOR_CAPACITY_UNSET)
    except CapacityUnavailable:
        return AdapterResult(code=posture.PROVIDER_UNREACHABLE)

    # 3. Run the operation under the derived timeout. wait_for cancels the awaited
    # coroutine on expiry (resources released) and raises asyncio.TimeoutError. A
    # transport failure (declared VectorTransportError or a stdlib OSError, which
    # covers ConnectionRefusedError / TimeoutError / other socket errors) is caught
    # alongside it. Every caught failure maps to PROVIDER_UNREACHABLE (records absent),
    # never an exception to the caller and never an empty success.
    try:
        result = await asyncio.wait_for(operation(), timeout=timeout_s)
    except (TimeoutError, VectorTransportError, OSError):
        return AdapterResult(code=posture.PROVIDER_UNREACHABLE)

    # 4. Enforce the size ceilings on a success carrying records. A breach aborts and
    # DISCARDS the partial result (records left absent), returning VECTOR_CAPACITY_EXCEEDED
    # — never a truncated/empty success. A success within the bounds (including a
    # zero-record success) and any non-ok result the operation produced are returned
    # unchanged.
    if result.ok and result.records is not None:
        if not result_size_within_bounds(
            result.records,
            max_count=max_count,
            max_bytes=max_bytes,
        ):
            return AdapterResult(code=posture.VECTOR_CAPACITY_EXCEEDED)
    return result
