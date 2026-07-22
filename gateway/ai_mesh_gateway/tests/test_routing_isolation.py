"""Unit tests for routing isolation helpers."""

from ai_mesh_gateway.routing_isolation import (
    DISABLE_FIRST,
    KILL_SWITCH_TRUTH_MODE,
    build_isolation_audit_metadata,
    model_passes_hard_filters,
    resolve_compliant_fallback,
)


def test_kill_switch_truth_mode_locked():
    assert KILL_SWITCH_TRUTH_MODE == "per_request_redis"
    assert DISABLE_FIRST is True


def test_model_passes_hard_filters():
    model = {
        "model_name": "secure-model",
        "is_active": True,
        "data_sensitivity_level": "confidential",
        "compliance_tags": ["hipaa"],
    }
    assert model_passes_hard_filters(
        model,
        data_sensitivity="confidential",
        required_compliance=["hipaa"],
    )
    assert not model_passes_hard_filters(
        model,
        data_sensitivity="restricted",
        required_compliance=["hipaa"],
    )


def test_resolve_explicit_fallback_when_eligible():
    routing = [
        {
            "model_name": "primary",
            "is_active": True,
            "data_sensitivity_level": "public",
            "compliance_tags": [],
        },
        {
            "model_name": "backup",
            "is_active": True,
            "data_sensitivity_level": "public",
            "compliance_tags": [],
        },
    ]
    selected, code = resolve_compliant_fallback(
        primary_model="primary",
        requested_fallback="backup",
        routing_models=routing,
        fallback_chains=None,
        data_sensitivity="public",
    )
    assert selected == "backup"
    assert code == "explicit_ok"


def test_resolve_uses_precomputed_chain_when_explicit_ineligible():
    routing = [
        {
            "model_name": "primary",
            "is_active": True,
            "data_sensitivity_level": "confidential",
            "compliance_tags": ["hipaa"],
        },
        {
            "model_name": "backup",
            "is_active": True,
            "data_sensitivity_level": "public",
            "compliance_tags": [],
        },
        {
            "model_name": "safe",
            "is_active": True,
            "data_sensitivity_level": "confidential",
            "compliance_tags": ["hipaa"],
        },
    ]
    chains = {
        "version": 1,
        "per_primary": {"primary": ["safe"]},
        "chains": {"confidential|hipaa": ["primary", "safe"]},
    }
    selected, code = resolve_compliant_fallback(
        primary_model="primary",
        requested_fallback="backup",
        routing_models=routing,
        fallback_chains=chains,
        data_sensitivity="confidential",
        required_compliance=["hipaa"],
    )
    assert selected == "safe"
    assert code == "chain_next"


def test_resolve_catalog_scan_when_profile_chain_missing():
    """Single-tag request must still find eligible model via routing catalog scan."""
    routing = [
        {
            "model_name": "primary",
            "is_active": True,
            "data_sensitivity_level": "restricted",
            "compliance_tags": ["HIPAA", "SOC2"],
            "routing_priority": 5,
        },
        {
            "model_name": "backup",
            "is_active": True,
            "data_sensitivity_level": "restricted",
            "compliance_tags": ["HIPAA", "SOC2"],
            "routing_priority": 10,
        },
    ]
    chains = {
        "version": 1,
        "per_primary": {},
        "chains": {"restricted|HIPAA,SOC2": ["primary", "backup"]},
    }
    selected, code = resolve_compliant_fallback(
        primary_model="primary",
        requested_fallback="",
        routing_models=routing,
        fallback_chains=chains,
        data_sensitivity="restricted",
        required_compliance=["HIPAA"],
    )
    assert selected == "backup"
    assert code == "catalog_scan"


def test_audit_metadata_includes_evidence_fields():
    meta = build_isolation_audit_metadata(
        event_kind="kill_switch",
        scope="org_model",
        trigger_source="kill_switch",
        action="reroute",
        reason="high error rate",
        original_model="a",
        selected_model="b",
        data_sensitivity="confidential",
        compliance_tags=["hipaa"],
        fallback_chain_version=1,
        fallback_reason_code="chain_ok",
    )
    assert meta["kill_switch_truth_mode"] == "per_request_redis"
    assert meta["fallback_profile"] == "confidential|hipaa"
    assert meta["original_model"] == "a"
