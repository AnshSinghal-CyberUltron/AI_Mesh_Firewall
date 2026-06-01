"""
Rollout verification matrix (plan Phase 6) encoded as executable checks.

Each case documents expected behavior for correctness, compliance, and resiliency.
"""

import pytest

from ai_mesh_gateway.routing_isolation import (
    KILL_SWITCH_TRUTH_MODE,
    resolve_compliant_fallback,
)


def _assert_sensitivity_isolation():
    routing = [
        {
            "model_name": "primary",
            "is_active": True,
            "data_sensitivity_level": "restricted",
            "compliance_tags": [],
        },
        {
            "model_name": "public-only",
            "is_active": True,
            "data_sensitivity_level": "public",
            "compliance_tags": [],
        },
    ]
    selected, _ = resolve_compliant_fallback(
        primary_model="primary",
        requested_fallback="public-only",
        routing_models=routing,
        fallback_chains=None,
        data_sensitivity="restricted",
    )
    return selected is None


VERIFICATION_MATRIX = [
    {
        "id": "V1_immediate_disable_truth",
        "description": "Kill-switch uses per-request Redis truth path",
        "assert_fn": lambda: KILL_SWITCH_TRUTH_MODE == "per_request_redis",
    },
    {
        "id": "V2_disable_first_no_compliant_fallback",
        "description": "Ineligible reroute target yields no model (disable-first)",
        "assert_fn": lambda: resolve_compliant_fallback(
            primary_model="p",
            requested_fallback="nonexistent",
            routing_models=[],
            fallback_chains=None,
        )[0] is None,
    },
    {
        "id": "V3_compliant_chain_honors_sensitivity",
        "description": "Fallback cannot downgrade data sensitivity",
        "assert_fn": _assert_sensitivity_isolation,
    },
]


@pytest.mark.parametrize("case", VERIFICATION_MATRIX, ids=[c["id"] for c in VERIFICATION_MATRIX])
def test_verification_matrix_case(case):
    assert case["assert_fn"](), case["description"]
