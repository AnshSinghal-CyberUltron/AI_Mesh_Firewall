"""LGW12 first-byte-latch no-splice property test (GW12, task 8.2).

# Feature: sse-egress-pipeline, Property 3: No-post-first-byte-splice
# Validates: Requirements 7.3, 7.4

This module hosts the ``edge/stream_control`` no-splice property test. Property 3
(R7.4, with R7.3 and R7.1 exercised alongside): once the :class:`FirstByteLatch`
is set, :func:`run_with_no_splice` never requests a further attempt and the
downstream byte sequence contains bytes from AT MOST ONE upstream response — the
gateway never concatenates two upstream responses into one downstream stream.

The driver under test, ``run_with_no_splice(latch, attempts)``, takes a one-way
``FirstByteLatch`` and an ``attempts()`` factory that yields the next signed safe
attempt (``StreamAttempt``) or ``None`` when exhausted. Each attempt is an
``async`` callable that models one upstream (provider) response. We model the
"released bytes" each attempt pushes downstream with a per-run ``_Sink`` that
(a) sets the latch the instant the attempt releases its FIRST content byte and
(b) records WHICH attempt released each byte, so the recorded downstream can be
checked against the no-splice invariant: it must equal the released bytes of a
single attempt (a prefix/exact of one attempt's output), never two attempts'
bytes concatenated.

Each generated attempt is one of:

* ``RELEASE_THEN_OK``   — releases >= 1 byte, then returns ``True`` (clean).
* ``RELEASE_THEN_FAIL`` — releases >= 1 byte, then raises (post-first-byte
  failure: R7.3 — the raise must propagate / terminate, never splice).
* ``BYTELESS_FAIL``     — raises BEFORE releasing any byte (R7.1 — a byte-less
  failure before the first byte MAY be followed by a retry).
* ``BYTELESS_RETURN``   — returns ``False`` having released nothing (a provider
  that produced no content; eligible for retry just like a byte-less raise).

A seeded ``random.Random`` drives >= 10,000 iterations (house idiom; NO
``hypothesis``). Async paths run via ``asyncio.run``. The seed is a module
constant logged at test start and interpolated into every assertion message so a
counterexample can be replayed.

Test files are NOT under the import-linter layer contract, so importing from
``gateway_v2.edge`` here is allowed.

_Design: Correctness Properties → Property 3._
"""

from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import Awaitable

from gateway_v2.edge.stream_control import (
    FirstByteLatch,
    StreamAttempt,
    run_with_no_splice,
)

_LOG = logging.getLogger(__name__)

#: >= 10,000 iterations (house idiom, no hypothesis).
_ITERATIONS = 10_000

#: Reproducible seed; logged at test start and interpolated into every assertion
#: message so a counterexample can be replayed.
_SEED = 0x7B17E1A7

#: The four attempt kinds the sweep draws from (see the module docstring).
_RELEASE_THEN_OK = "release_then_ok"
_RELEASE_THEN_FAIL = "release_then_fail"
_BYTELESS_FAIL = "byteless_fail"
_BYTELESS_RETURN = "byteless_return"

_ATTEMPT_KINDS: tuple[str, ...] = (
    _RELEASE_THEN_OK,
    _RELEASE_THEN_FAIL,
    _BYTELESS_FAIL,
    _BYTELESS_RETURN,
)

#: Kinds that release at least one content byte before returning/raising.
_RELEASING_KINDS = frozenset({_RELEASE_THEN_OK, _RELEASE_THEN_FAIL})


class _AttemptFailure(RuntimeError):
    """The exception a generated attempt raises to model an upstream failure.

    A ``RELEASE_THEN_FAIL`` attempt raises this AFTER releasing a byte (so it must
    propagate out of :func:`run_with_no_splice`, R7.3); a ``BYTELESS_FAIL``
    attempt raises it BEFORE any byte (so the driver swallows it and tries the
    next attempt, R7.1).
    """


class _Sink:
    """Records the bytes each attempt releases downstream and drives the latch.

    One instance per run. ``release`` is what an attempt calls for each content
    byte it would send to the client: it sets the latch on the FIRST byte (R7.2)
    and appends ``(attempt_index, byte)`` to :attr:`downstream` so the test can
    later prove the downstream carries bytes from at most one attempt (R7.4).
    """

    __slots__ = ("latch", "downstream", "release_calls")

    def __init__(self, latch: FirstByteLatch) -> None:
        self.latch = latch
        #: Ordered record of every released byte as ``(attempt_index, byte)``.
        self.downstream: list[tuple[int, int]] = []
        #: Count of ``release`` calls, for a sanity check against ``downstream``.
        self.release_calls = 0

    def release(self, attempt_index: int, byte: int) -> None:
        """Release one content byte downstream from ``attempt_index``.

        Sets the latch on the first released byte of the whole stream (R7.2) and
        records which attempt produced the byte so a splice (two attempts'
        bytes in ``downstream``) is detectable.
        """
        self.release_calls += 1
        self.latch.set_on_release()
        self.downstream.append((attempt_index, byte))


