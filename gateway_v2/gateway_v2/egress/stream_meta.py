"""Egress stream vocabulary, split out of `egress/stream.py` (size gate; a pure move).

The exception vocabulary, the undetermined-plan sentinel and the per-text-stream holdback state
(`_Text`). `egress/stream.py` re-exports every name so existing imports keep resolving.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from gateway_v2.domain import ExecutionPlan, Finding, PlanUnavailable, PlanUnknownTenant

__all__ = (
    "UNDETERMINED",
    "EnforcementUndetermined",
    "HoldbackOverflow",
    "ScanFailure",
    "TradeOffUndefined",
    "Undetermined",
    "UpstreamPlan",
)

# --------------------------------------------------------------------------- #
# Exceptions (task 7.1).
# --------------------------------------------------------------------------- #


class HoldbackOverflow(Exception):
    """A relaxed-class hold reached the ``2 * hold_cap`` ceiling (R6.5).

    Filled in by task 9; defined here so the vocabulary is complete and the
    scanner's ``overflow`` signal has a home.
    """


class TradeOffUndefined(Exception):
    """An over-cap pattern has no declared trade-off outcome (R6.6). Task 9."""


class ScanFailure(Exception):
    """The injected scanner raised while scanning the pending buffer (R3.6)."""


class EnforcementUndetermined(Exception):
    """The plan could not be resolved before the first chunk (R2.4).

    Raised before any byte is read or released, so the stream is withheld
    entirely and the caller learns the output-enforcement state is undetermined.
    """


# --------------------------------------------------------------------------- #
# Undetermined-plan sentinel (task 7.2 gating).
# --------------------------------------------------------------------------- #


class Undetermined:
    """A sentinel the caller passes to ``for_plan`` when the plan is unresolved.

    Distinct from ``PlanUnavailable`` / ``PlanUnknownTenant`` (which the caller
    may also pass): any of the three means the enforcing-output state cannot be
    determined, so ``for_plan`` raises ``EnforcementUndetermined`` and the stream
    releases nothing (R2.4).
    """

    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover - trivial
        return "UNDETERMINED"


#: The shared undetermined sentinel instance.
UNDETERMINED = Undetermined()


#: What ``for_plan`` accepts for the pinned plan: a resolved ``ExecutionPlan`` or
#: one of the unresolved markers that force ``EnforcementUndetermined``.
UpstreamPlan = ExecutionPlan | PlanUnavailable | PlanUnknownTenant | Undetermined



# --------------------------------------------------------------------------- #
# Per-text-stream state (task 7.1).
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class _Text:
    """Mutable per-text-stream holdback state.

    ``pending`` is the unreleased buffer tail (held bytes). ``base_offset`` is the
    absolute index of ``pending[0]`` within the whole text stream, so a released
    slice can be given an absolute base offset for the ``Decision``.
    ``held_since_ns`` is the clock reading when the OLDEST currently-held byte
    first entered ``pending`` -- the ``oldest_arrival`` used for the holdback-wait
    decomposition. ``released`` counts bytes already written downstream.
    ``max_held_tokens`` / ``max_unbroken_run_bytes`` are the per-stream maxima
    folded into the histogram sample. ``hits`` accumulates completed findings
    (populated by the injected detector in task 9.1; empty in the thin harness).
    ``whole`` is the full accumulated text of the stream (every delta ever
    appended), so the injected detector can classify a match against the whole
    buffer -- essential for a match that straddles a release boundary or that
    completes only after the hold cap force-released its prefix (task 9.1).
    ``forced_release_end`` is the absolute index up to which bytes were released
    EARLY because the word-class token cap forced release past the minimal
    suffix; a completed match whose span starts before this index is a
    force-release trade-off (R6.2-R6.4).
    """

    pending: str = ""
    whole: str = ""
    base_offset: int = 0
    held_since_ns: int = 0
    released: int = 0
    forced_release_end: int = 0
    max_held_tokens: int = 0
    max_unbroken_run_bytes: int = 0
    hits: list[Finding] = field(default_factory=list)
    # GW33 D-23: per-CHANNEL findings of the stateless ``delta_detector`` (canary), span-deduped.
    delta_hits: list[Finding] = field(default_factory=list)


