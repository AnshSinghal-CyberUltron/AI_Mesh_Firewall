"""Provider client protocol + injectable in-process stub (GW12, task 4.3).

This module owns the two shared dispatch value types that the egress loop reads
and that ``dispatch/transform.py`` (task 4.1) imports from here:

* :class:`UpstreamRequest` -- the destination + request body handed to a provider.
* :class:`UpstreamEvent` -- one upstream delivery: a tuple of ``(channel, delta)``
  text deltas (byte-compatible with ``egress.stream.DownstreamFrame.text_deltas``
  and ``UpstreamChunk.text_deltas``, both ``tuple[tuple[str, str], ...]``), a
  ``final`` flag, and an optional mid-stream ``error_frame`` (scanned per R11
  before any byte is forwarded).

:class:`ProviderClient` is the ``@runtime_checkable`` protocol the egress loop
reads from: it exposes an abortable streaming request (R8.1) and an ``abort``
coroutine that stops reading from the upstream source for the request (R8.2).

:class:`StubProviderClient` is the injectable, in-process provider local tests
substitute for a real socket (R8.5): it is driven by a seeded ``random.Random``
and a scripted sequence of :class:`UpstreamEvent`, yields the script from
``open``, and stops subsequent iteration once ``abort`` is awaited. The
real-socket provider is a DEFERRED live gate (R8.6) -- this module implements no
real HTTP client.

Layering (import-linter ``layers`` contract in ``pyproject.toml``): ``dispatch``
sits ABOVE ``egress`` and ``detect``; it may import ``domain`` and below. This
module imports only the standard library, so it adds no layer edge. The request
``body`` is a per-instance ``Mapping`` accepted through ``__init__`` arguments --
there is NO module-level mutable table (gated by
``lint/check_no_module_mutable.py``) and NO capacity literal.
"""

from __future__ import annotations

import random
from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

__all__ = (
    "ProviderClient",
    "StubProviderClient",
    "UpstreamEvent",
    "UpstreamRequest",
)


# --------------------------------------------------------------------------- #
# Shared dispatch value types (owned here; task 4.1 Transform imports them).
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class UpstreamRequest:
    """A resolved upstream request the ProviderClient opens a stream for (R8.1).

    ``destination`` is the plan-derived upstream selected by ``DispatchRouter``.
    ``body`` is the per-instance provider-format payload produced by
    ``Transform.to_provider``; it is a read-only ``Mapping`` carried by value, not
    a module-level mutable table.
    """

    destination: str
    body: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class UpstreamEvent:
    """One upstream delivery read from a provider stream (R8.1, R11).

    ``text_deltas`` is a tuple of ``(channel_name, delta_text)`` pairs -- one per
    text stream carried in this event -- byte-compatible with the shipped
    ``egress.stream.UpstreamChunk`` / ``DownstreamFrame`` transport shape so the
    egress loop and the SSE codec consume it unchanged. ``final`` is set on the
    event that carries the last token of the stream. ``error_frame`` is ``None``
    for an ordinary content event; a non-``None`` value is a provider mid-stream
    error frame that must be scanned before any of its bytes are forwarded
    downstream (R11).
    """

    text_deltas: tuple[tuple[str, str], ...]
    final: bool
    error_frame: str | None = None


# --------------------------------------------------------------------------- #
# ProviderClient protocol (task 4.3, R8.1 / R8.2).
# --------------------------------------------------------------------------- #


@runtime_checkable
class ProviderClient(Protocol):
    """The injectable, abortable upstream streaming source (R8.1).

    ``open`` returns an async iterator of :class:`UpstreamEvent` the egress loop
    reads from; ``abort`` stops reading from the upstream source for the request
    (R8.2), so the cancellation controller can cut an in-flight stream. The
    protocol is ``@runtime_checkable`` so a test can assert a stub satisfies it
    with ``isinstance`` (task 4.4).

    ``open`` is a plain (non-``async``) method returning an ``AsyncIterator`` --
    an async generator function has exactly this type -- so the caller writes
    ``async for event in provider.open(request): ...`` without an extra ``await``.
    """

    def open(self, request: UpstreamRequest) -> AsyncIterator[UpstreamEvent]: ...

    async def abort(self) -> None: ...


# --------------------------------------------------------------------------- #
# StubProviderClient: in-process, no socket (task 4.3, R8.5 / R8.6).
# --------------------------------------------------------------------------- #


class StubProviderClient:
    """An in-process :class:`ProviderClient` for local tests -- NO real socket.

    Driven by a seeded ``random.Random`` so a test's event stream is
    deterministic (``rng`` is injected, never created here), and by a scripted
    ``script`` of :class:`UpstreamEvent` that ``open`` yields in order. ``abort``
    flips a one-way ``_aborted`` latch; the next iteration of a stream opened by
    this client then stops, so an in-flight ``async for`` ceases reading at the
    next event boundary (R8.2) exactly as the real abort stops a socket read.

    This is what the egress tests inject in place of the real provider (R8.5).
    The real-socket path is a DEFERRED live gate (R8.6); it is not implemented
    here.

    It is a plain slotted class rather than a frozen dataclass because the abort
    latch is mutable one-way state; the two value types it carries
    (:class:`UpstreamRequest` / :class:`UpstreamEvent`) are the frozen slotted
    dataclasses. The ``rng`` is accepted and retained for parity with the real
    provider's injected-randomness contract (jittered reads in the live client)
    and so scripted-stub callers share one deterministic source; it is
    intentionally not consumed by the deterministic script replay below.
    """

    __slots__ = ("_aborted", "rng", "script")

    def __init__(
        self,
        *,
        script: Sequence[UpstreamEvent],
        rng: random.Random,
    ) -> None:
        # Snapshot the script into an immutable tuple so a caller mutating the
        # passed-in sequence after construction cannot change what a stream
        # replays (and so there is no shared mutable state between streams).
        self.script: tuple[UpstreamEvent, ...] = tuple(script)
        self.rng = rng
        self._aborted = False

    async def open(self, request: UpstreamRequest) -> AsyncIterator[UpstreamEvent]:
        """Yield the scripted events in order until exhausted or aborted (R8.1).

        ``request`` selects the upstream in the real client; the stub replays its
        pre-recorded ``script`` regardless, which is what makes local tests
        deterministic and socket-free. The abort latch is checked BEFORE each
        yield so an ``abort`` awaited mid-iteration stops the stream at the next
        event boundary and yields nothing further (R8.2).
        """
        for event in self.script:
            if self._aborted:
                return
            yield event

    async def abort(self) -> None:
        """Stop reading from the (stub) upstream for this request (R8.2).

        Sets the one-way abort latch; the ``open`` iterator observes it before its
        next yield and stops. Idempotent: a second ``abort`` is a no-op.
        """
        self._aborted = True
