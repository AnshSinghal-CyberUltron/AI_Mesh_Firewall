"""GW35 negative controls: put each defect back and prove the named test fails for it.

Each control asserts the failing exception type, so a vacuous pass is impossible.
"""

from __future__ import annotations

import asyncio

import pytest

from gateway_v2.edge import audit_emit, console_stages
from gateway_v2.edge.console_stages import StageOutcome
from tests.edge import _gw35_harness as h
from tests.edge import test_gw35_properties as props
from tests.edge import test_gw35_refusal_traces as refusals


def test_control_a_a_literal_allow_for_auth_and_rate_limit_is_caught(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real = console_stages.build_stages

    def legacy(facts):  # type: ignore[no-untyped-def]
        stages = list(real(facts))
        stages[0] = StageOutcome("auth", "allow", True)
        stages[1] = StageOutcome("rate_limit", "allow", True)
        return tuple(stages)

    monkeypatch.setattr(console_stages, "build_stages", legacy)
    with pytest.raises(AssertionError):
        props.test_p1_a_refusal_stops_every_later_stage()
    # The legacy stage fails schema validation, so the console is dropped: no trace key at all.
    with pytest.raises((AssertionError, KeyError)):
        refusals.test_a_bad_key_shows_auth_block_and_nothing_else_ran()


def test_control_b_treating_every_failure_as_policy_is_caught(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(console_stages, "is_operational_failure", lambda _code: False)
    with pytest.raises(AssertionError):
        props.test_p2_error_iff_provider_failed_and_no_block()
    # The builder is imported by name into the module, so patch the property's view as well.
    monkeypatch.setattr(props, "is_operational_failure", lambda _code: False)
    with pytest.raises(AssertionError):
        refusals.test_upstream_failure_is_error_not_an_output_block("upstream_error")


def test_control_c_the_legacy_audit_org_drops_a_failed_credential(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(audit_emit, "_audit_org", lambda holds, code, route: holds.org_id)
    app, records, _ = h.build_app(h.ScriptedProvider(h.ok_events()))
    res = asyncio.run(h.post(app, records, auth="Bearer wrong"))
    assert res.status == 401 and res.records == []
    from tests.edge.test_gw35_failed_credential_audit import (
        test_a_bad_key_is_one_rejected_row_under_the_reserved_org as failed_credential,
    )

    with pytest.raises(AssertionError):
        failed_credential()
