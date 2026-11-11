"""LGW30 provider-unreachable-not-empty property test (GW30, task 12.2).

# Feature: vector-store-adapter-gw30, Property 5: Provider-unreachable is not an empty success
# Validates: Requirements 6.2, 6.3, 6.4, 6.6

Property 5 (design §Correctness Properties → Property 5): *for all* failure modes
of the bounded operation path
:func:`~gateway_v2.dispatch.vector_bounded_op.run_bounded_operation`, the returned
:class:`~gateway_v2.dispatch.vector_adapter.AdapterResult` NEVER presents a failure
as an empty success. On every failure ``records is None`` (ABSENT) and ``ok is
False`` — never ``records == ()`` (the present-but-empty value a clean zero-record
search returns). Concretely:

* a transport failure (:class:`VectorTransportError` or an ``OSError``) →
  ``PROVIDER_UNREACHABLE`` with ``records is None`` (R6.2, R6.3);
* a timeout (the operation exceeds ``vector_operation_timeout_s()``) →
  ``PROVIDER_UNREACHABLE`` with ``records is None`` (R6.4);
* a result over the count or byte ceiling → ``VECTOR_CAPACITY_EXCEEDED`` with
  ``records is None`` — the partial result is discarded (R11.3);
* a bound unset at bind time (``CapacityUnset``) → ``VECTOR_CAPACITY_UNSET``
  (refuse to start); an unserviceable derived bound (``CapacityUnavailable``) →
  ``PROVIDER_UNREACHABLE`` (R6.5, R11.6);
* a legitimate within-bounds success (including a genuine zero-record success,
  ``records == ()``) is returned UNCHANGED with ``ok is True``, and a non-``ok``
  result the operation itself produced is passed through unchanged.

The heart of the property (R6.6 — the absent-vs-empty distinction): a failure
result is NEVER byte-equal to a clean zero-record success
(``AdapterResult(code=VECTOR_OK, records=())``). The two are distinguishable by
``code`` alone, and the wrapper NEVER raises to the caller (no unhandled exception
escapes :func:`run_bounded_operation`).

House idiom: a single seeded ``random.Random`` drives ``_ITERATIONS`` trials; each
trial randomises (a) which failure/success mode fires, (b) whether the injected
:class:`VectorBounds` is a real :class:`ResourceContract` (built via
``from_signals``) or a stub honouring the protocol (returning finite bounds or
raising ``CapacityUnset`` / ``CapacityUnavailable``), (c) the operation's returned
result (``ok`` with varying record counts / payload sizes; a non-``ok`` result
passed through), and (d) timeout-vs-transport-vs-size-breach. Each async trial is
driven through ``asyncio.run``. No ``hypothesis``.

_Design: Testing Strategy (property tests); Correctness Properties
(provider-unreachable-not-empty); Components §`dispatch/vector_connectors.py` +
§`runtime/resources.py`, Error Handling table._
_Requirements: 6.2, 6.3, 6.4, 6.6._
"""

from __future__ import annotations

import asyncio
import random
from types import MappingProxyType

from gateway_v2.dispatch.vector_adapter import AdapterResult, VectorRecord
from gateway_v2.dispatch.vector_bounded_op import (
    VectorBounds,
    VectorTransportError,
    run_bounded_operation,
)
from gateway_v2.domain import posture
from gateway_v2.runtime.errors import CapacityUnavailable, CapacityUnset
from gateway_v2.runtime.kinds import HardwareSignals
from gateway_v2.runtime.resources import ResourceContract, from_signals

_ITERATIONS = 10_000

# A fixed, present-but-empty clean success. The property forbids any failure
# result from ever being byte-equal to this value (the absent-vs-empty line).
_EMPTY_SUCCESS = AdapterResult(code=posture.VECTOR_OK, records=())


# --------------------------------------------------------------------------- #
# Injected VectorBounds — the simplest deterministic way to drive each branch.
# --------------------------------------------------------------------------- #


