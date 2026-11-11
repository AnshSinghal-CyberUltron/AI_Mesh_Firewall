"""GW28 value types + validation (design C1, D-3, D-8a, T-edge-6, T-edge-9 unit half)."""

from __future__ import annotations

import dataclasses

import pytest

from gateway_v2.dispatch.routing_types import (
    Candidate,
    CandidateSet,
    RoutingConfigError,
    RoutingContext,
    RoutingTable,
    is_valid_model_name,
)
from tests.dispatch._gw28_fakes import MAX_LEN, cand, cset, ctx, plan


def _build(*cs: Candidate, passthrough: bool = False, max_cands: int = 8) -> CandidateSet:
    return CandidateSet.build(
        cs,
        max_candidates=max_cands,
        max_weight=16,
        max_ident_len=MAX_LEN,
        allow_passthrough=passthrough,
    )


def test_build_sorts_and_freezes() -> None:
    s = cset(cand("b"), cand("a"))
    assert [c.candidate_id for c in s.candidates] == ["a", "b"]
    with pytest.raises(dataclasses.FrozenInstanceError):
        s.candidates = ()  # type: ignore[misc]


@pytest.mark.parametrize("bad", ["", "a|b", "a\nb", "a\x1fb", "a b", "x" * 65, "ünï", "a/b"])
def test_bad_identifiers_rejected(bad: str) -> None:
    with pytest.raises(RoutingConfigError):
        _build(cand(bad))
    with pytest.raises(RoutingConfigError):
        _build(cand("ok", provider=bad))
    with pytest.raises(RoutingConfigError):
        _build(cand("ok", models=(bad,)))


def test_upstream_model_allows_slashes_but_not_separators() -> None:
    _build(cand("a", upstream="meta-llama/Llama-3"))
    _build(cand("b", upstream="accounts/x/models/y"))
    for bad in ("a|b", "a\nb", "a b", "x" * 65, ""):
        with pytest.raises(RoutingConfigError):
            _build(cand("c", upstream=bad))


def test_destination_rules() -> None:
    base = cand("a")
    for bad in ("", "x" * 65, "a\x1fb", "a\x00b", "a\nb"):
        with pytest.raises(RoutingConfigError):
            _build(dataclasses.replace(base, destination=bad))
    with pytest.raises(RoutingConfigError):
        _build(cand("a"), dataclasses.replace(cand("b"), destination="dest-a"))


def test_duplicates_caps_and_weight() -> None:
    with pytest.raises(RoutingConfigError):
        _build(cand("a"), cand("a"))
    with pytest.raises(RoutingConfigError):
        _build()
    with pytest.raises(RoutingConfigError):
        _build(cand("a"), cand("b"), max_cands=1)
    for w in (0, -1, 17):
        with pytest.raises(RoutingConfigError):
            _build(cand("a", weight=w))
    with pytest.raises(RoutingConfigError):
        _build(dataclasses.replace(cand("a"), weight=True))


def test_passthrough_only_when_allowed_and_carries_no_model() -> None:
    p = cand("p", passthrough=True)
    with pytest.raises(RoutingConfigError):
        _build(p)
    _build(p, passthrough=True)
    with pytest.raises(RoutingConfigError):
        _build(dataclasses.replace(p, upstream_model="x"), passthrough=True)
    with pytest.raises(RoutingConfigError):
        _build(dataclasses.replace(cand("a"), served_models=frozenset()))


def test_table_lookup() -> None:
    t = RoutingTable.build({"o2": cset(cand("a")), "o1": cset(cand("b"))})
    assert [o for o, _ in t.by_org] == ["o1", "o2"]
    assert t.candidates_for("o1") is not None and t.candidates_for("zz") is None
    with pytest.raises(RoutingConfigError):
        RoutingTable.build({"": cset(cand("a"))})


def test_model_name_validation() -> None:
    for good in ("llama-3", "gpt-4o:2024-08", "a" * MAX_LEN):
        assert is_valid_model_name(good, MAX_LEN)
    for bad in (
        "a|b",
        "a\nb",
        "a\x1fb",
        "a b",
        "meta/llama-3",
        "\x00",
        "ünï",
        "abc\n",
        "",
        "a" * (MAX_LEN + 1),
    ):
        assert not is_valid_model_name(bad, MAX_LEN)
    assert not is_valid_model_name(None, MAX_LEN) and not is_valid_model_name(5, MAX_LEN)


def test_from_plan_stores_invalid_name_as_empty() -> None:
    assert ctx("llama-3").requested_model == "llama-3"
    assert ctx("a|b").requested_model == ""
    c = RoutingContext.from_plan(
        plan(), request_key="k", requested_model=None, sink=lambda _d: None, max_ident_len=MAX_LEN
    )
    assert c.requested_model == ""
