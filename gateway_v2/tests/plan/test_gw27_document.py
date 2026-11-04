"""GW27 Task 3: the ``controls`` member of the signed plan body."""
from __future__ import annotations

import json
import random
from typing import Any

import pytest

from gateway_v2.domain.plan import (
    UNSET_CONTROLS,
    AuditControl,
    OrgControls,
    RateControl,
    StreamingMode,
)
from gateway_v2.plan.compiler import CompileError, compile_plan
from gateway_v2.plan.document import PlanDocument, decode_plan_body, encode_plan_body
from tests.plan.test_lgw05c_delta import _draft

FULL = OrgControls(
    rate=RateControl(True, 1000, 150),
    org_tpm_limit=5000,
    max_response_tokens=4096,
    audit=AuditControl(True, False, 90),
    compliance_frameworks=("ISO27001", "SOC2"),
)


def _body(controls: dict[str, Any] | None = None, **extra: object) -> bytes:
    body = encode_plan_body(PlanDocument("org-a", StreamingMode.INCREMENTAL, (_draft(),)))
    if controls is not None:
        body["controls"] = controls
    body.update(extra)
    return json.dumps(body).encode()


def _random_controls(rng: random.Random) -> OrgControls:
    frameworks = tuple(sorted({f"F{rng.randint(0, 40)}" for _ in range(rng.randint(0, 5))}))
    return OrgControls(
        rate=RateControl(rng.random() < 0.5, rng.randint(0, 10_000), rng.randint(0, 1000))
        if rng.random() < 0.7 else None,
        org_tpm_limit=rng.choice((0, rng.randint(1, 10**7))),
        max_response_tokens=rng.choice((0, rng.randint(1, 32_000))),
        audit=AuditControl(rng.random() < 0.5, rng.random() < 0.5, rng.randint(1, 365))
        if rng.random() < 0.7 else None,
        compliance_frameworks=frameworks,
    )


# Feature: gw27-firewall-config-fields, Property 4: decode(encode(d)) == d for random OrgControls
# Validates: Requirements 5.1
def test_property_round_trip() -> None:
    seed = 2704
    rng = random.Random(seed)
    for i in range(10_000):
        controls = _random_controls(rng)
        document = PlanDocument("org-a", StreamingMode.INCREMENTAL, (_draft(),), controls)
        raw = json.dumps(encode_plan_body(document), sort_keys=True).encode()
        assert decode_plan_body(raw) == document, (seed, i, controls)


# Feature: gw27-firewall-config-fields, Property 5: plans without controls hash as before
# Validates: Requirements 5.1
def test_property_hash_stability_and_sensitivity() -> None:
    seed = 2705
    rng = random.Random(seed)
    drafts = (_draft("a"), _draft("b"))
    bare = compile_plan("org-a", drafts, compiled_at=1.0)
    explicit = compile_plan("org-a", drafts, compiled_at=1.0, controls=UNSET_CONTROLS)
    assert bare.content_hash == explicit.content_hash
    for i in range(10_000):
        controls = _random_controls(rng)
        plan = compile_plan("org-a", drafts, compiled_at=1.0, controls=controls)
        if controls == UNSET_CONTROLS:
            assert plan.content_hash == bare.content_hash, (seed, i)
        else:
            assert plan.content_hash != bare.content_hash, (seed, i, controls)
        assert plan.controls == controls


def test_unset_controls_leave_the_body_byte_identical() -> None:
    body = encode_plan_body(PlanDocument("o", StreamingMode.INCREMENTAL, (_draft(),)))
    assert "controls" not in body and "requires" not in body


def test_set_controls_write_requires_gate() -> None:
    body = encode_plan_body(PlanDocument("o", StreamingMode.INCREMENTAL, (_draft(),), FULL))
    assert body["requires"] == ["controls/1"]
    assert body["controls"]["version"] == 1  # type: ignore[index]


@pytest.mark.parametrize(
    "controls",
    [
        {"version": 2},
        {"version": True},
        {"version": 1.0},
        {},
        {"version": 1, "surprise": 1},
        {"version": 1, "rate": {"enabled": True, "requests_per_minute": 1, "burst": 1, "x": 1}},
        {"version": 1, "rate": {"enabled": 1, "requests_per_minute": 1, "burst": 1}},
        {"version": 1, "rate": {"enabled": True, "requests_per_minute": True, "burst": 1}},
        {"version": 1, "rate": {"enabled": True, "requests_per_minute": -1, "burst": 1}},
        {"version": 1, "rate": {"enabled": True, "requests_per_minute": 1.5, "burst": 1}},
        {"version": 1, "rate": {"enabled": True, "requests_per_minute": 1}},
        {"version": 1, "org_tpm_limit": -1},
        {"version": 1, "org_tpm_limit": "5"},
        {"version": 1, "max_response_tokens": 1e999},
        {"version": 1, "audit": {"enabled": True, "incident_logging": True}},
        {"version": 1, "audit": {"enabled": True, "incident_logging": True, "retention_days": -3}},
        {"version": 1, "compliance": {"frameworks": []}},
        {"version": 1, "compliance": {"frameworks": ["A", "A"]}},
        {"version": 1, "compliance": {"frameworks": [""]}},
        {"version": 1, "compliance": {"frameworks": [1]}},
        {"version": 1, "compliance": {"frameworks": ["A"], "extra": 1}},
        {"version": 1, "compliance": "SOC2"},
        {"version": 1, "rate": []},
    ],
)
def test_strict_decode_rejects(controls: dict[str, Any]) -> None:
    raw = _body(controls).replace(b"Infinity", b"1e999")
    with pytest.raises(CompileError):
        decode_plan_body(raw)


def test_non_object_controls_rejected() -> None:
    body = encode_plan_body(PlanDocument("o", StreamingMode.INCREMENTAL, (_draft(),)))
    body["controls"] = [1]
    with pytest.raises(CompileError):
        decode_plan_body(json.dumps(body).encode())


_BAD_REQUIRES = [["controls/2"], ["controls/1", "future/1"], "controls/1", [1]]


@pytest.mark.parametrize("requires", _BAD_REQUIRES)
def test_unknown_requires_is_refused(requires: object) -> None:
    with pytest.raises(CompileError):
        decode_plan_body(_body(requires=requires))


def test_known_requires_is_accepted() -> None:
    document = decode_plan_body(_body({"version": 1}, requires=["controls/1"]))
    assert document.controls == UNSET_CONTROLS
