"""Output-decision bookkeeping extracted from `egress/stream.py` (GW33 task 3.0, 3.5, 3.6).

`StreamPipeline` sat AT the 800-line module cap, so the helpers that decide *what the audit sees*
and *what a force-release owes* live here as free functions over explicit state. GW33 grows this
module with the multi-channel aggregate (`OutputAggregate`, `combine_decisions` for the audit
aggregate, never for bytes) and the INCREMENTAL final-check merge (`apply_final_check`, D-22).
Imports `domain` and sibling `egress` modules only (the `egress -> detect` edge stays forbidden).
"""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

from gateway_v2.domain import (
    Category,
    Decision,
    Disposition,
    Finding,
    FindingStatus,
    combine_decisions,
)
from gateway_v2.egress.holdback_tradeoff import (
    TRADE_OFF_REDACT_REMAINDER,
    TRADE_OFF_TERMINATE,
    ForcedReleaseTerminate,
    derive_trade_off_outcome,
)
from gateway_v2.egress.output_errors import FinalCheckUnapplied
from gateway_v2.egress.output_guard import FinalDetector, OutputBlocked, OutputResolver

__all__ = (
    "OutputAggregate",
    "WholeText",
    "PublishStats",
    "TradeOffUndefined",
    "apply_final_check",
    "bytes_action",
    "forced_release_remainder_edits",
    "publish_output",
)

_BYTES_RANK = ("allow", "flag", "redact", "rewrite", "block")


class TradeOffUndefined(Exception):
    """An over-cap pattern has no declared trade-off outcome (R6.6)."""


class PublishStats(Protocol):
    """The stats fields the audit reads; `StreamStats` / `StrictStats` satisfy it structurally."""

    output_action: str | None
    output_decision: Decision | None
    review_required: bool
    canary_leaked: bool
    bytes_action: str
    final_check_unavailable: int


class WholeText:
    """Per-channel accumulation for the PASS-THROUGH final check; inert unless a detector is wired.

    A flag-only IP/exfil/grounding rule holds nothing, but its check still needs the whole channel
    after the last chunk (D-9, D-22), so a pass-through run collects text only when asked to.
    """

    __slots__ = ("enabled", "parts")

    def __init__(self, enabled: bool) -> None:
        self.enabled = enabled
        self.parts: dict[str, str] = {}

    def add(self, deltas: Sequence[tuple[str, str]]) -> None:
        if self.enabled:
            for channel, delta in deltas:
                self.parts[channel] = self.parts.get(channel, "") + delta


def bytes_action(decision: Decision) -> str:
    """What the BYTES did (C13 L-1): ``rewrite`` if a rewrite edit survives, ``redact`` when a
    REWRITE disposition collapsed to redacts (D-7), else the disposition value."""
    if any(item.kind == "rewrite" for item in decision.transformations):
        return "rewrite"
    if decision.disposition is Disposition.REWRITE:
        return "redact"
    return decision.disposition.value


def _canary_executed(decision: Decision) -> bool:
    return any(
        f.category is Category.CANARY and f.status is FindingStatus.EXECUTED
        for f in decision.findings
    )


@dataclass(slots=True)
class OutputAggregate:
    """Per-run audit aggregate across released slices and channels (M-2).

    `decision` is built with `combine_decisions` (rules, codes and flags from every channel
    survive); BYTES are never taken from it. `canary_leaked` and `bytes_action` accumulate at the
    same site so a flag-only canary in one channel next to a redact in another still reports both.
    """

    decision: Decision | None = None
    canary_leaked: bool = False
    bytes_action: str = "allow"
    final_check_unavailable: int = 0

    def note(self, decision: Decision) -> None:
        self.decision = (
            decision if self.decision is None else combine_decisions(self.decision, decision)
        )
        self.canary_leaked = self.canary_leaked or _canary_executed(decision)
        action = bytes_action(decision)
        if _BYTES_RANK.index(action) > _BYTES_RANK.index(self.bytes_action):
            self.bytes_action = action


def publish_output(stats: PublishStats, agg: OutputAggregate) -> None:
    """Copy the aggregate onto the stats the edge audits.

    ``review_required`` is ``decision.review_required or final_check_unavailable`` (D-22): under
    ``FAIL_OPEN`` an UNAVAILABLE finding yields no per-finding disposition, so the `Decision` flag
    stays False by its own invariant and the forced flag lives only here.
    """
    decision = agg.decision
    stats.output_decision = decision
    stats.output_action = "allow" if decision is None else decision.disposition.value
    stats.review_required = (decision is not None and decision.review_required) or (
        agg.final_check_unavailable > 0
    )
    stats.canary_leaked = agg.canary_leaked
    stats.bytes_action = agg.bytes_action
    stats.final_check_unavailable = agg.final_check_unavailable


async def apply_final_check(
    wholes: Mapping[str, str],
    final_detector: FinalDetector | None,
    resolver: OutputResolver,
    agg: OutputAggregate,
) -> None:
    """Await the final detector once per channel, after the last chunk and before publish (D-22).

    INCREMENTAL only: the bytes are already out. UNAVAILABLE findings are reported (counter, forced
    review flag), never enforced. A BLOCK (a canary found only by the full-buffer scan, D-26)
    terminates with ``output_blocked``; a REDACT/REWRITE cannot be applied to released bytes and
    fails closed as ``final_check_unapplied``. FLAG and ALLOW are merged into the aggregate.
    """
    if final_detector is None:
        return
    for channel, whole in wholes.items():
        findings = tuple(await final_detector(channel, whole))
        usable = tuple(f for f in findings if f.status is not FindingStatus.UNAVAILABLE)
        if len(usable) != len(findings):
            agg.final_check_unavailable += 1
        if not usable:
            continue
        decision = resolver.decide(usable)
        if decision.disposition is Disposition.BLOCK:
            raise OutputBlocked(decision)  # the pipeline arm records it
        agg.note(decision)
        if decision.disposition in (Disposition.REDACT, Disposition.REWRITE):
            raise FinalCheckUnapplied("byte-altering final-check disposition in INCREMENTAL")


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
