"""Smoke test: the spec'd V1 pipeline RUNS (only the layer contract is violated)."""
import asyncio
from gateway_v2.plan.model import (Action, Category, ExecutionPlan, FailurePosture, Mode,
                                   PlatformIntegrity, Rule, RuleScope, StreamingMode)
from gateway_v2.detect.base import Finding, FindingStatus
from gateway_v2.resolve.resolver import resolve
from gateway_v2.resolve.decision import Phase, mint_dispatch_authorization
from gateway_v2.dispatch.provider import require_authorization
from gateway_v2.audit.record import DecisionRecord

plan = ExecutionPlan("org-a", "v7", 0.0,
    (Rule("r-inj", Category.PROMPT_INJECTION, Mode.ENFORCE, Action.BLOCK, 0.5, 10, RuleScope.BOTH, FailurePosture.FAIL_CLOSED),),
    frozenset({"semantic.injection"}), StreamingMode.INCREMENTAL, PlatformIntegrity(True))
f = Finding("semantic.injection", "m@1", Category.PROMPT_INJECTION, FindingStatus.EXECUTED, 0.99, (), None)
d = resolve((f,), plan, Phase.INPUT)
print("disposition:", d.disposition, "deciding:", d.deciding_rules)
print("mint for BLOCK ->", mint_dispatch_authorization(d))
try:
    Finding("x", "v", "prompt-injection", FindingStatus.EXECUTED, 0.9, (), None)  # type: ignore[arg-type]
except TypeError as e:
    print("LGW04-3 runtime rejection needs the Category import at runtime:", e)
print("audit record:", DecisionRecord("req-1", Phase.INPUT, d, d.findings, True).decision.disposition)
