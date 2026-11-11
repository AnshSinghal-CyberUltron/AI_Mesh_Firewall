"""GW27: every formerly unsupported FirewallConfig field has one signed disposition."""
from __future__ import annotations

import json
import random

import pytest

from tests.plan._gw27_control import EVIDENCE, load

_FIVE_ACTION_FIELDS = (
    "output_pii_action",
    "output_credential_action",
    "output_ip_leakage_action",
    "output_policy_action",
    "output_hallucination_action",
)


#: GW33 moved these from retired to mapped (grounding is built; the action takes ``rewrite``).
_GW33_BUILT = (
    "factuality_check_enabled",
    "hallucination_grounding_mode",
    "hallucination_grounding_threshold",
    "hallucination_grounding_model",
    "output_hallucination_action",
)


def _snapshot() -> list[dict[str, object]]:
    return json.loads((EVIDENCE / "pre-change-unsupported.json").read_text(encoding="utf-8"))


def _ledger() -> dict[str, object]:
    return json.loads((EVIDENCE / "retire-list.json").read_text(encoding="utf-8"))


# Feature: gw27-firewall-config-fields, Property 1: 15 built, 25 retired, 1 delegated
# (GW33 built the five grounding/hallucination entries, so 30 retired became 25 + 5 mapped)
# Validates: Requirements 1.1
def test_property_total_disposition() -> None:
    mod = load().map
    kinds = {name: spec["kind"] for name, spec in mod.FIELDS.items()}
    assert set(kinds.values()) == {"identity", "mapped", "built", "retired"}
    assert "unsupported" not in kinds.values()
    snapshot = [row["field"] for row in _snapshot()]
    assert len(snapshot) == 46 == len(set(snapshot))
    built = {n for n, k in kinds.items() if k == "built"}
    retired = {n for n, k in kinds.items() if k == "retired"} - set(mod.DELEGATED)
    assert (len(built), len(retired), len(mod.DELEGATED)) == (15, 25, 1)
    gw33_built = set(_GW33_BUILT)
    assert gw33_built.isdisjoint(retired)
    assert all(mod.FIELDS[n]["kind"] == "mapped" for n in gw33_built)
    # nothing dropped, nothing new
    assert built | retired | set(mod.DELEGATED) | gw33_built == set(snapshot)
    assert mod.DELEGATED == ("org_token_budget",)
    assert mod.FIELDS["org_token_budget"]["owner"] == "GW36"


def test_every_retired_entry_has_an_owner_a_reason_and_keeps_its_off_values() -> None:
    mod = load().map
    before = {row["field"]: row for row in _snapshot()}
    for name, spec in mod.FIELDS.items():
        if spec["kind"] != "retired":
            continue
        assert spec["owner"].strip() and spec["reason"].strip(), name
        old_off = before[name]["off"]
        assert (list(spec["off"]) if "off" in spec else None) == old_off, name


def test_the_committed_ledger_equals_the_map() -> None:
    mod = load().map
    ledger = _ledger()
    assert ledger["retired"] == mod.retire_ledger()
    assert ledger["delegated_outside_45"] == mod.delegated_ledger() == ["org_token_budget"]
    built = {row["field"]: row["via"] for row in ledger["built"]}  # type: ignore[union-attr]
    assert set(built) == {row["field"] for row in mod.built_ledger()}
    for row in mod.built_ledger():
        assert built[row["field"]].startswith(row["via"] + ":"), row
    exclusions = {row["surface"] for row in ledger["surface_exclusions"]}  # type: ignore[union-attr]
    assert exclusions == {"mcp", "moderations"}
    text = json.dumps(ledger["built"])
    for needle in ("per gateway replica", "estimated tokens", "case-insensitive substring",
                   "content-score floor"):
        assert needle in text.replace("admission-time estimate", "estimated tokens"), needle


def test_a_retired_refusal_names_the_field_and_its_owner() -> None:
    mod = load().map
    errors = mod.selection_errors({"routing_enabled": True}, {"routing_enabled": False})
    assert set(errors) == {"routing_enabled"}
    assert "routing_enabled" in errors["routing_enabled"] and "GW28" in errors["routing_enabled"]
    assert "retired and not enforced" in errors["routing_enabled"]


def test_an_unchanged_retired_value_and_a_switch_to_off_are_accepted() -> None:
    mod = load().map
    stored = {"rag_enabled": True, "routing_enabled": True, "default_model": "x"}
    assert mod.selection_errors({**stored}, stored) == {}
    assert mod.selection_errors({"rag_enabled": False}, stored) == {}  # an `off` value


@pytest.mark.parametrize("field", _FIVE_ACTION_FIELDS)
def test_rewrite_is_refused_for_all_five_action_fields_unless_the_org_is_served(field: str) -> None:
    mod = load().map
    errors = mod.selection_errors({field: "rewrite"}, {field: "redact"})
    assert set(errors) == {field}
    if field == "output_policy_action":
        assert "GW33" in errors[field]  # no template path: refused for every org, naming its owner
    else:
        assert "only enforced on the v2 gateway" in errors[field]
        served = mod.selection_errors({field: "rewrite"}, {field: "redact"}, "acme")
        assert set(served) == {field}  # no org is listed by default
def test_rewrite_is_accepted_for_the_four_mapped_fields_when_the_org_is_listed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mod = load().map
    monkeypatch.setenv("AMF_V2_REWRITE_ORGS", "acme")
    for field in _FIVE_ACTION_FIELDS:
        errors = mod.selection_errors({field: "rewrite"}, {field: "redact"}, "acme")
        assert set(errors) == ({field} if field == "output_policy_action" else set()), field
        assert set(mod.selection_errors({field: "rewrite"}, {field: "redact"}, "other")) == {field}
def test_built_fields_with_new_values_are_not_refused_by_the_map() -> None:
    mod = load().map
    errors = mod.selection_errors(
        {"requests_per_minute": 5, "blocked_keywords": "a,b", "retention_days": 7}, {}
    )
    assert errors == {}


#: Fields GW33 validates itself (rewrite gating, grounding mode/threshold); covered by its tests.
_GW33_VALIDATED = frozenset(_FIVE_ACTION_FIELDS[:3] + _FIVE_ACTION_FIELDS[4:]) | frozenset(
    {"hallucination_grounding_mode", "hallucination_grounding_threshold"},
)
# Feature: gw27-firewall-config-fields, Property 2: refused iff retired-and-changed or rejected
# Validates: Requirements 2.1
def test_property_honest_save() -> None:
    mod = load().map
    seed = 2702
    rng = random.Random(seed)
    names = sorted(mod.FIELDS)
    pool: list[object] = [True, False, None, 0, 1, 5, "", "x", "rewrite", "tag", "block"]
    for i in range(10_000):
        stored = {n: rng.choice(pool) for n in rng.sample(names, rng.randint(0, 20))}
        incoming = {n: rng.choice(pool) for n in rng.sample(names, rng.randint(0, 12))}
        expected: set[str] = set()
        for name, value in incoming.items():
            spec = mod.FIELDS[name]
            if stored.get(name) == value:
                continue  # unchanged (the console posts the whole row; absent reads None)
            if value in spec.get("reject", ()):
                expected.add(name)
            elif spec["kind"] == "retired" and value not in spec.get("off", ()):
                expected.add(name)
        got = set(mod.selection_errors(incoming, stored)) - _GW33_VALIDATED
        assert got == expected - _GW33_VALIDATED, (seed, i, incoming, stored, got ^ expected)