def _run[T](coro: Awaitable[T]) -> T:
    """Drive one coroutine to completion (house idiom; no async test plugin)."""
    return asyncio.run(coro)  # type: ignore[arg-type]


def _random_bytes(rng: random.Random) -> tuple[int, ...]:
    """A NON-empty tuple of byte values a releasing attempt pushes downstream."""
    return tuple(rng.randint(0, 255) for _ in range(rng.randint(1, 6)))


def _make_attempt(
    *,
    kind: str,
    attempt_index: int,
    payload: tuple[int, ...],
    sink: _Sink,
) -> StreamAttempt:
    """Build one ``StreamAttempt`` of ``kind`` that releases ``payload`` via ``sink``.

    * A releasing kind releases every byte of ``payload`` through ``sink.release``
      (which sets the latch on the first), then either returns ``True``
      (``RELEASE_THEN_OK``) or raises ``_AttemptFailure`` (``RELEASE_THEN_FAIL``).
    * ``BYTELESS_FAIL`` releases nothing and raises; ``BYTELESS_RETURN`` releases
      nothing and returns ``False``.
    """

    async def attempt() -> bool:
        if kind in _RELEASING_KINDS:
            for byte in payload:
                sink.release(attempt_index, byte)
            if kind == _RELEASE_THEN_FAIL:
                raise _AttemptFailure(f"attempt {attempt_index} failed after releasing")
            return True
        if kind == _BYTELESS_FAIL:
            raise _AttemptFailure(f"attempt {attempt_index} failed before any byte")
        # _BYTELESS_RETURN: produced no content, eligible for retry.
        return False

    return attempt


def _random_plan(rng: random.Random) -> tuple[str, ...]:
    """A random sequence of attempt kinds the factory will hand out in order."""
    return tuple(rng.choice(_ATTEMPT_KINDS) for _ in range(rng.randint(0, 7)))


