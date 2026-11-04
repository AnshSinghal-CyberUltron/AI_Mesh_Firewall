"""GW28 multi-model routing + stalled-upstream capacity accessors for `ResourceContract`.

Split out of `runtime/resources.py` (800-line size gate). The LITERALS these accessors read
(`_UPSTREAM_FIRST_EVENT_MULT`, `_UPSTREAM_GAP_FRACTION`, `_ROUTING_MAX_*`) stay in
`runtime/resources.py`, the one module `lint/check_capacity_literals.py` exempts; this module holds
only name references, so the constant reads are function-local to break the circular import (the
same arrangement as `resources_vector`). Every accessor fails closed with `CapacityUnavailable`.
"""

from __future__ import annotations

from gateway_v2.runtime.errors import CapacityUnavailable
from gateway_v2.runtime.kinds import PoolKind


class RoutingBoundsMixin:
    """Behaviour-only mixin (no fields, no ``__slots__``) composed into `ResourceContract`."""

    # Provided by the host `ResourceContract` (stubs so `mypy --strict` sees the types).
    def _stream_timeout_s(self, mult: float) -> float:  # pragma: no cover - host supplies
        raise NotImplementedError

    def inter_chunk_timeout_s(self) -> float:  # pragma: no cover - host supplies
        raise NotImplementedError

    def max_stream_duration_s(self) -> float:  # pragma: no cover - host supplies
        raise NotImplementedError

    def cancellation_bound_s(self) -> float:  # pragma: no cover - host supplies
        raise NotImplementedError

    def pool_size(self, kind: PoolKind) -> int:  # pragma: no cover - host supplies
        raise NotImplementedError

    target_p99_ms: float

    def upstream_first_event_timeout_s(self) -> float:
        """Cumulative pre-content upstream deadline (30 s at the 20 ms default)."""
        from gateway_v2.runtime.resources import _UPSTREAM_FIRST_EVENT_MULT

        return _positive(self._stream_timeout_s(_UPSTREAM_FIRST_EVENT_MULT), "first-event bound")

    def upstream_gap_timeout_s(self) -> float:
        """Mid-stream gap bound: a fraction of the edge inter-chunk bound (router fires first)."""
        from gateway_v2.runtime.resources import _MS_PER_S, _UPSTREAM_GAP_FRACTION

        p99_s = self.target_p99_ms / _MS_PER_S
        return _positive(
            max(_UPSTREAM_GAP_FRACTION * self.inter_chunk_timeout_s(), p99_s), "gap bound"
        )

    def upstream_total_timeout_s(self) -> float:
        """Absolute per-attempt ceiling: strictly below the edge max-stream duration."""
        return _positive(self.max_stream_duration_s() - self.cancellation_bound_s(), "total bound")

    def routing_pre_content_budget_s(self, n: int) -> float:
        """Edge first-chunk wait when routed: ``n x (first_event + cancel) + cancel`` (D-13)."""
        if n < 1:
            raise CapacityUnavailable("routing attempts below minimum")
        cancel = self.cancellation_bound_s()
        return _positive(n * (self.upstream_first_event_timeout_s() + cancel) + cancel, "budget")

    def provider_pool_share(self, n: int) -> int:
        """``pool_size(PROVIDER) // n`` with no floor; refuses when it would be < 1 (D-11)."""
        if n < 1:
            raise CapacityUnavailable("provider pool share needs at least one client")
        share = self.pool_size(PoolKind.PROVIDER) // n
        if share < 1:
            raise CapacityUnavailable("provider pool too small for the number of clients")
        return share

    def routing_max_attempts(self) -> int:
        from gateway_v2.runtime.resources import _ROUTING_MAX_ATTEMPTS

        return _positive_int(_ROUTING_MAX_ATTEMPTS, "max attempts")

    def routing_max_candidates(self) -> int:
        from gateway_v2.runtime.resources import _ROUTING_MAX_CANDIDATES

        return _positive_int(_ROUTING_MAX_CANDIDATES, "max candidates")

    def routing_max_weight(self) -> int:
        from gateway_v2.runtime.resources import _ROUTING_MAX_WEIGHT

        return _positive_int(_ROUTING_MAX_WEIGHT, "max weight")

    def routing_max_identifier_len(self) -> int:
        from gateway_v2.runtime.resources import _ROUTING_MAX_IDENT_LEN

        return _positive_int(_ROUTING_MAX_IDENT_LEN, "identifier length")


def _positive(value: float, what: str) -> float:
    if not value > 0:
        raise CapacityUnavailable(f"routing {what} below minimum to serve")
    return value


def _positive_int(value: int, what: str) -> int:
    if value < 1:
        raise CapacityUnavailable(f"routing {what} below minimum to serve")
    return value
