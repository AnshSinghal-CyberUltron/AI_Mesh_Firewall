"""The per-request FPR-budget algebra for semantic detection (GW10).

The semantic model's published operating point is **per window, not per
request**, and the false-positive rate (FPR) compounds across the ``W`` windows
a long input is split into. Under the independence assumption the per-request
FPR is::

    request_fpr(per_window_fpr, W) = 1 - (1 - per_window_fpr) ** W

A model at ~1% per-window FPR therefore sees ~1.99% at two windows and ~6.79%
at seven. To hold the per-request FPR at or below a declared ceiling across
``W`` independent windows the per-window operating point must have a per-window
FPR of at most::

    per_window_fpr_budget(ceiling, W) = 1 - (1 - ceiling) ** (1 / W)

``derive_threshold`` inverts the independence formula: it returns the per-window
confidence threshold corresponding to that per-window FPR budget for the
declared model operating point, so the per-request FPR is ``<= ceiling`` **by
construction** for the declared per-window FPR. The derived threshold is
monotone (non-decreasing strictness) in ``W`` -- more windows means a smaller
per-window FPR budget means a stricter per-window threshold.

Everything here is a pure, deterministic function of its inputs: no clock, no
I/O, no mutation. The ``ceiling`` and the declared ``per_window_fpr`` are read
from the plan / configuration by the CALLER and passed in as arguments; they are
never dataclass-field numeric literals (R3.1, R3.3, R8.4). The real measured
per-window FPR curve is deferred to GW20/T01 (R11.1) -- the local half proves the
mechanism against a declared per-window FPR input.

When no per-window threshold can keep the per-request FPR within the ceiling
(the declared per-window FPR already exceeds the per-window budget), the
derivation records the ``ceiling_exceeded`` condition rather than silently
proceeding; the caller applies the declared behaviour -- default action FLAG per
C6 (R3.5, R11.5).

Layering: this module imports only the standard library (pure math). It never
imports a higher layer (``edge`` / ``admit`` / ``plan``).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

__all__ = (
    "FprBudget",
    "derive_threshold",
    "per_window_fpr_budget",
    "request_fpr",
)


@dataclass(frozen=True, slots=True)
class FprBudget:
    """The pure result of a per-request FPR-budget derivation.

    ``derived_threshold`` -- the per-window confidence threshold in ``[0.0, 1.0]``
        corresponding to the per-window FPR budget for ``W`` windows. A larger
        value is stricter (fewer firings), and the value is non-decreasing as
        ``W`` grows.
    ``ceiling_exceeded`` -- ``True`` when no per-window threshold at the declared
        per-window FPR can keep the per-request FPR at or below the ceiling (the
        declared per-window FPR already exceeds the per-window budget). The
        condition is recorded, never silently proceeded past (C6 FLAG, R3.5).
    """

    derived_threshold: float
    ceiling_exceeded: bool


def _validate_prob(name: str, value: float) -> None:
    """Reject a probability outside the open interval ``(0, 1)``."""

    if not math.isfinite(value) or not 0.0 < value < 1.0:
        raise ValueError(f"{name} must be a probability in (0, 1), got {value!r}")


def _validate_window_count(window_count: int) -> None:
    """Reject a non-positive window count."""

    if window_count < 1:
        raise ValueError(f"window_count must be >= 1, got {window_count!r}")


def request_fpr(per_window_fpr: float, window_count: int) -> float:
    """Per-request FPR implied by a per-window FPR over ``window_count`` windows.

    ``request_fpr(per_window_fpr, W) = 1 - (1 - per_window_fpr) ** W`` -- the
    independence formula. Pure and deterministic. ``per_window_fpr`` must be a
    probability in ``(0, 1)`` and ``window_count`` must be ``>= 1``.
    """

    _validate_prob("per_window_fpr", per_window_fpr)
    _validate_window_count(window_count)
    return 1.0 - float((1.0 - per_window_fpr) ** window_count)


def per_window_fpr_budget(ceiling: float, window_count: int) -> float:
    """The per-window FPR budget that holds the per-request FPR within ``ceiling``.

    ``per_window_fpr_budget(ceiling, W) = 1 - (1 - ceiling) ** (1 / W)`` -- the
    inverse of the independence formula. For ``W`` independent windows a
    per-window FPR at or below this value keeps ``request_fpr <= ceiling`` by
    construction. The budget is non-increasing as ``W`` grows (more windows means
    a tighter per-window budget). Pure and deterministic.
    """

    _validate_prob("ceiling", ceiling)
    _validate_window_count(window_count)
    return 1.0 - float((1.0 - ceiling) ** (1.0 / window_count))


def _threshold_for_fpr(target_fpr: float) -> float:
    """The per-window confidence threshold for a target per-window FPR.

    This is the declared, monotone operating-point mapping that inverts the
    model's per-window FPR curve into a confidence cut: a SMALLER target FPR
    demands a STRICTER (higher) threshold. The real measured curve is deferred to
    GW20/T01 (R11.1); the local half uses the canonical monotone-decreasing
    survival mapping ``threshold = 1 - target_fpr``, which is strictly decreasing
    in ``target_fpr`` and so makes the derived threshold non-decreasing in ``W``.

    Pure and deterministic; the result is clamped to ``[0.0, 1.0]``.
    """

    return min(1.0, max(0.0, 1.0 - target_fpr))


def derive_threshold(ceiling: float, per_window_fpr: float, window_count: int) -> FprBudget:
    """Derive the per-window confidence threshold for the per-request FPR budget.

    Inverts the independence formula: the per-window FPR budget for
    ``window_count`` windows is ``1 - (1 - ceiling) ** (1 / W)``; the derived
    per-window threshold is the confidence cut corresponding to that budget. The
    per-request FPR is ``<= ceiling`` by construction for the declared
    ``per_window_fpr`` (R3.2, R3.4), and the derived threshold is monotone
    (non-decreasing strictness) in ``window_count`` (R3.6).

    ``ceiling`` and ``per_window_fpr`` are supplied by the caller from the plan /
    configuration (R3.1, R3.3), both probabilities in ``(0, 1)``; ``window_count``
    is ``>= 1``.

    When the declared ``per_window_fpr`` already exceeds the per-window budget, no
    per-window threshold can keep the per-request FPR within the ceiling: the
    returned ``FprBudget`` has ``ceiling_exceeded=True`` and the threshold derived
    from the (tighter) budget, so the condition is recorded for the caller's
    declared behaviour (FLAG per C6) rather than silently proceeded past (R3.5,
    R11.5). Pure and deterministic.
    """

    _validate_prob("ceiling", ceiling)
    _validate_prob("per_window_fpr", per_window_fpr)
    _validate_window_count(window_count)

    budget = per_window_fpr_budget(ceiling, window_count)
    # The declared operating point can satisfy the budget only when its
    # per-window FPR is at or below the per-window budget. If it exceeds the
    # budget, no per-window threshold at the declared operating point keeps the
    # per-request FPR within the ceiling -> record ceiling_exceeded.
    ceiling_exceeded = per_window_fpr > budget
    derived_threshold = _threshold_for_fpr(budget)
    return FprBudget(derived_threshold=derived_threshold, ceiling_exceeded=ceiling_exceeded)
