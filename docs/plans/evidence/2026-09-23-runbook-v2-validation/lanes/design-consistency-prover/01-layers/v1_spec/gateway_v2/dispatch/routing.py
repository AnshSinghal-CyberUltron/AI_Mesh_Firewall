"""Deterministic selection from the plan — rb.md L2159, L2754."""

from __future__ import annotations

# L2754 'Routing is deterministic from the pinned plan'
from gateway_v2.plan.model import ExecutionPlan


def route_key(plan: ExecutionPlan) -> str:
    return f"{plan.org_id}@{plan.version}"
