"""Optional local-model REWRITE proposer (GW33 C11, D-5). STRICT_WITHHOLD path only.

The declared template is the baseline in every mode. A proposer may only substitute the
REPLACEMENT TEXT of a `Transformation` the resolver already produced (via `dataclasses.replace`,
kind and span untouched), and every proposal is untrusted: it is accepted only when non-empty,
bounded, and re-inspected to `Disposition.ALLOW`. Any miss (None, empty, too long, non-ALLOW,
exception, timeout) keeps the template for that span, so `apply_decision` and `verify_rewrites`
verify the model text exactly like the template. Counters only; the model text never enters logs,
evidence, audit or metric labels. Imports `domain` only.

The proposer is NEVER a provider: no generative local backend ships in this card (the offline tests
use a stub), and the existing `dispatch.authorized.RewriteBackend` is a classification seam that
cannot generate text, so it is deliberately left untouched.
"""
from __future__ import annotations

import asyncio
import dataclasses
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Protocol

from gateway_v2.domain import Decision, Disposition, Finding, Transformation
from gateway_v2.egress.output_guard import OutputResolver

__all__ = ("RefineStats", "RewriteProposer", "refine_rewrites")

_REWRITE = "rewrite"


class RewriteProposer(Protocol):
    """Local text generator. Untrusted output; see module docstring."""

    async def propose(self, original: str, template: str, max_chars: int) -> str | None: ...


@dataclass(frozen=True, slots=True)
class RefineStats:
    """Counters only (`rewrite_applied`, `rewrite_fallback_template`)."""

    applied: int
    fallback_template: int
    blocked: int = 0


async def _accept(
    proposer: RewriteProposer,
    original: str,
    template: str,
    reinspect: Callable[[str], Sequence[Finding]],
    resolver: OutputResolver,
    max_chars: int,
    timeout_s: float | None,
) -> str | None:
    try:
        proposal = await asyncio.wait_for(
            proposer.propose(original, template, max_chars), timeout=timeout_s,
        )
        if not proposal or len(proposal) > max_chars:
            return None
        verdict = resolver.decide(tuple(reinspect(proposal)))
    except Exception:  # untrusted component: every failure keeps the template
        return None
    return proposal if verdict.disposition is Disposition.ALLOW else None


async def refine_rewrites(
    text: str,
    decision: Decision,
    proposer: RewriteProposer | None,
    reinspect: Callable[[str], Sequence[Finding]],
    resolver: OutputResolver,
    max_chars: int,
    *,
    timeout_s: float | None = None,
    on_stats: Callable[[RefineStats], None] | None = None,
) -> Decision:
    """Swap template replacements for accepted model proposals; spans and kinds never change."""
    if proposer is None or not any(t.kind == _REWRITE for t in decision.transformations):
        return decision
    refined: list[Transformation] = []
    applied = fallback = 0
    for item in decision.transformations:
        if item.kind != _REWRITE:
            refined.append(item)
            continue
        original = text[item.span.start : item.span.end]
        proposal = await _accept(
            proposer, original, item.replacement, reinspect, resolver, max_chars, timeout_s,
        )
        if proposal is None:
            fallback += 1
            refined.append(item)
        else:
            applied += 1
            refined.append(dataclasses.replace(item, replacement=proposal))
    if on_stats is not None:
        on_stats(RefineStats(applied=applied, fallback_template=fallback))
    return dataclasses.replace(decision, transformations=tuple(refined))
