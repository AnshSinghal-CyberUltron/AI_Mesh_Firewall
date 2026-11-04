"""GW17c metrics producer: label-free, seeded at zero, a fixed series set.

No method takes a key, org or tenant argument, so the series count cannot move with the estate.
`fail_open_total` is pinned to zero: nothing in the policy or cap path fails open. Publication
through the edge `Publisher` is a wiring step that is NOT done in this card (see the handoff).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class McpPolicyReading:
    tool_denied_total: int
    cap_refused_total: int
    cap_async_refill_total: int
    cap_overshoot: int
    cap_evicted_total: int
    fail_open_total: int


class McpPolicyMetrics:
    """O(1) observers; ``snapshot`` is the only reader."""

    SERIES_COUNT = 6

    def __init__(self) -> None:
        self._denied = self._refused = self._refills = self._overshoot = self._evicted = 0

    def observe_tool_denied(self) -> None:
        self._denied += 1

    def observe_cap_refused(self) -> None:
        self._refused += 1

    def observe_async_refill(self) -> None:
        self._refills += 1

    def observe_overshoot(self, unpaid_debt: int) -> None:
        """Declared overshoot actually taken: unpaid bootstrap debt at a lease reset."""
        self._overshoot = max(self._overshoot, unpaid_debt)

    def observe_evicted(self) -> None:
        self._evicted += 1

    def snapshot(self) -> McpPolicyReading:
        return McpPolicyReading(
            self._denied, self._refused, self._refills, self._overshoot, self._evicted, 0
        )
