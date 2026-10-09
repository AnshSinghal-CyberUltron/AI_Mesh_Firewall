"""Bounded queues + admission-side backpressure (R2-07 / GW19, tasks 8.1 / 12.1).

Two pure, injected-clock data structures the
:class:`~gateway_v2.admit.admission.AdmissionController`
composes, factored out of ``admission.py`` to keep that module under the size gate while staying
inside the ``admit`` layer (imports only the standard library + sibling ``admit`` modules).

``BoundedQueue`` (task 8.1, Req 7.1-7.4) is the depth/age accountant behind every ``Bounded_Queue``
in the design (request / guard / dispatch / egress / audit). It carries a declared maximum depth
from :class:`~gateway_v2.admit.quota.AdmissionBounds`, admits an enqueue only while
``depth < max`` (``if depth >= max: shed at the door`` — Req 7.2 / Property 4), and reads the age of
its oldest item against the injected clock. It never grows past its bound, so memory stays bounded
and overload is visible before it collapses the system.

``BackpressureBuffer`` (task 12.1, Req 10.1-10.3) is the admission-side credit accountant for a
single streaming consumer. Each consumer is granted a byte credit derived from
``ResourceContract.stream_buffer_bytes`` (passed in — this module holds **no** capacity literal) and
stops being fed the moment its buffered bytes would exceed that credit. A slow consumer therefore
occupies at most its own credit regardless of how far it falls behind, so total streaming memory is
bounded across any number of slow consumers (Req 10.2) and a fast consumer draws only on its own
credit, unaffected by the slow ones (Req 10.3). The real SSE transport + credit wiring is
``egress/backpressure.py`` / ``egress/stream.py`` (GW13); admission owns only this slot/credit bound
and must not import ``egress`` (layer rule), so this is the admission-side mirror of that eventual
transport, cross-referenced rather than imported.

Neither type holds module-level mutable state; both are deterministic given the injected clock.
"""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass

__all__ = (
    "BackpressureBuffer",
    "BoundedQueue",
    "QueueFull",
)


class QueueFull(RuntimeError):
    """A bounded queue is at its declared maximum depth; the enqueue sheds at the door (Req 7.2)."""


class BoundedQueue:
    """A FIFO queue with a declared maximum depth and injected-clock age accounting.

    The declared ``max_depth`` comes from :class:`~gateway_v2.admit.quota.AdmissionBounds` (itself a
    :class:`ResourceContract` call), so this type holds no capacity literal of its own. Enqueue is
    gated: ``offer`` returns ``False`` and enqueues nothing once ``depth >= max_depth`` (Req 7.2),
    so the depth invariant ``depth <= max_depth`` holds at every step (Property 4). The age of the
    oldest item is read against the injected clock (``Callable[[], float]``, defaulting to
    ``time.monotonic`` per the house convention), so a test advances time deterministically.
    """

    __slots__ = ("_clock", "_enqueued_at", "_items", "_max_depth")

    def __init__(self, max_depth: int, *, clock: Callable[[], float] = time.monotonic) -> None:
        if max_depth < 1:
            raise QueueFull(f"bounded queue max depth {max_depth} is below one")
        self._max_depth = max_depth
        self._clock = clock
        # Parallel deques: the enqueued item and the clock instant it was enqueued, so the oldest
        # item's age is O(1) to read. Instance state only -- no module-level mutable state.
        self._items: deque[object] = deque()
        self._enqueued_at: deque[float] = deque()

    @property
    def max_depth(self) -> int:
        """The declared maximum depth (from ``AdmissionBounds``)."""
        return self._max_depth

    @property
    def depth(self) -> int:
        """Current depth. O(1). Never exceeds ``max_depth`` (Property 4)."""
        return len(self._items)

    def is_full(self) -> bool:
        """Whether a further enqueue would exceed the declared max (shed at the door)."""
        return len(self._items) >= self._max_depth

    def offer(self, item: object, *, now: float | None = None) -> bool:
        """Enqueue ``item`` iff below the bound; return whether it was enqueued.

        ``if depth >= max: shed at the door`` (Req 7.2): a full queue enqueues nothing and returns
        ``False`` so the caller sheds rather than exceed the bound. ``now`` defaults to the injected
        clock so the item's enqueue instant (and thus its age) is deterministic under test.
        """
        if self.is_full():
            return False
        moment = self._clock() if now is None else now
        self._items.append(item)
        self._enqueued_at.append(moment)
        return True

    def pop(self) -> object:
        """Remove and return the oldest item (FIFO). Raises ``IndexError`` when empty."""
        self._enqueued_at.popleft()
        return self._items.popleft()

    def oldest_age_s(self, *, now: float | None = None) -> float:
        """Age in seconds of the oldest item, read against the injected clock (Req 7.4).

        Returns ``0.0`` when the queue is empty, so the exported age series reads a real zero rather
        than an absent value (mirrors the metrics producer's empty-is-zero discipline).
        """
        if not self._enqueued_at:
            return 0.0
        moment = self._clock() if now is None else now
        return moment - self._enqueued_at[0]


