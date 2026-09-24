"""Output detect -> resolve -> emit — rb.md L2151, L2159, L2833."""

from __future__ import annotations

from typing import TYPE_CHECKING

from collections.abc import Sequence

# L2151/L2159 'output detect -> resolve -> emit, same types'

# L2833 GW13 'Output uses resolve() with phase=OUTPUT. There is no enforce_output'
from gateway_v2.resolve.decision import Phase
from gateway_v2.resolve.resolver import resolve

if TYPE_CHECKING:
    from gateway_v2.detect.base import Detector
    from gateway_v2.plan.model import ExecutionPlan
    from gateway_v2.resolve.decision import Decision


def guard_text(text: str, detectors: Sequence[Detector], plan: ExecutionPlan) -> Decision:
    findings = tuple(f for d in detectors for f in d.detect(text, plan))
    return resolve(findings, plan, Phase.OUTPUT)
