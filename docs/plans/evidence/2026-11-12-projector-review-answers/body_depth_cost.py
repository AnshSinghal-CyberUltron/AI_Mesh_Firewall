"""Cost of the projector's plan-body depth scan (`body_depth`) and of `validate_plan` around it.

Run from gateway_v2/ with the gateway_v2 venv:
    python ../docs/plans/evidence/2026-11-12-projector-review-answers/body_depth_cost.py
Thread CPU, min and median of repeated runs. Prints nothing about the host; see the PR body.
"""
import json
import statistics
import time

from gateway_v2.domain.plan import StreamingMode
from gateway_v2.plan.document import PlanDocument, encode_plan_body
from state_control import projector
from tests.plan.test_lgw05c_delta import _draft


def plan_with(rules: int, pattern: str) -> str:
    body = encode_plan_body(PlanDocument("acme", StreamingMode.INCREMENTAL, (_draft(),)))
    first = body["rules"][0]
    body["rules"] = [
        {**first, "rule_id": f"r{n}", "condition": {
            "kind": "regex", "field": "", "direction": first["scope"], "keywords": [],
            "pattern": pattern, "detector_class": "", "pipeline_stage": ""}}
        for n in range(rules)
    ] or [first]
    return json.dumps(body)


def cost(fn, raw, repeat):
    samples = []
    for _ in range(repeat):
        start = time.thread_time_ns()
        fn(raw)
        samples.append((time.thread_time_ns() - start) / 1e3)
    return min(samples), statistics.median(samples)


cases = {
    "1-rule body (the test _doc)": (plan_with(0, ""), 2000),
    "5 regex rules": (plan_with(5, r"\b\d{3}-\d{2}-\d{4}\b"), 2000),
    "3,000 rules x 1,000-char pattern (B-S2 shape)": (plan_with(3000, "a|" * 499 + "b"), 5),
}
for name, (raw, repeat) in cases.items():
    d_min, d_med = cost(projector.body_depth, raw, repeat)
    v_min, v_med = cost(lambda r: projector.validate_plan("acme", r), raw, max(repeat // 10, 3))
    print(f"{name}: {len(raw):,} chars | body_depth min {d_min:,.1f} us median {d_med:,.1f} us | "
          f"validate_plan min {v_min:,.1f} us median {v_med:,.1f} us | scan share "
          f"{100 * d_min / v_min:.1f}%")
