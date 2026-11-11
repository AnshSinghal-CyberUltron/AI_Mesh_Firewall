"""GW30 vector-store-adapter capacity accessors, split out of `runtime/resources.py`.

`ResourceContract` mixes `VectorBoundsMixin` in to pick up the three GW30 bounds
(`vector_operation_timeout_s`, `vector_max_result_count`, `vector_max_result_bytes`). The split
keeps `runtime/resources.py` under its 800-line size gate; the capacity LITERALS these accessors
read (`_VECTOR_OP_TIMEOUT_MULT`, `_VECTOR_MAX_RESULT_COUNT` / `_VECTOR_MAX_RESULT_COUNT_MULT`,
`_VECTOR_MAX_RESULT_BYTES` / `_VECTOR_MAX_RESULT_BYTES_SHARE`) stay in `runtime/resources.py`, the
one module `lint/check_capacity_literals.py` exempts — this module holds only name references, no
capacity-position literal of its own (R11.1, R11.2, R11.4).

`resources.py` imports this mixin to build `ResourceContract`, and these accessors read that
module's constants; the constant reads are therefore function-local to break the otherwise-circular
import (`resources` → `resources_vector` for the mixin, `resources_vector` → `resources` for the
literals), the one direction that must stay deferred to its call site.

The accessors fail closed exactly like the shipped `stream_buffer_bytes` / `_guard_pool`:
`CapacityUnavailable` on a non-positive derived bound, `CapacityUnset` when the supplying signal is
unset (R6.4, R6.5, R11.6).
"""

from __future__ import annotations

import math

from gateway_v2.runtime.errors import CapacityUnavailable, CapacityUnset


class VectorBoundsMixin:
    """GW30 per-operation timeout and result-size bounds for `ResourceContract`.

    A behaviour-only mixin: it defines no field and no `__slots__`, so it composes cleanly with the
    frozen-slotted `ResourceContract` dataclass. It reads the host contract's `target_p99_ms`,
    `per_worker_rss` and `connection_budget()`; those are declared here only as annotations (no
    assignment) so `mypy --strict` sees their types without the mixin owning the state.
    """

    # Provided by the host `ResourceContract` (annotations only; no runtime attribute here).
    target_p99_ms: float
    per_worker_rss: int

    def connection_budget(self) -> int:  # pragma: no cover - supplied by ResourceContract
        raise NotImplementedError

    def vector_operation_timeout_s(self) -> float:
        """GW30: per-operation connector timeout in seconds (R6.4, R6.5).

        `p99_s x _VECTOR_OP_TIMEOUT_MULT` (~5 s at the 20 ms default). Raises `CapacityUnset` when
        no timeout signal is supplied — a non-finite `target_p99_ms` means the request-path SLO
        the budget derives from is unset, so the contract refuses to hand back a guessed bound,
        the same `None`-signal stance `_guard_pool` takes. Raises `CapacityUnavailable` on a
        non-positive result. A connector that gets `CapacityUnset` refuses to start the operation
        and returns `provider_unreachable` rather than issuing an unbounded wait.
        """
        from gateway_v2.runtime.resources import _MS_PER_S, _VECTOR_OP_TIMEOUT_MULT

        p99_ms = self.target_p99_ms
        if math.isnan(p99_ms) or math.isinf(p99_ms):
            raise CapacityUnset("vector operation timeout signal unset")
        value = (p99_ms / _MS_PER_S) * _VECTOR_OP_TIMEOUT_MULT
        if value <= 0:
            raise CapacityUnavailable("vector operation timeout below minimum to serve")
        return value

    def vector_max_result_count(self) -> int:
        """GW30: maximum result count, in `[1, _VECTOR_MAX_RESULT_COUNT]` (R11.2).

        `connection_budget() x _VECTOR_MAX_RESULT_COUNT_MULT` clamped up to one and down to the
        10,000-item ceiling, so a result count scales with how many tenant connections a worker
        budgets while never exceeding the R11.2 cap. Fails closed with `CapacityUnavailable` if the
        derived budget is below one — a worker that cannot admit even one connection could not
        serve a single result — matching every shipped contract method.
        """
        from gateway_v2.runtime.resources import (
            _VECTOR_MAX_RESULT_COUNT,
            _VECTOR_MAX_RESULT_COUNT_MULT,
        )

        derived = self.connection_budget() * _VECTOR_MAX_RESULT_COUNT_MULT
        if derived < 1:
            raise CapacityUnavailable("vector max result count below minimum to serve")
        return min(derived, _VECTOR_MAX_RESULT_COUNT)

    def vector_max_result_bytes(self) -> int:
        """GW30: maximum result payload bytes, in `[1, _VECTOR_MAX_RESULT_BYTES]` (R11.2).

        `per_worker_rss x _VECTOR_MAX_RESULT_BYTES_SHARE` clamped up to one byte and down to the
        10 MiB ceiling: a result payload is transient per-request state, so its ceiling is a small
        share of the per-request memory a worker budgets, the same `share`-of-a-declared-resource
        idiom `stream_buffer_bytes` / `_MAX_REQUEST_BYTES_SHARE` use. Fails closed with
        `CapacityUnavailable` if the derived share is below one byte — a surface that could not
        admit even a single byte of result could never serve — matching every shipped contract
        method.
        """
        from gateway_v2.runtime.resources import (
            _VECTOR_MAX_RESULT_BYTES,
            _VECTOR_MAX_RESULT_BYTES_SHARE,
        )

        derived = math.floor(self.per_worker_rss * _VECTOR_MAX_RESULT_BYTES_SHARE)
        if derived < 1:
            raise CapacityUnavailable("vector max result bytes below minimum to serve")
        return min(derived, _VECTOR_MAX_RESULT_BYTES)
