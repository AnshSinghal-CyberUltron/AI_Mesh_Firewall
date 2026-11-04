"""GW27 Task 1: the org-controls value types are frozen, hashable and default to UNSET."""
from __future__ import annotations

import dataclasses

import pytest

from gateway_v2.domain.plan import (
    UNSET_CONTROLS,
    AuditControl,
    ExecutionPlan,
    OrgControls,
    RateControl,
    StreamingMode,
)


def test_unset_is_the_default_and_equal_to_a_fresh_instance() -> None:
    assert OrgControls() == UNSET_CONTROLS
    assert UNSET_CONTROLS.rate is None and UNSET_CONTROLS.audit is None


def test_value_types_are_frozen_and_hashable() -> None:
    controls = OrgControls(
        rate=RateControl(True, 1000, 150),
        org_tpm_limit=5,
        max_response_tokens=64,
        audit=AuditControl(True, False, 30),
        compliance_frameworks=("SOC2",),
    )
    assert hash(controls) == hash(dataclasses.replace(controls))
    with pytest.raises(dataclasses.FrozenInstanceError):
        controls.org_tpm_limit = 1  # type: ignore[misc]
    assert controls != UNSET_CONTROLS


def test_execution_plan_defaults_to_unset_controls_as_last_field() -> None:
    fields = [f.name for f in dataclasses.fields(ExecutionPlan)]
    assert fields[-1] == "controls"
    plan = ExecutionPlan(
        org_id="o", epoch=1, sequence=1, content_hash="h" * 64, compiled_at=0.0,
        rules=(), required_detectors=frozenset(), streaming_mode=next(iter(StreamingMode)),
        integrity_locked=False,
    )
    assert plan.controls is UNSET_CONTROLS
