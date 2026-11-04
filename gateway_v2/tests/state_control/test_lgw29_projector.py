"""GW29 projector: org-model scopes behind the rollout flag, per-key switches still unsupported."""
from __future__ import annotations

import json
import logging

import pytest

from gateway_v2.domain.incident import isolation_scope
from gateway_v2.domain.state import StateKind
from state_control.incident_scopes import (
    MIN_GENERATION_ENV,
    PROJECT_FLAG_ENV,
    IncidentScopePolicy,
    is_isolation_scope,
    org_model_projection,
    policy_from_env,
)
from state_control.projector import Projector, desired_state
from state_control.source import MemorySource
from tests.state_control.test_gw36_s2_projector import KNOBS, Lab
from tests.state_control.test_lgw05c_writer import Gateway

ON = IncidentScopePolicy(flag_on=True, declared_generation=2)


def _source() -> MemorySource:
    source = MemorySource()
    source.org("acme")
    source.org("other")
    return source


def _engaged(source: MemorySource, policy: IncidentScopePolicy | None):  # type: ignore[no-untyped-def]
    return desired_state(source.read(), policy)


@pytest.mark.parametrize(("policy", "projected"), [
    (None, False),
    (IncidentScopePolicy(), False),
    (IncidentScopePolicy(flag_on=True), False),
    (IncidentScopePolicy(flag_on=True, declared_generation=1), False),
    (IncidentScopePolicy(flag_on=False, declared_generation=2), False),
    (IncidentScopePolicy(flag_on=True, declared_generation=2), True),
    (IncidentScopePolicy(flag_on=True, declared_generation=3), True),
])
def test_org_model_scopes_need_the_flag_and_the_generation_declaration(
    policy: IncidentScopePolicy | None, projected: bool,
) -> None:
    source = _source()
    source.switch("acme", model_name="GPT-4o")
    desired = _engaged(source, policy)
    if projected:
        assert desired.switches_on == {"om:acme:gpt-4o"}
        assert desired.unsupported_switches == 0
    else:
        assert desired.switches_on == frozenset()
        assert desired.unsupported_switches == 1
        assert any("org-model scope" in note for note in desired.notes)
        assert all("(GW29)" not in note for note in desired.notes)


def test_org_wide_switches_are_unchanged_by_the_flag() -> None:
    source = _source()
    source.switch("acme")
    assert _engaged(source, None).switches_on == _engaged(source, ON).switches_on == {"org:acme"}


def test_per_key_credential_switches_stay_unsupported_even_when_enabled() -> None:
    source = _source()
    source.switch("acme", api_key_prefix="abcd1234")
    source.switch("acme", model_name="gpt-4o", api_key_prefix="abcd1234")
    desired = _engaged(source, ON)
    assert desired.switches_on == frozenset() and desired.unsupported_switches == 2
    assert all("NOT enforced by gateway v2" in n and "(GW29)" not in n for n in desired.notes)


def test_non_disable_actions_and_global_switches_stay_unsupported() -> None:
    source = _source()
    source.switch("acme", model_name="gpt-4o", action="reroute")
    source.switch(None, model_name="gpt-4o")
    desired = _engaged(source, ON)
    assert desired.switches_on == frozenset() and desired.unsupported_switches == 2


@pytest.mark.parametrize("name", ["bad name", "a" * 200, "naïve", "x\x00y", "a;b"])
def test_an_invalid_model_name_is_counted_and_never_projected(name: str) -> None:
    source = _source()
    source.switch("acme", model_name=name)
    desired = _engaged(source, ON)
    assert desired.switches_on == frozenset() and desired.invalid_names == 1
    assert desired.unsupported_switches == 0


def test_a_switch_for_an_inactive_org_projects_nothing() -> None:
    source = _source()
    source.switch("ghost", model_name="gpt-4o")
    assert _engaged(source, ON).switches_on == frozenset()


