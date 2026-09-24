"""Composition root: the ONLY place that wires detect/resolve/dispatch/egress/audit together."""

from __future__ import annotations

from collections.abc import AsyncIterator

from gateway_v2.audit.record import DecisionRecord
from gateway_v2.detect.base import Detector, Finding, run_detectors
from gateway_v2.dispatch.provider import open_authorized
from gateway_v2.contracts.domain.plan import ExecutionPlan
from gateway_v2.contracts.domain.ports import ProviderClient
from gateway_v2.egress.output_guard import OutputGuard
from gateway_v2.egress.stream import OutputContext, StreamPipeline
from gateway_v2.resolve.decision import Phase, mint_dispatch_authorization
from gateway_v2.resolve.resolver import resolve


async def handle(
    request_id: str,
    text: str,
    plan: ExecutionPlan,
    detectors: tuple[Detector, ...],
    provider: ProviderClient,
) -> tuple[DecisionRecord, AsyncIterator[bytes] | None]:
    findings = run_detectors(text, plan, detectors)
    decision = resolve(findings, plan, Phase.INPUT)
    record = DecisionRecord(request_id, Phase.INPUT, decision, decision.findings)
    auth = mint_dispatch_authorization(decision)
    if auth is None:
        return record, None

    def detect_fn(text: str, plan: ExecutionPlan) -> tuple[Finding, ...]:
        return run_detectors(text, plan, detectors)

    guard = OutputGuard(detect=detect_fn, resolve=resolve)
    upstream = open_authorized(provider, auth, text.encode())
    return record, StreamPipeline().run(upstream, OutputContext(plan, guard))


def _wire_probe(plan: ExecutionPlan) -> None:
    from gateway_v2.detect.evil_detector import EvilAnnotated, EvilRaises, EvilUnannotated

    run_detectors("x", plan, (EvilAnnotated(), EvilUnannotated(), EvilRaises()))
