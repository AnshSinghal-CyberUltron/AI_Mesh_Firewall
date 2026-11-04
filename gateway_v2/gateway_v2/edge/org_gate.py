"""GW27 phase 5b: the org rate / burst / TPM gate and the org response-token ceiling.

One helper so ``openai_chat.py`` and ``openai_misc.py`` (embeddings) share the same order and the
same refund binding. The check runs after admission and before the per-key GCRA and the budget
lease, so a rate-limited request never touches the budget. It performs no I/O.
"""
from __future__ import annotations

from gateway_v2.admit.grant import BudgetVerdict
from gateway_v2.domain.plan import ExecutionPlan
from gateway_v2.edge.chat_deps import OrgLimitDecider
from gateway_v2.edge.request_holds import RequestHolds
from gateway_v2.runtime.resources import ResourceContract

__all__ = ("enforce_org_limits", "org_response_ceiling")


def enforce_org_limits(
    org_limits: OrgLimitDecider,
    plan: ExecutionPlan,
    org_id: str,
    cost: int,
    holds: RequestHolds,
    request_id: str,
) -> BudgetVerdict | None:
    """``None`` admits (and binds the TPM refund to ``holds``); a verdict refuses with a 429."""
    verdict = org_limits.check(org_id, plan.controls, cost, request_id=request_id)
    if verdict is None:
        holds.org_charged(org_id, cost, org_limits.refund_tokens)
    return verdict


def org_response_ceiling(plan: ExecutionPlan, contract: ResourceContract) -> int:
    """The org's ``max_response_tokens`` when the plan carries one, else the contract default.

    Callers still bound it by ``contract.max_output_tokens()``.
    """
    return plan.controls.max_response_tokens or contract.default_response_token_ceiling()