def test_property3_no_post_first_byte_splice() -> None:
    """Once the latch is set, downstream carries bytes from at most one response.

    # Feature: sse-egress-pipeline, Property 3: No-post-first-byte-splice
    # Validates: Requirements 7.3, 7.4

    A seeded ``random.Random`` drives >= 10,000 iterations. Each iteration builds
    a random plan of attempt kinds, wires them to a shared ``_Sink`` that records
    the per-attempt downstream byte stream and drives the latch, and runs
    ``run_with_no_splice``. It then asserts:

    * **No-splice (R7.4):** every byte recorded in ``downstream`` came from the
      SAME attempt index — the stream carried bytes from at most one upstream
      response; two attempts' bytes are never concatenated.
    * **Prefix/exact of a single attempt (R7.4):** the recorded downstream equals
      exactly the first releasing attempt's full payload — a prefix of one
      attempt, never a mix.
    * **Latch gates further attempts (R7.4):** no attempt is requested from the
      factory after the latch is set — the number of attempts invoked never
      exceeds (index of the first releasing attempt + 1).
    * **Byte-less-before-first-byte MAY retry (R7.1):** when the plan's leading
      attempts are all byte-less, each is tried in turn until one releases a byte
      or the plan is exhausted.
    * **Post-first-byte failure propagates, never splices (R7.3):** when the
      first releasing attempt is a ``RELEASE_THEN_FAIL``, the driver re-raises
      ``_AttemptFailure`` (clean termination is the caller's job) and the
      recorded downstream is still exactly that one attempt's bytes — no fallback
      spliced on.
    * **Latch/return agreement:** ``run_with_no_splice`` returns ``True`` iff a
      byte was released (``downstream`` non-empty and the latch set), ``False``
      iff nothing was ever released.
    """
    _LOG.info("Property 3 no-post-first-byte-splice seed=%#x", _SEED)
    rng = random.Random(_SEED)

    for i in range(_ITERATIONS):
        plan = _random_plan(rng)
        latch = FirstByteLatch()
        sink = _Sink(latch)

        # Pre-generate each attempt's released payload so the expected single
        # response's bytes are known independently of the driver.
        payloads: list[tuple[int, ...]] = [
            _random_bytes(rng) if kind in _RELEASING_KINDS else ()
            for kind in plan
        ]

        # The first attempt that would release a byte — the ONLY response whose
        # bytes may legitimately reach downstream (R7.4).
        first_releasing: int | None = next(
            (k for k, kind in enumerate(plan) if kind in _RELEASING_KINDS),
            None,
        )

        handed_out: list[int] = []

        def _factory() -> StreamAttempt | None:
            idx = len(handed_out)
            if idx >= len(plan):
                return None
            handed_out.append(idx)
            return _make_attempt(
                kind=plan[idx],
                attempt_index=idx,
                payload=payloads[idx],
                sink=sink,
            )

        raised: _AttemptFailure | None = None
        try:
            released = _run(run_with_no_splice(latch, _factory))
        except _AttemptFailure as exc:  # a post-first-byte failure propagates (R7.3)
            raised = exc
            released = None

        # ---- A post-first-byte failure MUST propagate, never be swallowed. --- #
        expect_raise = (
            first_releasing is not None
            and plan[first_releasing] == _RELEASE_THEN_FAIL
        )
        if expect_raise:
            assert raised is not None, (
                f"i={i} seed={_SEED:#x} plan={plan!r}: a post-first-byte failure "
                f"was swallowed (expected _AttemptFailure to propagate, R7.3)"
            )
        else:
            assert raised is None, (
                f"i={i} seed={_SEED:#x} plan={plan!r}: unexpected _AttemptFailure "
                f"propagated: {raised!r}"
            )

        # ---- No-splice: all recorded bytes came from ONE attempt (R7.4). ----- #
        source_indices = {idx for idx, _ in sink.downstream}
        assert len(source_indices) <= 1, (
            f"i={i} seed={_SEED:#x} plan={plan!r}: downstream carried bytes from "
            f"MULTIPLE attempts {sorted(source_indices)!r} — a splice (R7.4)"
        )

        if first_releasing is None:
            # No attempt ever releases a byte: downstream is empty, latch unset,
            # driver returned False, and every attempt in the plan was tried
            # (byte-less failures/returns are all retry-eligible, R7.1).
            assert sink.downstream == [], (
                f"i={i} seed={_SEED:#x} plan={plan!r}: bytes released with no "
                f"releasing attempt: {sink.downstream!r}"
            )
            assert not latch.is_set(), (
                f"i={i} seed={_SEED:#x} plan={plan!r}: latch set with no byte released"
            )
            assert released is False, (
                f"i={i} seed={_SEED:#x} plan={plan!r}: driver returned {released!r}, "
                f"want False when nothing was released"
            )
            assert handed_out == list(range(len(plan))), (
                f"i={i} seed={_SEED:#x} plan={plan!r}: byte-less attempts were not "
                f"all retried: handed_out={handed_out!r} (R7.1)"
            )
            continue

        # ---- A byte WAS released: it is exactly the first releasing attempt. - #
        released_bytes = [byte for _, byte in sink.downstream]
        expected_single = list(payloads[first_releasing])
        assert source_indices == {first_releasing}, (
            f"i={i} seed={_SEED:#x} plan={plan!r}: downstream bytes came from "
            f"attempt {sorted(source_indices)!r}, want only {first_releasing}"
        )
        # Prefix/exact of a SINGLE attempt's output: here, the exact full payload
        # of the first releasing attempt — never two attempts concatenated.
        assert released_bytes == expected_single, (
            f"i={i} seed={_SEED:#x} plan={plan!r}: downstream {released_bytes!r} is "
            f"not exactly the single response {expected_single!r} (R7.4)"
        )
        assert sink.release_calls == len(expected_single), (
            f"i={i} seed={_SEED:#x} plan={plan!r}: release_calls={sink.release_calls} "
            f"!= payload len {len(expected_single)}"
        )

        # ---- Latch gates further attempts: none requested after it is set. --- #
        # The latch flips on the first byte of attempt ``first_releasing``, so the
        # factory is asked for attempts 0..first_releasing (inclusive) and NEVER
        # for a later one (R7.4 — no second response is ever attempted).
        assert handed_out == list(range(first_releasing + 1)), (
            f"i={i} seed={_SEED:#x} plan={plan!r}: factory handed out {handed_out!r}, "
            f"want exactly {list(range(first_releasing + 1))!r} — an attempt was "
            f"requested after the latch set (splice path, R7.4)"
        )
        assert latch.is_set(), (
            f"i={i} seed={_SEED:#x} plan={plan!r}: latch not set after a byte released"
        )

        # ---- Latch/return agreement on the clean (non-raising) path. --------- #
        if not expect_raise:
            assert released is True, (
                f"i={i} seed={_SEED:#x} plan={plan!r}: driver returned {released!r}, "
                f"want True after a byte was released"
            )
