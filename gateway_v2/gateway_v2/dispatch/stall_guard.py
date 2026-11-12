"""Application-level stalled-upstream bound for one routing attempt (GW28 design C3).

``bounded_events`` wraps one candidate's event iterator with phase-specific deadlines enforced by
``asyncio.wait_for`` on every await, so a silent upstream can never hold a request for the transport
read timeout:

* Phase 1 (pre-content): ONE cumulative deadline ``start + first_event_s`` governs every event until
  the first event carrying text, an ``error_frame``, ``final`` or a ``gateway_error`` (D-14). A
  role-only (empty-delta) event neither resets nor extends it.
* Phase 2 (after that event): each wait is bounded by ``gap_s`` and by the remaining ``total_s``.
* On expiry exactly one ``upstream_timeout`` terminal event is yielded. ``wait_for`` cancels the
  pending read, so the httpx stream context exits and the connection is closed, not pooled.
* The ``finally`` block closes the underlying iterator within ``teardown_s``. If that close fails
  or times out on a normal exit, ``TeardownUnconfirmed`` is raised to the consumer so no further
  attempt can be opened. ``CancelledError``/``GeneratorExit`` are never swallowed.

The clock is the event-loop clock; it is a stall-guard input only, never a selection input.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass

from gateway_v2.dispatch.openai_upstream import UPSTREAM_TIMEOUT
from gateway_v2.dispatch.provider import UpstreamEvent
from gateway_v2.dispatch.routing_types import RoutingConfigError

__all__ = ("StallBounds", "TeardownUnconfirmed", "bounded_events")


class TeardownUnconfirmed(Exception):
    """The attempt iterator's ``aclose()`` raised or exceeded ``teardown_s``."""


@dataclass(frozen=True, slots=True)
class StallBounds:
    """Validated bound ordering: ``0 < gap_s < first_event_s < total_s`` and ``teardown_s > 0``."""

    first_event_s: float
    gap_s: float
    total_s: float
    teardown_s: float

    def __post_init__(self) -> None:
        if not (0 < self.gap_s < self.first_event_s < self.total_s) or not self.teardown_s > 0:
            raise RoutingConfigError("stall bounds must satisfy 0 < gap < first_event < total")


def _timeout() -> UpstreamEvent:
    return UpstreamEvent(text_deltas=(), final=True, gateway_error=UPSTREAM_TIMEOUT)


def _commits(event: UpstreamEvent) -> bool:
    return bool(
        event.text_deltas
        or event.error_frame is not None
        or event.final
        or event.gateway_error is not None
    )


def _terminal(event: UpstreamEvent) -> bool:
    return event.final or event.error_frame is not None or event.gateway_error is not None


async def _close(it: AsyncIterator[UpstreamEvent], teardown_s: float) -> None:
    aclose = getattr(it, "aclose", None)
    if aclose is None:
        return
    await aclose()  # TEMPORARY #116 CI EXPERIMENT: unbounded teardown; reverted in the next commit


async def bounded_events(
    it: AsyncIterator[UpstreamEvent], bounds: StallBounds
) -> AsyncIterator[UpstreamEvent]:
    """Yield ``it``'s events under the phase deadlines; see the module docstring."""
    loop = asyncio.get_running_loop()
    start = loop.time()
    first_deadline = start + bounds.first_event_s
    total_deadline = start + bounds.total_s
    content_started = False
    normal = False
    try:
        while True:
            now = loop.time()
            if content_started:
                limit = min(bounds.gap_s, total_deadline - now)
            else:
                limit = min(first_deadline, total_deadline) - now
            if limit <= 0:
                yield _timeout()
                break
            try:
                event = await asyncio.wait_for(it.__anext__(), limit)
            except StopAsyncIteration:
                break
            except TimeoutError:
                yield _timeout()
                break
            content_started = content_started or _commits(event)
            yield event
            if _terminal(event):
                break
        normal = True
    finally:
        try:
            await _close(it, bounds.teardown_s)
        except Exception as exc:
            if normal:
                raise TeardownUnconfirmed("attempt iterator close failed") from exc
