"""C2 replay differ, expected-diff ledger, and C3 scoring (GW02)."""

from gateway_v2.contracts.parity.clock import FrozenClock
from gateway_v2.contracts.parity.differ import Classification, classify
from gateway_v2.contracts.parity.types import C2Record, ReplayOutcome

__all__ = (
    "C2Record",
    "Classification",
    "FrozenClock",
    "ReplayOutcome",
    "classify",
)
