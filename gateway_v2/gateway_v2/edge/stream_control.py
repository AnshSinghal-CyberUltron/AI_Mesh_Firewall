"""First-byte latch + the no-splice gate for the streaming chat orchestration (GW12, task 8.1).

The streaming chat handler sits in :mod:`gateway_v2.edge.routes`; this sibling module holds the
ONE piece of per-request orchestration state that gates whether a signed safe retry/fallback may
still run: the :class:`FirstByteLatch`. It lives here (not in ``edge/routes.py`` inline, and not in
``edge/cancel.py`` which task 9 owns) so the no-splice invariant has a single, named, testable home
that the chat handler composes above its egress loop — exactly where the design places it
(Components §9: *"a one-way latch owned by the stream orchestration in edge/dispatch, above the
egress loop so it can gate retry/fallback before handing off to the provider"*).

**Why this is the ONE documented mutable-value exception.** Every other value type in this card is a
frozen slotted dataclass or a ``StrEnum``; the latch is the single documented mutable one-way-latch
exception (design Data Models; tasks.md Conventions). It is a one-way boolean: it starts unset,
flips to set exactly once on the first released content byte, and never flips back. The mutation is
monotone, so the "mutable" surface is a latch, not a general mutable value — the no-splice reasoning
depends only on *"once set, stays set"*. ``edge`` is outside the ``check_frozen_dataclasses`` gate's
scope (``plan``/``detect``/``resolve`` only), so the mutable slotted class needs no gate exemption
here, matching the shipped mutable slotted ``_Text`` / ``StreamStats`` in ``egress/stream.py`` and
the ``_TerminalLatch`` already in ``edge/routes.py``.

**The no-splice invariant (R7).** While the latch is unset, the gateway MAY run a signed safe
retry/fallback against a *fresh* provider response (R7.1) — nothing has reached the client, so
swapping the upstream is invisible downstream. The first released content byte sets the latch
(R7.2). Once set, an upstream failure yields clean termination or a declared ``Error_Frame`` and
NEVER a spliced fallback (R7.3): the gateway never concatenates two upstream responses into one
downstream stream (R7.4, the no-splice invariant). :func:`run_with_no_splice` is the orchestration
driver that enforces this — it attempts providers only while :meth:`FirstByteLatch.may_retry` is
true, and once a byte has been released it stops attempting new providers and lets the current
stream terminate.

**Fail closed.** An ambiguous retry decision *after* the first byte MUST NOT splice: the gate is a
hard boolean read of the latch, so an attempt is permitted ONLY when ``may_retry()`` is true. There
is no "retry anyway" path, no best-effort splice, and no module-level mutable — the latch is a
per-request instance threaded through the handler.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

__all__ = (
    "FirstByteLatch",
    "StreamAttempt",
    "run_with_no_splice",
)


class FirstByteLatch:
    """A one-way latch gating pre-first-byte retry/fallback (R7, the one mutable exception).

    Starts unset. :meth:`set_on_release` flips it on the first released content byte and is
    idempotent (a second call is a no-op — the latch is one-way). :meth:`may_retry` is true ONLY
    while unset, so a signed safe retry/fallback runs only before anything has reached the client
    (R7.1); :meth:`is_set` reports whether the first byte has been released.

    Mutable + slotted on purpose: it is the single documented mutable one-way-latch exception in
    this card (design Data Models). The ``__slots__`` keeps it a fixed two-method boolean cell with
    no instance ``__dict__`` — the mutation surface is exactly one monotone flag.
    """

    __slots__ = ("_set",)

    def __init__(self) -> None:
        self._set = False

    def may_retry(self) -> bool:
        """True only while unset — a signed safe retry/fallback may still run (R7.1/R7.3).

        Once the first content byte has been released the latch is set and this returns false, so
        the orchestration never attempts a second provider response (no-splice, R7.4). The read is
        a hard boolean: an ambiguous retry decision after the first byte can never be permitted.
        """
        return not self._set

    def set_on_release(self) -> None:
        """Flip the latch on the first released content byte (R7.2); idempotent thereafter.

        Called by the downstream sink the instant a content frame's bytes are handed to the
        client. One-way: a second call leaves the latch set (there is no reset), so once the first
        byte is out the no-splice gate stays closed for the life of the request.
        """
        self._set = True

    def is_set(self) -> bool:
        """Whether the first content byte has been released downstream."""
        return self._set


#: One attempt at producing the downstream stream against a single upstream (provider) response.
#:
#: Returns ``True`` when the attempt released at least one content byte downstream (so the latch is
#: now set and no further attempt may run), and ``False`` when it produced no byte — e.g. the
#: provider failed before the first byte, which is the ONLY situation in which a fresh attempt is
#: eligible. The attempt is responsible for calling :meth:`FirstByteLatch.set_on_release` through
#: its sink the moment it releases a content byte; :func:`run_with_no_splice` reads the latch to
#: decide eligibility, so an attempt that releases a byte and then fails can never be followed by a
#: splice. An attempt that raises propagates only when the latch is already set (a post-first-byte
#: failure is a clean termination / declared ``Error_Frame``, never a swap).
StreamAttempt = Callable[[], Awaitable[bool]]


async def run_with_no_splice(
    latch: FirstByteLatch,
    attempts: Callable[[], StreamAttempt | None],
) -> bool:
    """Drive stream attempts under the no-splice gate (R7.1–R7.4).

    ``attempts`` is a factory: each call yields the NEXT signed safe attempt to try (a fresh
    provider / fallback), or ``None`` when no further attempt is available. The driver runs an
    attempt only while :meth:`FirstByteLatch.may_retry` is true — i.e. before any content byte has
    reached the client:

    * If an attempt releases a content byte, its sink has already set the latch; the driver returns
      immediately and never requests another attempt (R7.2/R7.4 — at most one upstream response
      reaches the client).
    * If an attempt produces no byte (provider failed before the first byte) the latch is still
      unset, so a signed safe retry/fallback MAY run (R7.1): the driver asks the factory for the
      next attempt and tries it. Swapping here is invisible downstream because nothing was emitted.
    * If an attempt raises AFTER the latch is set, the exception propagates (clean termination / a
      declared ``Error_Frame`` is the caller's job); the driver does NOT catch it to splice a
      fallback (R7.3). A raise BEFORE the latch is set is treated as a byte-less failure and the
      next attempt (if any) runs.

    Returns ``True`` when a content byte was released (the stream carried exactly one upstream
    response), ``False`` when every eligible attempt was exhausted without releasing a byte (the
    caller renders the terminal failure). Fail-closed: the loop condition is ``latch.may_retry()``,
    so there is no path that runs a second attempt once a byte is out.
    """
    while latch.may_retry():
        attempt = attempts()
        if attempt is None:
            return False
        try:
            released = await attempt()
        except Exception:
            # A failure. If the latch is already set the first byte is out: re-raise so the caller
            # terminates cleanly / emits a declared Error_Frame — NEVER splice a fallback (R7.3).
            if latch.is_set():
                raise
            # Byte-less failure before the first byte: a fresh attempt is still eligible (R7.1).
            continue
        if released or latch.is_set():
            return True
    return latch.is_set()
