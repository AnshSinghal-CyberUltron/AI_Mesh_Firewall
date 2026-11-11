"""GW34 tool-calling bounds, split out of `runtime/resources.py` (size gate).

`ResourceContract` mixes `ToolBoundsMixin` in. Every capacity LITERAL (`_MAX_TOOLS`, ...) stays in
`runtime/resources.py`, the one module `lint/check_capacity_literals.py` exempts; this module holds
references and derivations only (no numeric capacity literal) and is NOT added to the gate's
allow-list. The constant reads are function-local to break the circular import, like
`resources_vector.py`. Each derived bound is checked `>= 1`, else `CapacityUnavailable`.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from gateway_v2.runtime.errors import CapacityUnavailable

__all__ = ("ToolBounds", "ToolBoundsMixin")


@dataclass(frozen=True, slots=True)
class ToolBounds:
    """The bounds tool/function calling is validated against (see design "ToolBounds values")."""

    max_tools: int
    schema_depth: int
    schema_nodes: int
    max_tool_calls: int
    arguments_bytes: int
    max_scanned_slots: int
    parts_allowance: int
    max_withheld_bytes: int


class ToolBoundsMixin:
    """Behaviour-only mixin (no field, no `__slots__`) supplying `tool_bounds()`."""

    target_p99_ms: float

    def max_request_bytes(self) -> int:  # pragma: no cover - supplied by ResourceContract
        raise NotImplementedError

    def connection_budget(self) -> int:  # pragma: no cover - supplied by ResourceContract
        raise NotImplementedError

    def stream_buffer_bytes(self, active_streams: int) -> int:  # pragma: no cover
        raise NotImplementedError

    def scan_window_s(self) -> float:
        """The wall time one request's detector invocations may take: the scan share of p99.

        `max_scanned_slots` is how many slots fit in this window, and boot calibration times
        passes of exactly this length (edge/scan_calibration.py).
        """
        from gateway_v2.runtime.resources import _MS_PER_S, _SCAN_BUDGET_FRACTION

        return self.target_p99_ms / _MS_PER_S * _SCAN_BUDGET_FRACTION

    def tool_bounds(
        self, slot_cost_us: float | None = None, scanned_slots_floor: int = 0
    ) -> ToolBounds:
        """Derive the tool bounds; fail closed (`CapacityUnavailable`) on any value below one.

        `slot_cost_us` is this host's measured per-slot scan cost (boot calibration,
        `edge/scan_calibration.py`); without it the DECLARED `_SCAN_SLOT_COST_US` is used, so a
        faster host admits more scanned slots and a slower one fewer, inside the same budget.
        `scanned_slots_floor` raises a calibrated bound that came out below what a realistic
        request needs (`edge/scan_calibration.py` decides it and logs the clamp).

        `max_withheld_bytes` is the per-stream buffer ceiling at the connection budget (the
        largest stream count the worker admits), so the assembler's total withheld bytes can never
        exceed a share of memory the egress holds already use (the live `Coalescer` charge is the
        second, per-stream bound).
        """
        from gateway_v2.runtime.resources import (
            _MAX_TOOL_CALLS,
            _MAX_TOOLS,
            _MS_PER_S,
            _PARTS_PER_SCHEMA_NODE,
            _SCAN_BUDGET_FRACTION,
            _SCAN_SLOT_COST_US,
            _SCHEMA_DEPTH,
            _SCHEMA_NODE_BYTES,
            _TOOL_ARGUMENTS_DIVISOR,
        )

        request_bytes = self.max_request_bytes()
        nodes = request_bytes // _SCHEMA_NODE_BYTES
        cost_us = _SCAN_SLOT_COST_US if slot_cost_us is None else slot_cost_us
        if not cost_us > 0 or not math.isfinite(cost_us):  # `not >` also refuses NaN
            raise CapacityUnavailable("scan slot cost must be finite and positive")
        # Same window as scan_window_s(), kept in microseconds to avoid float round-trips.
        slots = math.floor(self.target_p99_ms * _MS_PER_S * _SCAN_BUDGET_FRACTION / cost_us)
        slots = max(slots, scanned_slots_floor)
        bounds = ToolBounds(
            max_tools=_MAX_TOOLS,
            schema_depth=_SCHEMA_DEPTH,
            schema_nodes=nodes,
            max_tool_calls=_MAX_TOOL_CALLS,
            arguments_bytes=request_bytes // _TOOL_ARGUMENTS_DIVISOR,
            max_scanned_slots=slots,
            parts_allowance=_PARTS_PER_SCHEMA_NODE * nodes,
            max_withheld_bytes=self.stream_buffer_bytes(self.connection_budget()),
        )
        for name in bounds.__slots__:
            if getattr(bounds, name) < 1:
                raise CapacityUnavailable(f"tool bound {name} below minimum to serve")
        return bounds
