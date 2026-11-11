"""Group F output-budget accessors, split out of `runtime/resources.py`.

`ResourceContract` mixes `BudgetBoundsMixin` in. The capacity LITERALS these accessors return
(`_OUTPUT_TOKENS_HARD_CAP`, `_DEFAULT_RESPONSE_TOKEN_CEILING`, `_MAX_OUTPUT_TOKENS_BOUNDARY`,
`_MAX_COMPLETIONS_PER_REQUEST`) stay in `runtime/resources.py`, the one module
`lint/check_capacity_literals.py` exempts; the reads are function-local to break the circular
import (the same shape as `resources_vector.py`). A pure move: no behaviour change.
"""
from __future__ import annotations


class BudgetBoundsMixin:
    """Behaviour-only mixin (no field, no `__slots__`) for the Group F static accessors."""

    @staticmethod
    def max_output_tokens() -> int:
        """Group F: v1's hard cap on a clamped `max_tokens` (`_OUTPUT_TOKENS_HARD_CAP`, R2.16)."""
        from gateway_v2.runtime.resources import _OUTPUT_TOKENS_HARD_CAP

        return _OUTPUT_TOKENS_HARD_CAP

    @staticmethod
    def default_response_token_ceiling() -> int:
        """Group F: v1's default org `max_response_tokens` (`_DEFAULT_RESPONSE_TOKEN_CEILING`).

        The org ceiling the output-budget clamp uses until the plan carries the org value
        (Phase 3 / GW14b), R2.15 / R2.16.
        """
        from gateway_v2.runtime.resources import _DEFAULT_RESPONSE_TOKEN_CEILING

        return _DEFAULT_RESPONSE_TOKEN_CEILING

    @staticmethod
    def max_output_tokens_boundary() -> int:
        """Group F: v1's `MAX_OUTPUT_TOKENS_CEILING` parse boundary (`_MAX_OUTPUT_TOKENS_BOUNDARY`).

        A `max_tokens` / `max_completion_tokens` above it is refused with HTTP 400 (R2.16).
        """
        from gateway_v2.runtime.resources import _MAX_OUTPUT_TOKENS_BOUNDARY

        return _MAX_OUTPUT_TOKENS_BOUNDARY

    @staticmethod
    def max_completions() -> int:
        """Group F: v1's forced `n = 1` (`_MAX_COMPLETIONS_PER_REQUEST`, R2.15 / R2.16)."""
        from gateway_v2.runtime.resources import _MAX_COMPLETIONS_PER_REQUEST

        return _MAX_COMPLETIONS_PER_REQUEST