class _StubBounds:
    """A ``VectorBounds`` honouring the protocol: finite bounds or a fail-closed raise.

    ``timeout_mode`` selects what ``vector_operation_timeout_s`` does — return a
    finite positive value, raise ``CapacityUnset`` (no SLO signal), or raise
    ``CapacityUnavailable`` (unserviceable derived bound). The two size accessors
    return the injected finite ceilings. This is the minimal deterministic lever
    for the unset / unavailable branches, which a real ``ResourceContract`` only
    reaches through a degenerate build.
    """

    __slots__ = ("_timeout_s", "_timeout_mode", "_max_count", "_max_bytes")

    def __init__(
        self,
        *,
        timeout_s: float,
        timeout_mode: str,
        max_count: int,
        max_bytes: int,
    ) -> None:
        self._timeout_s = timeout_s
        self._timeout_mode = timeout_mode
        self._max_count = max_count
        self._max_bytes = max_bytes

    def vector_operation_timeout_s(self) -> float:
        if self._timeout_mode == "unset":
            raise CapacityUnset("vector operation timeout signal unset")
        if self._timeout_mode == "unavailable":
            raise CapacityUnavailable("vector operation timeout below minimum")
        return self._timeout_s

    def vector_max_result_count(self) -> int:
        return self._max_count

    def vector_max_result_bytes(self) -> int:
        return self._max_bytes


def _real_contract() -> ResourceContract:
    """A serviceable real ``ResourceContract`` via ``from_signals`` (as the sibling tests do).

    Anchors one arm of the property in the real carrier (``VectorBoundsMixin``
    mixed into ``ResourceContract``), proving the bounded path reads the shipped
    accessors structurally, not just a stub.
    """
    per_worker_rss = 100 * 1024 * 1024
    signals = HardwareSignals(
        cpu_quota=4.0,
        memory_limit=per_worker_rss * 32,
        fd_limit=4096,
        cpu_source="test",
        mem_source="test",
        fd_source="test",
    )
    return from_signals(
        signals,
        target_p99_ms=20.0,
        utilization_cap=0.75,
        per_worker_rss=per_worker_rss,
    )


# --------------------------------------------------------------------------- #
# Operation factories — zero-arg coroutine factories per the run_bounded_operation
# contract.
# --------------------------------------------------------------------------- #


def _record(i: int, *, metadata_width: int = 0) -> VectorRecord:
    """A deterministic record; ``metadata_width`` inflates its payload bytes."""
    meta = {f"k{j}": "v" * 8 for j in range(metadata_width)}
    return VectorRecord(
        id=f"id-{i}",
        vector=(float(i),),
        metadata=MappingProxyType(meta),
    )


def _make_operation(
    rng: random.Random, mode: str, *, raise_kind: str, record_count: int, metadata_width: int
):
    """Build the zero-arg coroutine factory the wrapper runs.

    * ``"transport"`` — raises ``VectorTransportError`` or an ``OSError`` family
      member (the raise the wrapper maps to ``PROVIDER_UNREACHABLE``).
    * ``"timeout"`` — awaits far longer than the derived timeout, so
      ``asyncio.wait_for`` cancels it and the wrapper maps the timeout to
      ``PROVIDER_UNREACHABLE``.
    * ``"success"`` — returns an ``ok`` result carrying ``record_count`` records
      of ``metadata_width`` metadata entries each (used for both within-bounds and
      over-bounds arms).
    * ``"passthrough"`` — returns a non-``ok`` result the operation itself
      produced (a connector-declared ``unsupported_operation``), which the wrapper
      returns unchanged.
    """

    async def _transport() -> AdapterResult:
        if raise_kind == "vector":
            raise VectorTransportError("backing store refused the connection")
        if raise_kind == "connrefused":
            raise ConnectionRefusedError("connection refused")
        if raise_kind == "oserror":
            raise OSError("transport error")
        raise TimeoutError("connect-or-read timeout")

    async def _timeout() -> AdapterResult:
        # Far beyond any derived bound; wait_for cancels this coroutine on expiry.
        await asyncio.sleep(3600.0)
        return _EMPTY_SUCCESS  # pragma: no cover - never reached

    async def _success() -> AdapterResult:
        records = tuple(_record(i, metadata_width=metadata_width) for i in range(record_count))
        return AdapterResult(code=posture.VECTOR_OK, records=records)

    async def _passthrough() -> AdapterResult:
        return AdapterResult(code=posture.UNSUPPORTED_OPERATION)

    if mode == "transport":
        return _transport
    if mode == "timeout":
        return _timeout
    if mode == "passthrough":
        return _passthrough
    return _success


