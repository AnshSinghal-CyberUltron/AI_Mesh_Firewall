"""Compose the per-request output detectors from the pinned plan (GW33 C10, D-22, D-23, D-28, D-32).

`build_output_detectors` reads `builtin_output_detector(rule)` for the plan's non-OFF OUTPUT/BOTH
rules (customer rules and category labels never select a detector) and constructs ONLY the matching
detectors from `contract.output_bounds()` (D-30). Each detector is stamped with the platform
`rule_id` that selected it (D-32), so the resolver joins by identity.

Assignment by mode (D-23):
* INCREMENTAL (and any plan with no enforcing output rule): `per_delta` = the existing GW09
  detector; `canary_delta` = the STATELESS overlap scan; `final` = IP + exfil (flag-only) + the
  full-buffer canary scan + grounding, run once after the last chunk.
* STRICT_WITHHOLD with an enforcing output rule: `sync_whole` = existing + IP + exfil + canary
  (+ the legacy IP shim); `final` = grounding only. Nothing is released before the decision.
A plan with no new family returns the existing detector unchanged and no extra hooks, so behaviour
without new categories is byte-identical (requirements 2.9, 8.7).

D-2 legacy shim: the shipped GW26 IP rule (`fw-exfil-` ids, category `exfil`, no condition) is INERT
in INCREMENTAL (exactly as before this card) and, in STRICT, is fed the IP detector's findings
re-labelled `EXFIL` and stamped with the legacy rule's id. `legacy_rule_ids` is exposed so the
caller can count and warn once per plan install.
"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, replace

from gateway_v2.detect.guard.backend import GuardBackend
from gateway_v2.detect.output.canary import CanaryDetector
from gateway_v2.detect.output.common import unavailable
from gateway_v2.detect.output.exfil import ExfilDetector
from gateway_v2.detect.output.grounding import GroundingDetector
from gateway_v2.detect.output.ip_leak import IpLeakDetector
from gateway_v2.domain import (
    LEGACY_IP_RULE_PREFIX,
    Action,
    Category,
    ExecutionPlan,
    Finding,
    GroundingMode,
    Mode,
    Rule,
    RuleScope,
    StreamingMode,
    builtin_output_detector,
)
from gateway_v2.edge.request_holds import CanaryTicketLike
from gateway_v2.egress.output_guard import FinalDetector, enforcing_output_rules
from gateway_v2.egress.strict import SyncRunner
from gateway_v2.runtime.resources import ResourceContract

__all__ = (
    "OutputDetectors",
    "build_output_detectors",
    "legacy_ip_rules",
    "needs_output_detectors",
)

Detect = Callable[[str], Sequence[Finding]]
DeltaDetect = Callable[[str, int], Sequence[Finding]]
_DEFAULT_THRESHOLD = 0.5


@dataclass(frozen=True, slots=True)
class OutputDetectors:
    """What the pipeline selected by `plan.streaming_mode` is given (D-22)."""

    per_delta: Detect | None = None
    canary_delta: DeltaDetect | None = None
    sync_whole: Detect | None = None
    final: FinalDetector | None = None
    runner: SyncRunner | None = None
    legacy_rule_ids: tuple[str, ...] = ()
    #: Non-streaming parity: the sync detectors over the whole collected channel. Nothing is
    #: released before the decision there, so it scans what STRICT scans (grounding excluded).
    whole_response: Detect | None = None

    def detector_for(self, mode: StreamingMode) -> Detect | None:
        """The `detector=` seam of the pipeline that `mode` selects."""
        return self.sync_whole if mode is StreamingMode.STRICT_WITHHOLD else self.per_delta


def _output_rules(plan: ExecutionPlan) -> tuple[Rule, ...]:
    scopes = (RuleScope.OUTPUT, RuleScope.BOTH)
    return tuple(r for r in plan.rules if r.mode is not Mode.OFF and r.scope in scopes)


def legacy_ip_rules(plan: ExecutionPlan) -> tuple[Rule, ...]:
    """The shipped condition-less GW26 IP rules (D-2): category `exfil`, `fw-exfil-` id."""
    return tuple(
        r for r in _output_rules(plan)
        if r.condition is None
        and r.category is Category.EXFIL
        and r.rule_id.startswith(LEGACY_IP_RULE_PREFIX)
    )


def _guard(fn: Detect, rule_id: str, category: Category) -> Detect:
    """D-18: a composer-level failure is ONE UNAVAILABLE finding, never an exception."""
    def run(text: str) -> Sequence[Finding]:
        try:
            return tuple(fn(text))
        except Exception:  # noqa: BLE001
            return (unavailable(rule_id, "output-composer", category),)
    return run


def _chain(*parts: Detect | None) -> Detect | None:
    live = tuple(p for p in parts if p is not None)
    if not live:
        return None
    if len(live) == 1:
        return live[0]

    def run(text: str) -> Sequence[Finding]:
        out: list[Finding] = []
        for part in live:
            out.extend(part(text))
        return tuple(out)
    return run


def _relabel(fn: Detect, rule_id: str) -> Detect:
    def run(text: str) -> Sequence[Finding]:
        return tuple(
            replace(f, detector=rule_id, category=Category.EXFIL) for f in fn(text)
        )
    return run


def _grounding_mode(rule: Rule) -> GroundingMode:
    assert rule.condition is not None
    suffix = rule.condition.detector_class.split(".", 2)[2:]
    head = suffix[0].split(":", 1)[0] if suffix else ""
    return GroundingMode.MODEL if head == GroundingMode.MODEL.value else GroundingMode.LEXICAL


def needs_output_detectors(plan: ExecutionPlan) -> bool:
    """True when the plan selects a built-in output detector or carries a legacy IP rule."""
    has_family = any(builtin_output_detector(r) is not None for r in _output_rules(plan))
    return has_family or bool(legacy_ip_rules(plan))


def build_output_detectors(
    plan: ExecutionPlan,
    *,
    base: Detect | None,
    ticket: CanaryTicketLike | None,
    context_chunks: tuple[str, ...],
    backend: GuardBackend | None,
    contract: ResourceContract,
    runner: SyncRunner | None,
) -> OutputDetectors:
    """Build the per-request detector set; the identity `OutputDetectors(base, ...)` when none."""
    rules = _output_rules(plan)
    by_family: dict[str, Rule] = {}
    for rule in rules:
        family = builtin_output_detector(rule)
        if family is not None and family not in by_family:
            by_family[family] = rule
    legacy = legacy_ip_rules(plan)
    if not by_family and not legacy:
        return OutputDetectors(per_delta=base, sync_whole=base, whole_response=base)
    bounds = contract.output_bounds()
    ip = exfil = canary = None
    if "ip_leak" in by_family:
        rule = by_family["ip_leak"]
        ip = _guard(
            IpLeakDetector(bounds=bounds, rule_id=rule.rule_id).detect, rule.rule_id,
            Category.IP_LEAK,
        )
    if "exfil" in by_family:
        rule = by_family["exfil"]
        assert rule.condition is not None
        exfil = _guard(
            ExfilDetector(
                bounds=bounds, allow_hosts=frozenset(rule.condition.allow_hosts),
                rule_id=rule.rule_id,
            ).detect,
            rule.rule_id, Category.EXFIL,
        )
    canary_det: CanaryDetector | None = None
    if "canary" in by_family and ticket is not None:  # no ticket: canary_checked stays False
        rule = by_family["canary"]
        canary_det = CanaryDetector(ticket.reveal(), rule_id=rule.rule_id)
        canary = _guard(canary_det.detect, rule.rule_id, Category.CANARY)
    grounding = _grounding(by_family.get("grounding"), context_chunks, backend, contract)
    enforcing = bool(enforcing_output_rules(plan))
    if plan.streaming_mode is StreamingMode.STRICT_WITHHOLD and enforcing:
        legacy_dets = tuple(
            _relabel(IpLeakDetector(bounds=bounds, rule_id=r.rule_id).detect, r.rule_id)
            for r in legacy
        )
        whole = _chain(base, ip, exfil, canary, *legacy_dets)
        return OutputDetectors(
            sync_whole=whole,
            whole_response=whole,
            final=_final(None, grounding, contract),
            runner=runner,
            legacy_rule_ids=tuple(r.rule_id for r in legacy),
        )
    canary_rule = by_family.get("canary")
    delta: DeltaDetect | None = None
    armed = canary_rule is not None and canary_rule.action is not Action.ALLOW
    if canary_det is not None and armed:
        delta = _canary_delta(canary_det, contract.canary_scan_overlap_chars())
    return OutputDetectors(
        per_delta=base,
        canary_delta=delta,
        final=_final(_chain(ip, exfil, canary), grounding, contract, runner),
        runner=runner,
        legacy_rule_ids=tuple(r.rule_id for r in legacy),
        whole_response=_chain(base, ip, exfil, canary),
    )


def _canary_delta(detector: CanaryDetector, overlap: int) -> DeltaDetect:
    """D-23: stateless; scans from `previous_len - (overlap - 1)` so a mid-delta canary is seen."""
    def run(whole: str, previous_len: int) -> Sequence[Finding]:
        return detector.detect_from(whole, max(0, previous_len - (overlap - 1)))
    return run


def _grounding(
    rule: Rule | None,
    context_chunks: tuple[str, ...],
    backend: GuardBackend | None,
    contract: ResourceContract,
) -> GroundingDetector | None:
    if rule is None:
        return None
    threshold = _DEFAULT_THRESHOLD if rule.threshold is None else rule.threshold
    return GroundingDetector(
        mode=_grounding_mode(rule),
        context_chunks=context_chunks,
        threshold=threshold,
        max_input_chars=contract.grounding_max_input_chars(),
        backend=backend,
        guard_budget_s=contract.grounding_wait_budget_s(),
        rule_id=rule.rule_id,
    )


def _final(
    sync: Detect | None,
    grounding: GroundingDetector | None,
    contract: ResourceContract,
    runner: SyncRunner | None = None,
) -> FinalDetector | None:
    """One async whole-channel check: sync detectors off-loop, grounding under a wait budget."""
    if sync is None and grounding is None:
        return None
    wait = contract.grounding_wait_budget_s()

    async def final(channel: str, whole: str) -> Sequence[Finding]:
        findings: list[Finding] = []
        if sync is not None:
            check = sync
            findings.extend(
                await runner(lambda: check(whole)) if runner is not None else check(whole),
            )
        if grounding is not None:
            findings.extend(await _bounded(grounding, whole, wait))
        return tuple(findings)

    return final


async def _bounded(grounding: GroundingDetector, whole: str, wait: float) -> Sequence[Finding]:
    awaitable: Awaitable[tuple[Finding, ...]] = grounding.adetect(whole)
    try:
        return await asyncio.wait_for(awaitable, timeout=wait)
    except Exception:  # noqa: BLE001 - a timeout or backend failure is one UNAVAILABLE (D-22)
        return (unavailable(grounding.rule_id, "output-composer", Category.HALLUCINATION),)
