"""GW12 stream-timeout accessors, split out of `runtime/resources.py`.

`ResourceContract` mixes `StreamBoundsMixin` in. The `*_MULT` literals stay in
`runtime/resources.py` (the one module `lint/check_capacity_literals.py` exempts) and are read
function-locally, like `resources_vector.py`. A pure move: no behaviour change.
"""
from __future__ import annotations

from gateway_v2.runtime.errors import CapacityUnavailable


class StreamBoundsMixin:
    """Behaviour-only mixin; `target_p99_ms` is supplied by the host `ResourceContract`."""

    target_p99_ms: float

    def _stream_timeout_s(self, mult: float) -> float:
        """GW12: a stream timeout/duration/bound derived from the request-path SLO.

        value = p99_s x mult, clamped up to a derived floor of one p99 period (no stream bound
        may be shorter than a single SLO unit of work), and fails closed with
        `CapacityUnavailable` on a non-positive result -- matching the shipped
        `stream_buffer_bytes` contract. `target_p99_ms` is positive by construction
        (`from_signals` refuses to start otherwise), so the floor is positive; the guard is the
        same defensive fail-closed stance the rest of the contract takes.
        """
        from gateway_v2.runtime.resources import _MS_PER_S

        p99_s = self.target_p99_ms / _MS_PER_S
        value = max(p99_s * mult, p99_s)
        if value <= 0:
            raise CapacityUnavailable("stream timeout below minimum to serve")
        return value

    def inter_chunk_timeout_s(self) -> float:
        from gateway_v2.runtime.resources import _INTER_CHUNK_TIMEOUT_MULT

        return self._stream_timeout_s(_INTER_CHUNK_TIMEOUT_MULT)

    def idle_timeout_s(self) -> float:
        from gateway_v2.runtime.resources import _IDLE_TIMEOUT_MULT

        return self._stream_timeout_s(_IDLE_TIMEOUT_MULT)

    def write_timeout_s(self) -> float:
        from gateway_v2.runtime.resources import _WRITE_TIMEOUT_MULT

        return self._stream_timeout_s(_WRITE_TIMEOUT_MULT)

    def max_stream_duration_s(self) -> float:
        from gateway_v2.runtime.resources import _MAX_STREAM_DURATION_MULT

        return self._stream_timeout_s(_MAX_STREAM_DURATION_MULT)

    def max_cut_latency_s(self) -> float:
        from gateway_v2.runtime.resources import _MAX_CUT_LATENCY_MULT

        return self._stream_timeout_s(_MAX_CUT_LATENCY_MULT)

    def max_snapshot_age_s(self) -> float:
        from gateway_v2.runtime.resources import _MAX_SNAPSHOT_AGE_MULT

        return self._stream_timeout_s(_MAX_SNAPSHOT_AGE_MULT)

    def cancellation_bound_s(self) -> float:
        from gateway_v2.runtime.resources import _CANCEL_BOUND_MULT

        return self._stream_timeout_s(_CANCEL_BOUND_MULT)