def test_run_bounded_operation_failure_is_absent_never_empty_success() -> None:
    """Property 5: every failure → declared code, ``records is None``, never ``()``.

    For every drawn (bounds, operation, failure-mode) the wrapper's outcome must
    match an independently-computed oracle, and on every failure arm:

    * the declared code is the one the mode dictates (``PROVIDER_UNREACHABLE`` for
      transport/timeout/unserviceable-bound; ``VECTOR_CAPACITY_EXCEEDED`` for a
      size breach; ``VECTOR_CAPACITY_UNSET`` for an unset bound);
    * ``records is None`` (ABSENT) and ``ok is False``;
    * the result is NOT byte-equal to a clean zero-record ``VECTOR_OK`` success,
      and ``records != ()`` (R6.6);
    * no success-only field (``count`` / ``collections`` / ``status``) leaked;
    * no unhandled exception escaped the wrapper.

    A within-bounds success is returned UNCHANGED (``ok is True``, its records
    intact including a genuine ``()``); a non-``ok`` result the operation produced
    is passed through verbatim.

    _Validates: Requirements 6.2, 6.3, 6.4, 6.6._
    """
    rng = random.Random(0x5E1EC7ED)

    _MODES = (
        "transport",
        "timeout",
        "size_exceeded",
        "unset_bound",
        "unavailable_bound",
        "success_within",
        "success_empty",
        "passthrough",
    )
    seen: dict[str, bool] = {m: False for m in _MODES}

    for _ in range(_ITERATIONS):
        mode = rng.choice(_MODES)
        seen[mode] = True

        # Bounds: either the real contract or a stub. For the unset/unavailable
        # arms the stub is the deterministic lever; otherwise either may be used.
        use_real = rng.random() < 0.5 and mode not in ("unset_bound", "unavailable_bound")
        bounds: VectorBounds
        if use_real:
            bounds = _real_contract()
            max_count = bounds.vector_max_result_count()
            max_bytes = bounds.vector_max_result_bytes()
        else:
            timeout_mode = "ok"
            if mode == "unset_bound":
                timeout_mode = "unset"
            elif mode == "unavailable_bound":
                timeout_mode = "unavailable"
            # Keep the stub's finite bounds generous unless the size arm needs a
            # tight ceiling to force a breach.
            max_count = rng.randint(1, 10_000)
            max_bytes = rng.randint(1, 10_485_760)
            bounds = _StubBounds(
                timeout_s=0.05,
                timeout_mode=timeout_mode,
                max_count=max_count,
                max_bytes=max_bytes,
            )

        # Build the operation + its oracle.
        op_mode = "success"
        raise_kind = "vector"
        record_count = 0
        metadata_width = 0
        expected_code = posture.VECTOR_OK
        expect_failure = True
        expect_records_none = True

        if mode == "transport":
            op_mode = "transport"
            raise_kind = rng.choice(("vector", "connrefused", "oserror", "timeouterr"))
            expected_code = posture.PROVIDER_UNREACHABLE
        elif mode == "timeout":
            op_mode = "timeout"
            expected_code = posture.PROVIDER_UNREACHABLE
            # Keep the derived timeout small so the test does not stall. The real
            # contract's ~5 s bound would — so the timeout arm always uses a stub
            # with a tiny timeout.
            bounds = _StubBounds(
                timeout_s=0.01,
                timeout_mode="ok",
                max_count=max_count if not use_real else 10_000,
                max_bytes=max_bytes if not use_real else 10_485_760,
            )
        elif mode == "unset_bound":
            op_mode = "success"
            expected_code = posture.VECTOR_CAPACITY_UNSET
        elif mode == "unavailable_bound":
            op_mode = "success"
            expected_code = posture.PROVIDER_UNREACHABLE
        elif mode == "size_exceeded":
            op_mode = "success"
            expected_code = posture.VECTOR_CAPACITY_EXCEEDED
            # Force a breach deterministically against a tight stub ceiling: drive
            # the count over max_count OR the bytes over max_bytes.
            if rng.random() < 0.5:
                tight_count = rng.randint(1, 5)
                bounds = _StubBounds(
                    timeout_s=0.5,
                    timeout_mode="ok",
                    max_count=tight_count,
                    max_bytes=10_485_760,
                )
                record_count = tight_count + rng.randint(1, 5)
                metadata_width = 0
            else:
                bounds = _StubBounds(
                    timeout_s=0.5,
                    timeout_mode="ok",
                    max_count=10_000,
                    max_bytes=16,
                )
                record_count = rng.randint(2, 6)
                metadata_width = rng.randint(4, 8)
        elif mode == "success_within":
            op_mode = "success"
            expected_code = posture.VECTOR_OK
            expect_failure = False
            expect_records_none = False
            record_count = rng.randint(1, 3)
            metadata_width = 0
            # Ensure the bounds admit this small result.
            bounds = _StubBounds(
                timeout_s=0.5,
                timeout_mode="ok",
                max_count=10_000,
                max_bytes=10_485_760,
            )
        elif mode == "success_empty":
            op_mode = "success"
            expected_code = posture.VECTOR_OK
            expect_failure = False
            expect_records_none = False
            record_count = 0
            bounds = _StubBounds(
                timeout_s=0.5,
                timeout_mode="ok",
                max_count=10_000,
                max_bytes=10_485_760,
            )
        else:  # passthrough
            op_mode = "passthrough"
            expected_code = posture.UNSUPPORTED_OPERATION
            expect_failure = True
            expect_records_none = True

        operation = _make_operation(
            rng,
            op_mode,
            raise_kind=raise_kind,
            record_count=record_count,
            metadata_width=metadata_width,
        )

        # The wrapper NEVER raises — a leak would surface as an exception here.
        result = asyncio.run(run_bounded_operation(operation, bounds))

        assert isinstance(result, AdapterResult)
        assert result.code == expected_code, (
            f"mode={mode} expected code {expected_code!r}, got {result.code!r}"
        )

        if mode == "success_within":
            # Returned UNCHANGED, ok, records intact (non-empty).
            assert result.ok is True
            assert result.records is not None
            assert len(result.records) == record_count
            assert result != _EMPTY_SUCCESS
            continue

        if mode == "success_empty":
            # A genuine zero-record success — present but empty.
            assert result.ok is True
            assert result.records == ()
            assert result == _EMPTY_SUCCESS
            continue

        # Every failure arm (transport / timeout / size / unset / unavailable /
        # passthrough) is ABSENT, never an empty success.
        assert expect_failure
        assert expect_records_none
        assert result.ok is False
        assert result.records is None
        # The absent-vs-empty line: never byte-equal to a clean empty success.
        assert result != _EMPTY_SUCCESS
        assert result.records != ()
        # No success-only field leaked onto the failure value.
        assert result.count is None
        assert result.collections is None
        assert result.status is None

    # Every mode must have fired at least once — otherwise a degenerate run could
    # pass vacuously.
    for mode, fired in seen.items():
        assert fired, f"mode {mode!r} was never generated"


def test_empty_success_is_distinct_from_provider_unreachable_absent() -> None:
    """The absent-vs-empty distinction is a real inequality (R6.6).

    A zero-record ``VECTOR_OK`` success (``records == ()``, present-but-empty) is
    never equal to a ``PROVIDER_UNREACHABLE`` result (``records is None``, absent),
    and only the former is ``ok``. This pins the inequality the property relies on.

    _Validates: Requirements 6.2, 6.6._
    """
    empty_success = AdapterResult(code=posture.VECTOR_OK, records=())
    unreachable = AdapterResult(code=posture.PROVIDER_UNREACHABLE)

    assert empty_success.records == ()
    assert empty_success.ok is True

    assert unreachable.records is None
    assert unreachable.ok is False

    assert empty_success != unreachable
    assert empty_success.code != unreachable.code
