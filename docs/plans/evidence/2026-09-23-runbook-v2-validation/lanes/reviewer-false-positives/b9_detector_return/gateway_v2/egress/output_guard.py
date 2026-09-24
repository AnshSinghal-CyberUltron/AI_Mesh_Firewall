"""Output detect -> resolve -> emit, with detect/resolve INJECTED by edge/ (no upward import)."""

from __future__ import annotations

from dataclasses import dataclass

from gateway_v2.contracts.domain.decision import Decision
from gateway_v2.contracts.domain.plan import ExecutionPlan
from gateway_v2.contracts.domain.ports import DetectFn, ResolveFn
from gateway_v2.contracts.domain.taxonomy import Phase


@dataclass(frozen=True, slots=True)
class OutputGuard:
    detect: DetectFn
    resolve: ResolveFn

    def check(self, text: str, plan: ExecutionPlan) -> Decision:
        return self.resolve(self.detect(text, plan), plan, Phase.OUTPUT)
