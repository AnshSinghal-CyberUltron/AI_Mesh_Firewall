"""A bound on audit records for requests that name no tenant (GW35).

A failed credential has no org, so its record goes to the reserved ``_unauthenticated`` stream.
The caller controls how often that happens, so a fixed-window counter caps writes per window. A
dropped record is counted, never delays or alters the 401/503, and a gate that cannot derive its
bound drops everything (fail closed). One gate per process, held on ``ChatRoute``; it holds no
request data.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping

from gateway_v2.runtime.errors import CapacityUnavailable
from gateway_v2.runtime.resources import ResourceContract
from gateway_v2.runtime.resources_audit import unauth_audit_rate

__all__ = ("UnauthAuditCounters", "UnauthAuditGate", "build_unauth_gate")

_LOG = logging.getLogger("amf.edge.unauth_audit")
ACCEPTED_SERIES = "amf_audit_unauthenticated_total"
DROPPED_SERIES = "amf_audit_unauthenticated_dropped_total"


class UnauthAuditGate:
    """Fixed-window admission for unauthenticated audit records. O(1), never raises."""

    __slots__ = ("_accepted", "_clock", "_count", "_dropped", "_rate", "_window_s", "_window_start")

    def __init__(
        self, *, rate_per_window: int, window_s: float, clock: Callable[[], float],
    ) -> None:
        self._rate = rate_per_window
        self._window_s = window_s
        self._clock = clock
        self._window_start = clock()
        self._count = 0
        self._accepted = 0
        self._dropped = 0

    def admit(self) -> bool:
        """True when one more record fits in the current window; otherwise count a drop."""
        try:
            now = self._clock()
            if now - self._window_start >= self._window_s or now < self._window_start:
                self._window_start, self._count = now, 0
            if self._count < self._rate:
                self._count += 1
                self._accepted += 1
                return True
        except Exception:  # noqa: BLE001 - the gate must never raise on the request path.
            pass
        self._dropped += 1
        return False

    @property
    def accepted(self) -> int:
        return self._accepted

    @property
    def dropped(self) -> int:
        return self._dropped


class UnauthAuditCounters:
    """Label-free producer over one gate: accepted and dropped totals."""

    SERIES_COUNT = 2
    __slots__ = ("_gate",)

    def __init__(self, gate: UnauthAuditGate) -> None:
        self._gate = gate

    def series(self) -> Mapping[str, float]:
        return {
            ACCEPTED_SERIES: float(self._gate.accepted),
            DROPPED_SERIES: float(self._gate.dropped),
        }


def build_unauth_gate(contract: ResourceContract, clock: Callable[[], float]) -> UnauthAuditGate:
    """A gate sized from the contract. An underivable bound drops all records and logs once."""
    try:
        return UnauthAuditGate(
            rate_per_window=unauth_audit_rate(contract),
            window_s=contract.metrics_window_s(),
            clock=clock,
        )
    except CapacityUnavailable:
        _LOG.error("unauthenticated audit rate underivable; dropping unauthenticated records")
        return UnauthAuditGate(rate_per_window=0, window_s=1.0, clock=clock)
