"""Deterministic selection from the plan — rb.md L2159, L2754."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from gateway_v2.plan.model import ExecutionPlan

# L2754 'Routing is deterministic from the pinned plan'


def route_key(plan: ExecutionPlan) -> str:
    return f"{plan.org_id}@{plan.version}"