def test_the_projection_helper_matches_the_gateway_grammar() -> None:
    assert org_model_projection("acme", " GPT-4o ") == "om:acme:gpt-4o"
    assert org_model_projection("bad slug", "gpt-4o") is None
    assert org_model_projection("acme", "") is None
    assert is_isolation_scope(isolation_scope("gpt-4o")) and not is_isolation_scope("org:a")


def test_policy_from_env() -> None:
    assert policy_from_env({}) == IncidentScopePolicy()
    assert policy_from_env({PROJECT_FLAG_ENV: "1", MIN_GENERATION_ENV: "2"}).enabled
    assert policy_from_env({PROJECT_FLAG_ENV: "TRUE", MIN_GENERATION_ENV: "x"}).refusal
    assert not policy_from_env({PROJECT_FLAG_ENV: "0", MIN_GENERATION_ENV: "2"}).enabled
    assert policy_from_env({PROJECT_FLAG_ENV: "yes"}).refusal is not None
    assert policy_from_env({}).refusal is None


def test_flag_on_without_the_declaration_logs_an_error_with_the_remediation(
    caplog: pytest.LogCaptureFixture,
) -> None:
    lab = Lab()
    with caplog.at_level(logging.ERROR, logger="amf.state.projector"):
        Projector(lab.db, lab.source, lab.writer, holder="a", knobs=KNOBS,
                  incident_scopes=IncidentScopePolicy(flag_on=True))
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert MIN_GENERATION_ENV in text and "NOT written" in text


def _lab(policy: IncidentScopePolicy) -> Lab:
    lab = Lab()
    lab.projector = Projector(lab.db, lab.source, lab.writer, holder="a", knobs=KNOBS,
                              incident_scopes=policy)
    return lab


def test_an_org_model_switch_is_projected_read_and_cleared_with_an_explicit_off() -> None:
    lab = _lab(ON)
    lab.org("acme")
    lab.source.switch("acme", model_name="GPT-4o")
    summary = lab.projector.round_once()
    assert summary.unsupported_switches == 0 and summary.heartbeat
    assert lab.db.snapshot(StateKind.KS)[2] == ("om:acme:gpt-4o",)
    gateway = Gateway(lab.store)
    gateway.poll(StateKind.KS)
    evaluation = gateway.switches.evaluate("acme", "gpt-4o")
    assert evaluation.org_model_engaged and not evaluation.model_engaged
    assert not gateway.switches.evaluate("other", "gpt-4o").engaged, "another org is unaffected"
    assert not gateway.switches.evaluate("acme", "gpt-4o-mini").engaged

    lab.source.switch("acme", model_name="GPT-4o", engaged=False)
    lab.projector.round_once()
    record = next(r for r in lab.db.snapshot(StateKind.KS)[1] if r.key == "om:acme:gpt-4o")
    assert json.loads(record.body) == {"on": False}, "an explicit OFF record, not an absence"
    assert lab.db.snapshot(StateKind.KS)[2] == (), "on_count honest: nothing engaged"
    gateway.poll(StateKind.KS)
    assert not gateway.switches.evaluate("acme", "gpt-4o").engaged


def test_flag_off_projects_nothing_new_and_says_so() -> None:
    lab = _lab(IncidentScopePolicy())
    lab.org("acme")
    lab.source.switch("acme", model_name="gpt-4o")
    summary = lab.projector.round_once()
    assert summary.unsupported_switches == 1
    assert lab.db.snapshot(StateKind.KS)[1] == (), "no unknown scope is handed to an unrolled fleet"


def test_the_projector_never_clears_an_isolation_record() -> None:
    lab = _lab(ON)
    lab.org("acme")
    lab.writer.isolation("gpt-4o", on=True, fallback="gpt-4o-mini", reason="ops",
                         expires_at=9_999_999_999.0)
    for _ in range(2):
        lab.projector.round_once()
    assert lab.db.snapshot(StateKind.KS)[2] == (isolation_scope("gpt-4o"),)
    gateway = Gateway(lab.store)
    gateway.poll(StateKind.KS)
    assert gateway.switches.isolation_record("gpt-4o") is not None
