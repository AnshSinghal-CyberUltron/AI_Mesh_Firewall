"""Multi-model routed upstream for the chat surface (GW28 design C4/C5).

``RoutedProvider`` is deliberately NOT a ``ProviderClient`` (it has no ``open``): it exposes
``open_routed(request, ctx)`` and receives the routing inputs out of band as a ``RoutingContext``.
Its safety properties are structural:

* ONE sequential attempt loop with exactly ONE candidate-open call site and one ``committed``
  latch. The next attempt is opened only after the previous attempt's iterator has been closed
  (``bounded_events`` closes it inside ``teardown_s``), and never once content was forwarded.
* The router never sees enforcement or prompt text. ``rebind`` replaces only the ``model`` field of
  ``body[BODY_OPENAI_KEY]`` and the destination label; every attempt reuses the same verified body.
* Every failure ends the stream with ONE ``UpstreamEvent(gateway_error=<code>)``; no HTTP object is
  built here. ``CancelledError``/``GeneratorExit`` propagate after the live iterator is closed.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Callable, Mapping
from types import MappingProxyType

from gateway_v2.dispatch.model_routing import ModelRouter
from gateway_v2.dispatch.openai_upstream import BODY_OPENAI_KEY, UPSTREAM_TIMEOUT
from gateway_v2.dispatch.provider import ProviderClient, UpstreamEvent, UpstreamRequest
from gateway_v2.dispatch.routing_types import (
    DECISION_SOURCE,
    REASON_FALLBACK_AFTER_RATE_LIMITED,
    REASON_FALLBACK_AFTER_TIMEOUT,
    REASON_FALLBACK_AFTER_UNAVAILABLE,
    REASON_FALLBACK_AFTER_UPSTREAM_ERROR,
    REASON_PRIMARY,
    REASON_REFUSED_ALL_UNAVAILABLE,
    REASON_REFUSED_ATTEMPTS_EXHAUSTED,
    REASON_REFUSED_CONTEXT_MISSING,
    REASON_REFUSED_INTERNAL_ERROR,
    REASON_REFUSED_NO_CANDIDATES,
    REASON_REFUSED_SNAPSHOT_UNAVAILABLE,
    REASON_REFUSED_TEARDOWN_UNCONFIRMED,
    REASON_REFUSED_UNKNOWN_MODEL,
    Candidate,
    RoutingConfigError,
    RoutingContext,
    RoutingDecision,
    RoutingRefusal,
    RoutingSnapshot,
    RoutingTable,
)
from gateway_v2.dispatch.stall_guard import StallBounds, TeardownUnconfirmed, bounded_events
from gateway_v2.domain.posture import (
    ROUTING_ALL_UNAVAILABLE,
    ROUTING_ATTEMPTS_EXHAUSTED,
    ROUTING_CONTEXT_MISSING,
    ROUTING_INTERNAL_ERROR,
    ROUTING_NO_CANDIDATES,
    ROUTING_SNAPSHOT_UNAVAILABLE,
    ROUTING_TEARDOWN_UNCONFIRMED,
    ROUTING_UNKNOWN_MODEL,
    STREAM_MALFORMED_UPSTREAM,
)

__all__ = ("RoutedProvider", "rebind")

_LOG = logging.getLogger("gateway_v2.dispatch.routed_provider")

_UNAVAILABLE = "upstream_unavailable"
_RATE_LIMITED = "upstream_rate_limited"
_UPSTREAM_ERROR = "upstream_error"
#: D-5: the closed set of failures that may fall back to the next candidate (pre-content only).
_RETRYABLE = frozenset({UPSTREAM_TIMEOUT, _UNAVAILABLE, _RATE_LIMITED, _UPSTREAM_ERROR})
_FALLBACK_REASON: Mapping[str, str] = MappingProxyType(
    {
        UPSTREAM_TIMEOUT: REASON_FALLBACK_AFTER_TIMEOUT,
        _UNAVAILABLE: REASON_FALLBACK_AFTER_UNAVAILABLE,
        _RATE_LIMITED: REASON_FALLBACK_AFTER_RATE_LIMITED,
        _UPSTREAM_ERROR: REASON_FALLBACK_AFTER_UPSTREAM_ERROR,
    }
)
_REFUSAL_REASON: Mapping[str, str] = MappingProxyType(
    {
        ROUTING_NO_CANDIDATES: REASON_REFUSED_NO_CANDIDATES,
        ROUTING_UNKNOWN_MODEL: REASON_REFUSED_UNKNOWN_MODEL,
        ROUTING_ALL_UNAVAILABLE: REASON_REFUSED_ALL_UNAVAILABLE,
        ROUTING_ATTEMPTS_EXHAUSTED: REASON_REFUSED_ATTEMPTS_EXHAUSTED,
        ROUTING_CONTEXT_MISSING: REASON_REFUSED_CONTEXT_MISSING,
        ROUTING_TEARDOWN_UNCONFIRMED: REASON_REFUSED_TEARDOWN_UNCONFIRMED,
        ROUTING_SNAPSHOT_UNAVAILABLE: REASON_REFUSED_SNAPSHOT_UNAVAILABLE,
        ROUTING_INTERNAL_ERROR: REASON_REFUSED_INTERNAL_ERROR,
    }
)


def _terminal(code: str) -> UpstreamEvent:
    return UpstreamEvent(text_deltas=(), final=True, gateway_error=code)


def rebind(request: UpstreamRequest, cand: Candidate) -> UpstreamRequest:
    """The ONLY mutation of the verified request: the model name and the destination label.

    A passthrough candidate leaves ``model`` untouched. A body without a rendered request is
    returned unchanged so the client's own ``upstream_request_unbuildable`` path refuses it.
    """
    openai = request.body.get(BODY_OPENAI_KEY)
    if not isinstance(openai, Mapping):
        return request
    body = dict(request.body)
    body[BODY_OPENAI_KEY] = (
        openai if cand.passthrough else MappingProxyType({**openai, "model": cand.upstream_model})
    )
    return UpstreamRequest(destination=cand.destination, body=MappingProxyType(body))


def _decision(
    ctx: RoutingContext,
    cand: Candidate | None,
    *,
    index: int,
    reason: str,
    count: int,
    prev: str | None = None,
    refusal: str | None = None,
    cause: str | None = None,
) -> RoutingDecision:
    model = None
    if cand is not None:
        model = cand.upstream_model or ctx.requested_model
    return RoutingDecision(
        request_key=ctx.request_key,
        requested_model=ctx.requested_model,
        selected_candidate=None if cand is None else cand.candidate_id,
        selected_model=model,
        destination=None if cand is None else cand.destination,
        attempt_index=index,
        remapped_from=prev,
        routing_reason=reason,
        decision_source=DECISION_SOURCE,
        candidate_count=count,
        refusal_code=refusal,
        cause=cause,
    )


class RoutedProvider:
    """Set-once, per-worker router; per-request state lives in the generator's locals."""

    __slots__ = (
        "_bounds",
        "_clients",
        "_max_attempts",
        "_max_ident_len",
        "_router",
        "_snapshot_source",
        "_table",
    )

    def __init__(
        self,
        *,
        router: ModelRouter,
        table: RoutingTable,
        clients: Mapping[str, ProviderClient],
        bounds: StallBounds,
        max_attempts: int,
        max_ident_len: int,
        snapshot_source: Callable[[], RoutingSnapshot],
    ) -> None:
        if max_attempts < 1:
            raise RoutingConfigError("max_attempts must be >= 1")
        if not isinstance(bounds, StallBounds):
            raise RoutingConfigError("bounds must be StallBounds")
        for cand in table.all_candidates():
            client = clients.get(cand.candidate_id)
            if client is None:
                raise RoutingConfigError(f"candidate {cand.candidate_id} has no client")
            if getattr(type(client), "per_request_safe", False) is not True:
                raise RoutingConfigError(f"client for {cand.candidate_id} is not per_request_safe")
        self._router = router
        self._table = table
        self._clients: Mapping[str, ProviderClient] = MappingProxyType(dict(clients))
        self._bounds = bounds
        self._max_attempts = max_attempts
        self._max_ident_len = max_ident_len
        self._snapshot_source = snapshot_source

    def preflight(self, ctx: RoutingContext | None) -> str | None:
        """Pure configuration check: a refusal code, or None. Reads no snapshot, opens nothing."""
        if not isinstance(ctx, RoutingContext):
            return ROUTING_CONTEXT_MISSING
        cset = self._table.candidates_for(ctx.org_id) or self._table.fallback_set
        if cset is None:
            return ROUTING_NO_CANDIDATES
        ranked = self._router.rank(ctx, cset, RoutingSnapshot(), max_ident_len=self._max_ident_len)
        if isinstance(ranked, RoutingRefusal):
            return ranked.code
        return None

    def open_routed(
        self, request: UpstreamRequest, ctx: RoutingContext | None
    ) -> AsyncIterator[UpstreamEvent]:
        return self._run(request, ctx)

    def serves_alias(self, name: str) -> bool:
        """True iff a NON-passthrough candidate lists ``name`` (passthrough does not count)."""
        return any(name in c.served_models for c in self._table.all_candidates())

    async def abort(self) -> None:
        """No-op: the consumer cuts a request by closing its iterator (D-6)."""
        return None

    # ------------------------------------------------------------------ internals

    def _emit(self, ctx: RoutingContext, decision: RoutingDecision, *, committed: bool) -> None:
        """Record a decision. Pre-content a sink failure refuses; post-commit it is logged."""
        try:
            ctx.sink(decision)
        except Exception:
            if not committed:
                raise
            _LOG.error("routing sink failed after commit", exc_info=True)

    def _plan(self, ctx: RoutingContext) -> tuple[tuple[Candidate, ...], None] | tuple[None, str]:
        """Table lookup + single snapshot reading + rank. Returns (ranked, None) or (None, code)."""
        cset = self._table.candidates_for(ctx.org_id) or self._table.fallback_set
        if cset is None:
            return None, ROUTING_NO_CANDIDATES
        try:
            snapshot = self._snapshot_source()
            if not isinstance(snapshot, RoutingSnapshot):
                raise TypeError("snapshot source returned a wrong type")
        except Exception:
            _LOG.error("routing snapshot unavailable", exc_info=True)
            return None, ROUTING_SNAPSHOT_UNAVAILABLE
        ranked = self._router.rank(ctx, cset, snapshot, max_ident_len=self._max_ident_len)
        if isinstance(ranked, RoutingRefusal):
            return None, ranked.code
        return ranked, None

    def _open(self, cand: Candidate, request: UpstreamRequest) -> AsyncIterator[UpstreamEvent]:
        """The ONLY candidate-open call site in this module (P4/P6)."""
        return bounded_events(
            self._clients[cand.candidate_id].open(rebind(request, cand)), self._bounds
        )

    async def _run(
        self, request: UpstreamRequest, ctx: RoutingContext | None
    ) -> AsyncIterator[UpstreamEvent]:
        if not isinstance(ctx, RoutingContext):
            _LOG.warning("routing context missing")
            yield _terminal(ROUTING_CONTEXT_MISSING)
            return
        att = _Attempt()
        try:
            ranked, code = self._plan(ctx)
            if ranked is None:
                assert code is not None
                self._emit(ctx, self._refusal(ctx, code, 0, 0), committed=False)
                yield _terminal(code)
                return
            n = min(self._max_attempts, len(ranked))
            prev: str | None = None
            cause: str | None = None
            for k, cand in enumerate(ranked[:n]):
                reason = REASON_PRIMARY if k == 0 else _FALLBACK_REASON.get(str(cause), "")
                self._emit(
                    ctx,
                    _decision(
                        ctx,
                        cand,
                        index=k,
                        reason=reason,
                        count=len(ranked),
                        prev=prev,
                        cause=cause,
                    ),
                    committed=False,
                )
                att = _Attempt(self._open(cand, request))
                failure: str | None = None
                while failure is None:
                    event, failure = await _step(att)
                    if event is not None:
                        yield event
                        if att.done:
                            return
                if failure in _RETRYABLE and k + 1 < n:
                    prev, cause = cand.candidate_id, failure
                    continue
                final = ROUTING_ATTEMPTS_EXHAUSTED if failure in _RETRYABLE and k >= 1 else failure
                self._emit(ctx, self._refusal(ctx, final, k, len(ranked), failure), committed=False)
                yield _terminal(final)
                return
        except TeardownUnconfirmed:
            att.live = None
            _LOG.error("attempt teardown unconfirmed")
            yield _terminal(ROUTING_TEARDOWN_UNCONFIRMED)
        except Exception:
            _LOG.error("routing internal error", exc_info=True)
            await _close_quietly(att)
            yield _terminal(ROUTING_INTERNAL_ERROR)
        finally:
            await _close_quietly(att)

    def _refusal(
        self,
        ctx: RoutingContext,
        code: str,
        index: int,
        count: int,
        cause: str | None = None,
    ) -> RoutingDecision:
        reason = _REFUSAL_REASON.get(code, REASON_REFUSED_INTERNAL_ERROR)
        return _decision(
            ctx, None, index=index, reason=reason, count=count, refusal=code, cause=cause
        )