@dataclass(frozen=True, slots=True)
class CreditReceipt:
    """Outcome of a buffer offer: whether it was accepted and the bytes now buffered.

    ``accepted`` is ``False`` when feeding the chunk would exceed the consumer's byte credit — the
    admission-side signal to stop feeding that consumer (Req 10.1). ``buffered_bytes`` is the
    consumer's current buffered total, always ``<= credit_bytes``.
    """

    accepted: bool
    buffered_bytes: int


class BackpressureBuffer:
    """Credit-based bounded buffer for one streaming consumer (admission-side mirror of GW13).

    A consumer is granted ``credit_bytes`` (derived from ``ResourceContract.stream_buffer_bytes``
    and passed in — no capacity literal here). ``offer`` accepts a produced chunk only while the
    buffered total stays within the credit; once the credit would be exceeded it refuses the chunk
    (``accepted=False``) so production stops feeding this consumer (Req 10.1). ``drain`` models the
    consumer reading bytes off the buffer, freeing credit. A slow consumer therefore occupies at
    most ``credit_bytes`` no matter how far behind it falls, so total streaming memory is bounded
    across any number of slow consumers (Req 10.2) and fast consumers are unaffected (Req 10.3).
    """

    __slots__ = ("_buffered", "_credit_bytes")

    def __init__(self, credit_bytes: int) -> None:
        if credit_bytes < 1:
            raise QueueFull(f"stream credit {credit_bytes} bytes is below one")
        self._credit_bytes = credit_bytes
        self._buffered = 0

    @property
    def credit_bytes(self) -> int:
        """The consumer's declared byte credit (from ``stream_buffer_bytes``)."""
        return self._credit_bytes

    @property
    def buffered_bytes(self) -> int:
        """Bytes currently buffered for this consumer. Always ``<= credit_bytes`` (Req 10.1)."""
        return self._buffered

    @property
    def available(self) -> int:
        """Remaining byte credit before backpressure engages."""
        return self._credit_bytes - self._buffered

    def offer(self, chunk_bytes: int) -> CreditReceipt:
        """Buffer a produced chunk iff it fits in the remaining credit.

        Returns a :class:`CreditReceipt`: ``accepted=False`` (and nothing buffered) when the chunk
        would push the buffered total past the credit, which is the admission-side backpressure
        signal to stop feeding this consumer (Req 10.1). A non-positive chunk is a no-op accept.
        """
        if chunk_bytes <= 0:
            return CreditReceipt(accepted=True, buffered_bytes=self._buffered)
        if self._buffered + chunk_bytes > self._credit_bytes:
            return CreditReceipt(accepted=False, buffered_bytes=self._buffered)
        self._buffered += chunk_bytes
        return CreditReceipt(accepted=True, buffered_bytes=self._buffered)

    def drain(self, read_bytes: int) -> int:
        """Model the consumer reading ``read_bytes`` off the buffer, freeing credit.

        Returns the buffered total after the read. Reading more than is buffered simply empties the
        buffer (clamped at zero); a non-positive read is a no-op.
        """
        if read_bytes <= 0:
            return self._buffered
        self._buffered = max(0, self._buffered - read_bytes)
        return self._buffered
