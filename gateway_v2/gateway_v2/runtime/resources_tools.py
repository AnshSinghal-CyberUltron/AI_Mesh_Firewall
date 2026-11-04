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

    def tool_bounds(self) -> ToolBounds:
        """Derive the tool bounds; fail closed (`CapacityUnavailable`) on any value below one.

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
        slots = math.floor(
            self.target_p99_ms * _MS_PER_S * _SCAN_BUDGET_FRACTION / _SCAN_SLOT_COST_US
        )
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