class _Attempt:
    """Per-request, per-attempt local state (never shared, never on the provider)."""

    __slots__ = ("committed", "done", "live")

    def __init__(self, live: AsyncIterator[UpstreamEvent] | None = None) -> None:
        self.live = live
        self.committed = False
        self.done = False


async def _step(att: _Attempt) -> tuple[UpstreamEvent | None, str | None]:
    """Advance one attempt: ``(event_to_forward, None)``, ``(None, failure)`` or withheld."""
    live = att.live
    if live is None:
        return None, STREAM_MALFORMED_UPSTREAM
    try:
        event = await live.__anext__()
    except StopAsyncIteration:
        att.live = None
        if att.committed:
            att.done = True
            return _terminal(STREAM_MALFORMED_UPSTREAM), None
        return None, STREAM_MALFORMED_UPSTREAM
    if event.gateway_error is not None and not att.committed:
        await _finish(att)
        return None, event.gateway_error
    if att.committed or event.text_deltas or event.final or event.error_frame is not None:
        att.committed = True
        if event.final or event.error_frame is not None or event.gateway_error is not None:
            await _finish(att)
            att.done = True
        return event, None
    return None, None  # empty, non-terminal, pre-content: withheld


async def _finish(att: _Attempt) -> None:
    """Drive a finished attempt to its end so its iterator close is confirmed (or raises)."""
    live, att.live = att.live, None
    if live is None:
        return
    try:
        await live.__anext__()
    except StopAsyncIteration:
        return
    aclose = getattr(live, "aclose", None)
    if aclose is not None:
        await aclose()


async def _close_quietly(att: _Attempt) -> None:
    live, att.live = att.live, None
    aclose = getattr(live, "aclose", None)
    if aclose is None:
        return
    try:
        await aclose()
    except Exception:
        _LOG.error("attempt iterator close failed", exc_info=True)
