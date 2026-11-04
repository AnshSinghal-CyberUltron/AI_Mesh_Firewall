"""Output-decision bookkeeping extracted from `egress/stream.py` (GW33 task 3.0).

Behaviour-preserving extraction: `StreamPipeline` sat AT the 800-line module cap, so the three
helpers that decide *what the audit sees* and *what a force-release owes* live here as free
functions over explicit state. Imports `domain` and sibling `egress` modules only (the
`egress -> detect` edge stays forbidden). GW33 later grows this module with the final-check merge
(`apply_final_check`) and the multi-channel aggregate.
"""
from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Protocol

from gateway_v2.domain import Decision, Finding, most_restrictive
from gateway_v2.egress.holdback_tradeoff import (
    TRADE_OFF_REDACT_REMAINDER,
    TRADE_OFF_TERMINATE,
    ForcedReleaseTerminate,
    derive_trade_off_outcome,
)
from gateway_v2.egress.output_guard import OutputResolver

__all__ = (
    "PublishStats",
    "TradeOffUndefined",
    "forced_release_remainder_edits",
    "kept_output_decision",
    "publish_output",
)


class TradeOffUndefined(Exception):
    """An over-cap pattern has no declared trade-off outcome (R6.6)."""


class PublishStats(Protocol):
    """The two stats fields the audit reads; `StreamStats` satisfies it structurally."""

    output_action: str | None
    output_decision: Decision | None


def kept_output_decision(current: Decision | None, decision: Decision) -> Decision:
    """The most restrictive output decision seen on this stream (a tie keeps the NEWER one)."""
    if current is not None:
        winner = most_restrictive((current.disposition, decision.disposition))
        if winner is not decision.disposition:
            return current
    return decision


def publish_output(stats: PublishStats, decision: Decision | None) -> None:
    """Copy the kept decision onto the stats the edge audits."""
    stats.output_decision = decision
    stats.output_action = "allow" if decision is None else decision.disposition.value


def forced_release_remainder_edits(
    hits: Sequence[Finding],
    forced_release_end: int,
    release_start: int,
    release_end: int,
    resolver: OutputResolver,
    note: Callable[[Decision], None],
) -> tuple[tuple[int, int], ...]:
    """Absolute remainder spans to redact for force-release trade-offs.

    A completed match whose span starts before ``forced_release_end`` had its prefix released early
    by the cap (R6.2). Its declared outcome applies: ``terminate`` raises ``ForcedReleaseTerminate``
    (R6.4); ``redact_remainder`` yields the still-held remainder span inside THIS slice to redact
    before any of it is written (R6.3; the prefix was already redacted at its own release point, so
    Released_Bytes never carries an unredacted match, Property 1); no derivable outcome raises
    ``TradeOffUndefined`` (R6.6, fail closed).
    """
    if forced_release_end <= 0 or not hits:
        return ()
    edits: list[tuple[int, int]] = []
    for finding in hits:
        for span in finding.spans:
            if span.start >= forced_release_end:
                continue  # prefix was not force-released; normal path handles it
            decision = resolver.decide((finding,))
            outcome = derive_trade_off_outcome(decision)
            if outcome == TRADE_OFF_TERMINATE:
                note(decision)
                raise ForcedReleaseTerminate(finding.detector)
            if outcome != TRADE_OFF_REDACT_REMAINDER:
                raise TradeOffUndefined(
                    f"over-cap pattern has no declared trade-off outcome "
                    f"(class={finding.detector})",
                )
            rem_start = max(span.start, release_start)
            rem_end = min(span.end, release_end)
            if rem_start < rem_end:
                edits.append((rem_start, rem_end))
    return tuple(edits)
