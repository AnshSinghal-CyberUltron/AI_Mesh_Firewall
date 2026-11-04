"""Per-request routing trace (GW28 design C6, "Trace").

One ``RoutingTrace`` is created for each chat request and hangs off that request's own
``RequestHolds``; it is never stored in a dict keyed by ``request_key`` (a content hash that two
concurrent identical requests share), so concurrent requests cannot conflate traces (the
PIPELINE-0023 class). It is bounded by ``routing_max_attempts() + 1`` entries: appends beyond the
bound are dropped and counted, never raised. It stores ``RoutingDecision`` values only, which carry
closed-vocabulary strings and operator-validated ids, never prompt or output text.
"""

from __future__ import annotations

import logging

from gateway_v2.dispatch.routing_types import RoutingDecision

__all__ = ("RoutingTrace", "deciding_rule")

_LOG = logging.getLogger("gateway_v2.edge.routing_trace")
_NONE = "-"


def _dash(value: str | None) -> str:
    return _NONE if value is None else value


def deciding_rule(decision: RoutingDecision, alias: str) -> str:
    """``reason|selected|attempt|remapped_from|alias`` -- five fields, ``-`` for None."""
    return "|".join(
        (
            decision.routing_reason,
            _dash(decision.selected_candidate),
            str(decision.attempt_index),
            _dash(decision.remapped_from),
            alias or _NONE,
        )
    )


class RoutingTrace:
    """Bounded, per-request decision list. ``append`` never raises."""

    __slots__ = ("_alias", "_cap", "_decisions", "_dropped")

    def __init__(self, *, cap: int, alias: str = _NONE) -> None:
        self._cap = cap
        self._alias = alias
        self._decisions: list[RoutingDecision] = []
        self._dropped = 0

    def append(self, decision: RoutingDecision) -> None:
        if len(self._decisions) >= self._cap:
            self._dropped += 1
            return
        self._decisions.append(decision)
        _LOG.info(
            "routing_decision reason=%s selected=%s model=%s destination=%s attempt=%d "
            "candidates=%d refusal=%s cause=%s source=%s",
            decision.routing_reason,
            _dash(decision.selected_candidate),
            _dash(decision.selected_model),
            _dash(decision.destination),
            decision.attempt_index,
            decision.candidate_count,
            _dash(decision.refusal_code),
            _dash(decision.cause),
            decision.decision_source,
        )

    def decisions(self) -> tuple[RoutingDecision, ...]:
        return tuple(self._decisions)

    @property
    def dropped(self) -> int:
        return self._dropped

    @property
    def alias(self) -> str:
        return self._alias
