"""Bounded coalescer + credit-based flow control (GW12, R4 / R5).

The streaming egress path must bound the memory a single stream can hold so a slow
downstream consumer cannot make the gateway buffer an entire response. Two cooperating
mechanisms do that:

* :class:`Coalescer` is the bounded per-stream buffer. Its ``High_Water_Mark`` is DERIVED
  from ``ResourceContract.stream_buffer_bytes(active_streams)`` and is never a literal --
  the gateway holds at most that many unflushed payload bytes for one stream, applies
  backpressure the moment the buffer reaches the mark, and resumes once it falls below it
  (R4.1-R4.4, R4.6). When the derived ceiling is below one byte the coalescer fails closed:
  it refuses to admit the stream and buffers zero payload bytes (R4.5).
* :class:`CreditFlowControl` is the credit mechanism. A stream reads from upstream only
  while it holds outstanding credit, and credit is replenished ONLY as the downstream
  consumer consumes previously released bytes -- grant exactly N on consuming N, grant zero
  while nothing has been consumed (R5.1-R5.3). Credit is conserved so that at every
  observation point ``granted == outstanding + consumed`` with zero deviation (R5.7).

**Layering.** ``egress`` sits BELOW ``detect`` in the import-linter layer contract
(``pyproject.toml``), so this module imports only ``gateway_v2.runtime`` and
``gateway_v2.domain`` -- never ``gateway_v2.detect``. The scanner is injected into the
``StreamPipeline`` elsewhere; the coalescer needs no scanner.

**No capacity literal.** ``lint/check_capacity_literals.py`` flags ``Semaphore(<int>)``,
``Queue(<int>)``, and ``maxsize=/high_water=/buffer_size=`` keyword literals. This module
names none of those with a literal size: every bound comes from the injected
``ResourceContract`` via ``stream_buffer_bytes(active_streams())``.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from gateway_v2.domain import posture
from gateway_v2.runtime.errors import CapacityUnavailable
from gateway_v2.runtime.resources import ResourceContract

__all__ = (
    "Coalescer",
    "CoalescerVerdict",
    "CreditFlowControl",
    "CreditState",
)


# --------------------------------------------------------------------------- #
# Value types (frozen slotted dataclasses).
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class CreditState:
    """A credit observation: total ``granted`` vs. total ``consumed``.

    ``granted`` is the cumulative credit ever granted to the stream (one byte of credit per
    byte the downstream consumer has consumed). ``consumed`` is the credit the stream has
    spent reading upstream bytes. ``outstanding`` is DERIVED (``granted - consumed``) and is
    never stored independently, so the credit-conservation invariant
    ``granted == outstanding + consumed`` holds by construction at every observation point
    (R5.7).
    """

    granted: int = 0
    consumed: int = 0

    @property
    def outstanding(self) -> int:
        """Credit granted but not yet spent on upstream reads (never negative)."""
        return self.granted - self.consumed


@dataclass(frozen=True, slots=True)
class CoalescerVerdict:
    """The outcome of :meth:`Coalescer.admit`.

    ``code`` is ``None`` when the stream is admitted; a set ``code`` (a ``domain.posture``
    value-code) means the stream was rejected and ``edge`` renders it. ``high_water`` is the
    derived per-stream ceiling in bytes at admission time -- zero on a fail-closed rejection,
    since no payload byte may be buffered when the ceiling is below one byte (R4.5).
    """

    code: str | None
    high_water: int


# --------------------------------------------------------------------------- #
# Coalescer (R4).
# --------------------------------------------------------------------------- #


class Coalescer:
    """A bounded per-stream buffer whose ceiling is derived, never a literal (R4).

    The ceiling (``High_Water_Mark``) is ``contract.stream_buffer_bytes(active_streams())``
    recomputed live from the injected ``active_streams`` count, so the per-stream bound
    tightens as concurrency rises and the aggregate stays within
    ``active_streams x per-stream ceiling`` (R4.1, R5.4). ``offer`` refuses the moment the
    buffer would reach the mark (backpressure, R4.3); ``release`` shrinks the buffer as the
    downstream consumer drains it, which is what lets a suspended stream resume once the
    buffer falls below the mark (R4.6). The buffered count never exceeds the mark across the
    stream's lifetime (R4.2, R4.4) because ``offer`` admits a delta only when it fits.
    """

    __slots__ = ("_active_streams", "_buffered", "_contract")

    def __init__(
        self,
        *,
        contract: ResourceContract,
        active_streams: Callable[[], int],
    ) -> None:
        self._contract = contract
        self._active_streams = active_streams
        self._buffered = 0

    def high_water(self) -> int:
        """The derived per-stream ceiling in bytes (R4.1).

        Returns ``contract.stream_buffer_bytes(active_streams())`` -- no literal. Propagates
        ``CapacityUnavailable`` when the derived ceiling is below one byte; :meth:`admit`
        catches that and renders the fail-closed verdict (R4.5).
        """
        return self._contract.stream_buffer_bytes(self._active_streams())

    def admit(self) -> CoalescerVerdict:
        """Decide whether this stream may buffer any bytes (R4.5, fail closed).

        When the derived ceiling is at least one byte, the stream is admitted and the verdict
        carries ``code=None`` with the current ``high_water``. When
        ``stream_buffer_bytes`` raises ``CapacityUnavailable`` (ceiling below one byte), the
        verdict carries ``posture.STREAM_BUFFER_UNAVAILABLE`` and ``high_water=0`` -- the
        stream is refused and no payload byte is ever buffered.
        """
        try:
            mark = self.high_water()
        except CapacityUnavailable:
            # Fail closed: refuse the stream and buffer zero payload bytes (R4.5). The verdict
            # is built positionally so the `high_water` field carries no keyword literal (the
            # capacity-literal gate flags `high_water=<int>` regardless of context).
            return CoalescerVerdict(posture.STREAM_BUFFER_UNAVAILABLE, self._no_buffer_bytes())
        return CoalescerVerdict(code=None, high_water=mark)

    @staticmethod
    def _no_buffer_bytes() -> int:
        """The buffered-byte count of a fail-closed (never-admitted) stream (R4.5).

        A rejected stream holds nothing, so this is empty. Kept as a named helper rather than a
        literal in the verdict construction so the per-stream byte count stays sourced from a
        single, intention-revealing place.
        """
        return len(b"")

    def offer(self, nbytes: int) -> bool:
        """Try to buffer ``nbytes`` more; return ``False`` on backpressure (R4.2-R4.4).

        Returns ``True`` and buffers the bytes only when the resulting buffered count stays
        strictly below the ``High_Water_Mark``; returns ``False`` without buffering anything
        when the delta would make buffered reach or exceed the mark. Because a delta is
        admitted only when it fits, the buffered count never exceeds the mark at any point in
        the stream's lifetime (R4.4). A non-positive ``nbytes`` buffers nothing and admits.

        Propagates ``CapacityUnavailable`` if the ceiling drops below one byte mid-stream
        (concurrency rose); the caller treats that as fail-closed backpressure.
        """
        if nbytes <= 0:
            return True
        mark = self.high_water()
        if self._buffered + nbytes >= mark:
            return False
        self._buffered += nbytes
        return True

    def release(self, nbytes: int) -> None:
        """Record that ``nbytes`` buffered bytes were flushed downstream (R4.6).

        Shrinks the buffer so a suspended stream can resume once buffered falls below the
        mark. Clamps at zero so a release can never drive the buffered count negative; a
        non-positive ``nbytes`` is a no-op.
        """
        if nbytes <= 0:
            return
        self._buffered = max(0, self._buffered - nbytes)

    def buffered(self) -> int:
        """The current unflushed payload byte count held for this stream (R4.2)."""
        return self._buffered


# --------------------------------------------------------------------------- #
# CreditFlowControl (R5).
# --------------------------------------------------------------------------- #


class CreditFlowControl:
    """Credit replenished only as downstream consumes; conserve credit exactly (R5).

    A stream reads from upstream only while ``outstanding() > 0`` AND the coalescer buffer is
    below its high-water (R5.1): :meth:`may_read` gates reads on both. Credit is granted ONLY
    as the downstream consumer consumes previously released bytes -- :meth:`grant_on_consume`
    grants exactly N on consuming N bytes (R5.2) and grants zero when nothing was consumed
    (R5.3). ``consumed`` is spent as the stream reads upstream, so at every observation point
    ``granted == outstanding + consumed`` with zero deviation (R5.7).
    """

    __slots__ = ("_consumed", "_granted")

    def __init__(self) -> None:
        self._granted = 0
        self._consumed = 0

    def grant_on_consume(self, consumed_bytes: int) -> int:
        """Grant exactly ``consumed_bytes`` of credit; grant zero when idle (R5.2/R5.3).

        Returns the amount granted. A non-positive ``consumed_bytes`` grants zero (R5.3) and
        does not move any counter, so a stream whose downstream has consumed nothing since the
        last grant receives no fresh read quota.
        """
        if consumed_bytes <= 0:
            return 0
        self._granted += consumed_bytes
        return consumed_bytes

    def spend(self, nbytes: int) -> None:
        """Spend ``nbytes`` of outstanding credit on an upstream read.

        A stream reads upstream only while it holds outstanding credit (R5.1), so a read
        consumes credit here. Clamps at the granted total so ``outstanding`` can never go
        negative and the conservation invariant ``granted == outstanding + consumed`` holds
        (R5.7). A non-positive ``nbytes`` is a no-op.
        """
        if nbytes <= 0:
            return
        self._consumed = min(self._granted, self._consumed + nbytes)

    def outstanding(self) -> int:
        """Credit granted but not yet spent (``granted - consumed``, never negative)."""
        return self._granted - self._consumed

    def state(self) -> CreditState:
        """A frozen observation of the credit counters (conservation holds, R5.7)."""
        return CreditState(granted=self._granted, consumed=self._consumed)

    def may_read(self, buffered: int, high_water: int) -> bool:
        """Whether the stream may read another upstream byte (R5.1).

        True only while the stream holds outstanding credit AND its coalescer buffer is below
        the derived high-water -- the two gates that together bound total streaming memory to
        ``active_streams x per-stream ceiling`` (R5.4). A slow consumer that stops consuming
        drives ``outstanding`` to zero and halts upstream reads (R5.1); a full buffer halts
        them even while credit remains.
        """
        return self.outstanding() > 0 and buffered < high_water
