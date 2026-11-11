"""Pure weighted-rendezvous (HRW) ranking of routing candidates (GW28 design C2).

``ModelRouter.rank`` is a total function of ``(RoutingContext, CandidateSet, RoutingSnapshot)``:
no clock, no ``random``, no I/O, no instance state, and no enforcement/decision parameter, so it
cannot condition on policy. It consumes a ``RoutingContext`` (not an ``ExecutionPlan``), which
carries only tenant-stable plan identity; ``compiled_at``/``feed_seq``/``streaming_mode``/
``integrity_locked`` are excluded by type. Integer arithmetic only.

A refusal is RETURNED (``RoutingRefusal``), never raised, so mypy forces callers to handle it.
"""

from __future__ import annotations

import hashlib

from gateway_v2.dispatch.routing_types import (
    Candidate,
    CandidateSet,
    RoutingContext,
    RoutingRefusal,
    RoutingSnapshot,
    is_valid_model_name,
)
from gateway_v2.domain.posture import ROUTING_ALL_UNAVAILABLE, ROUTING_UNKNOWN_MODEL

__all__ = ("ModelRouter", "selection_key")

_SEP = "\x1f"


def selection_key(ctx: RoutingContext) -> str:
    """The HRW request key: plan identity + request key + requested model."""
    return _SEP.join(
        (
            ctx.org_id,
            str(ctx.epoch),
            str(ctx.sequence),
            ctx.content_hash,
            ctx.request_key,
            ctx.requested_model,
        )
    )


def _score(prefix: bytes, candidate: Candidate) -> int:
    """Max over ``weight`` vnodes of sha256(key SEP id SEP v); prefix hasher copied per vnode."""
    base = hashlib.sha256(prefix + candidate.candidate_id.encode("utf-8") + _SEP.encode())
    best = -1
    for v in range(candidate.weight):
        h = base.copy()
        h.update(str(v).encode("ascii"))
        value = int.from_bytes(h.digest(), "big")
        if value > best:
            best = value
    return best


class ModelRouter:
    """Stateless ranking; ``rank`` returns the full fallback order or a typed refusal."""

    __slots__ = ()

    def rank(
        self,
        ctx: RoutingContext,
        cset: CandidateSet,
        snapshot: RoutingSnapshot,
        *,
        max_ident_len: int,
    ) -> tuple[Candidate, ...] | RoutingRefusal:
        # 0. Client-name guard: never trust the (public) context constructor.
        if not is_valid_model_name(ctx.requested_model, max_ident_len):
            return RoutingRefusal(ROUTING_UNKNOWN_MODEL, "invalid or empty model name")
        # 1. match
        matching = [
            c for c in cset.candidates if c.passthrough or ctx.requested_model in c.served_models
        ]
        if not matching:
            return RoutingRefusal(ROUTING_UNKNOWN_MODEL, "no candidate serves the model")
        # 2. eligibility (single snapshot reading)
        eligible = [c for c in matching if c.candidate_id not in snapshot.unavailable]
        if not eligible:
            return RoutingRefusal(ROUTING_ALL_UNAVAILABLE, "all matching candidates unavailable")
        # 3-5. key, score, total order
        prefix = (selection_key(ctx) + _SEP).encode("utf-8")
        scored = [(-_score(prefix, c), c.candidate_id, c) for c in eligible]
        scored.sort(key=lambda t: (t[0], t[1]))
        return tuple(c for _s, _i, c in scored)
