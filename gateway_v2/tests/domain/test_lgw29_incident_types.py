# Feature: gw29-killswitch-isolation, Property 15: GateVerdict is exactly one of refuse/proceed
# Validates: Requirements 1.2, 3.1, 3.4, 4.1 (scope grammar, isolation parser, value types)
"""GW29 domain types: scope grammar, canonical model, isolation parser, GateVerdict invariant."""

from __future__ import annotations

import dataclasses
import math
from types import MappingProxyType

import pytest

from gateway_v2.domain import incident as inc
from gateway_v2.domain.posture import (
    KILL_SWITCH_ENGAGED,
    KILL_SWITCH_UNAVAILABLE,
    MIN_RETRY_AFTER_S,
    MODEL_ISOLATED_NO_FALLBACK,
    MODEL_KILLED,
    REROUTE_REFUSED,
    STREAM_MODEL_ISOLATED,
)
from gateway_v2.domain.posture import __all__ as posture_all
from gateway_v2.domain.state import StoreDataUnavailable
from tests.domain._lgw29_support import ks_record

ALIASES = MappingProxyType({"gpt-4o-2024-08-06": "gpt-4o"})


# -- scope grammar ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("key", "kind", "org", "model"),
    [
        ("global", inc.ScopeKind.GLOBAL, None, None),
        ("org:acme", inc.ScopeKind.ORG, "acme", None),
        ("model:gpt-4o", inc.ScopeKind.MODEL, None, "gpt-4o"),
        ("model:GPT-4o", inc.ScopeKind.MODEL, None, "gpt-4o"),
        ("model:gpt-4o-2024-08-06", inc.ScopeKind.MODEL, None, "gpt-4o"),
        ("model:org/model:v1", inc.ScopeKind.MODEL, None, "org/model:v1"),
        ("om:acme:gpt-4o", inc.ScopeKind.ORG_MODEL, "acme", "gpt-4o"),
        ("om:acme:vendor/m:v1", inc.ScopeKind.ORG_MODEL, "acme", "vendor/m:v1"),
        ("iso:gpt-4o", inc.ScopeKind.ISOLATION, None, "gpt-4o"),
    ],
)
def test_valid_scopes(key: str, kind: inc.ScopeKind, org: str | None, model: str | None) -> None:
    parsed = inc.parse_scope(key, ALIASES)
    assert (parsed.kind, parsed.org, parsed.model, parsed.quarantined) == (kind, org, model, False)


@pytest.mark.parametrize("key", ["", "org:", "model:", "om:acme", "om::m", "om:a b:m", "orgs:x",
                                 "OM:acme:m", "x" * 5, "om:" + "a" * 65 + ":m", "isox:y"])
def test_unknown_shape_raises(key: str) -> None:
    with pytest.raises(StoreDataUnavailable):
        inc.parse_scope(key)


@pytest.mark.parametrize(
    "key",
    ["model:bad name", "model:é", "om:acme:", "iso:", "iso:" + "m" * 129,
     "model:a\x00b", "iso:a b"],
)
def test_known_shape_bad_model_is_quarantined_not_raised(key: str) -> None:
    parsed = inc.parse_scope(key)
    assert parsed.quarantined and parsed.model is None


def test_scope_builders_roundtrip_and_reject() -> None:
    assert inc.parse_scope(inc.org_model_scope("acme", " GPT-4o ")).model == "gpt-4o"
    assert inc.parse_scope(inc.isolation_scope("Vendor/M:V1")).model == "vendor/m:v1"
    with pytest.raises(ValueError):
        inc.org_model_scope("bad slug", "m")
    with pytest.raises(ValueError):
        inc.org_model_scope("acme", "bad model")
    with pytest.raises(ValueError):
        inc.isolation_scope("")


# -- canonical_model -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("gpt-4o", "gpt-4o"),
        ("GPT-4O", "gpt-4o"),
        ("  gpt-4o\t", "gpt-4o"),
        ("ｇｐｔ-4o", "gpt-4o"),  # fullwidth NFKC folds to ASCII
        ("gpt-4o-2024-08-06", "gpt-4o"),  # alias, single hop
        ("GPT-4O-2024-08-06", "gpt-4o"),
        ("auto", "auto"),
        ("vendor/model:v1.2_x", "vendor/model:v1.2_x"),
        ("a$b", None),
        ("vendor/model:v1.2", "vendor/model:v1.2"),
        ("", None),
        ("   ", None),
        ("m" * 129, None),
        ("m" * 128, "m" * 128),
        ("gp\x00t", None),
        ("gp\nt", None),
        ("gpt é", None),
        ("日本", None),
        ("a b", None),
    ],
)
def test_canonical_model_matrix(raw: str, expected: str | None) -> None:
    assert inc.canonical_model(raw, ALIASES) == expected


@pytest.mark.parametrize("raw", [None, 5, b"gpt", ["gpt"], 1.5])
def test_canonical_model_rejects_non_str(raw: object) -> None:
    assert inc.canonical_model(raw) is None


def test_canonical_model_without_aliases_keeps_the_dated_name() -> None:
    assert inc.canonical_model("gpt-4o-2024-08-06") == "gpt-4o-2024-08-06"


# -- isolation parser (D13 origin rows) ------------------------------------------------------


def _iso(body: dict[str, object] | bytes) -> inc.IsolationRecord:
    return inc.parse_isolation_body(ks_record("iso:m", body, feed_seq=9), "m", ALIASES)


def test_origin_missing_with_finite_expiry_is_an_expiring_operator_isolation() -> None:
    rec = _iso({"on": True, "expires_at": 1000.0, "fallback": "GPT-4o-2024-08-06"})
    assert not rec.malformed
    assert (rec.origin, rec.expires_at, rec.no_expiry, rec.fallback) == (
        inc.IsolationOrigin.OPERATOR, 1000.0, False, "gpt-4o")
    assert rec.feed_seq == 9


def test_origin_missing_with_explicit_no_expiry_is_permanent() -> None:
    rec = _iso({"on": True, "expires_at": None, "no_expiry": True})
    assert not rec.malformed and rec.no_expiry and rec.expires_at is None


def test_origin_missing_with_neither_is_malformed() -> None:
    assert _iso({"on": True}).malformed
    assert _iso({"on": True, "expires_at": None}).malformed


def test_bogus_origin_is_malformed() -> None:
    assert _iso({"on": True, "origin": "bogus", "expires_at": 5.0}).malformed
    assert _iso({"on": True, "origin": None, "expires_at": 5.0}).malformed


@pytest.mark.parametrize(
    "body",
    [
        {"on": True, "expires_at": 5.0, "no_expiry": True},  # contradictory
        {"on": True, "expires_at": "soon"},
        {"on": True, "expires_at": float("nan")},
        {"on": True, "expires_at": float("inf")},
        {"on": True, "expires_at": -1},
        {"on": True, "expires_at": True},
        {"on": True, "expires_at": 5.0, "fallback": 7},
        {"on": True, "expires_at": 5.0, "fallback": "bad name"},
        {"on": True, "expires_at": 5.0, "no_expiry": "yes"},
        {"on": True, "expires_at": 5.0, "created_at": "now"},
    ],
)
def test_malformed_operator_bodies(body: dict[str, object]) -> None:
    assert _iso(body).malformed


@pytest.mark.parametrize("raw", [b"not json", b"[1]", b"7", b"\xff\xfe"])
def test_unparseable_body_is_malformed_not_raised(raw: bytes) -> None:
    assert _iso(raw).malformed


def test_auto_with_unusable_expiry_is_not_malformed() -> None:
    for expiry in (None, "x", float("nan"), float("inf"), 0, -5):
        rec = _iso({"on": True, "origin": "auto", "created_at": 10.0, "expires_at": expiry})
        assert not rec.malformed and rec.expires_at is None and rec.created_at == 10.0
        assert rec.origin is inc.IsolationOrigin.AUTO


def test_reason_is_sanitised_and_truncated_never_a_failure() -> None:
    long = _iso({"on": True, "expires_at": 5.0, "reason": "x" * 500})
    assert not long.malformed and len(long.reason) == inc.REASON_MAX
    ugly = _iso({"on": True, "expires_at": 5.0, "reason": "a\nb\x00é"})
    assert not ugly.malformed and ugly.reason == "a?b??"
    assert _iso({"on": True, "expires_at": 5.0, "reason": 5}).reason == ""


# -- value types -----------------------------------------------------------------------------

_VALUE_TYPES = (
    inc.IsolationRecord, inc.ModelStatus, inc.ModelProfile, inc.RequestConstraints,
    inc.RouteDecision, inc.RerouteNote, inc.StreamBinding, inc.IncidentEvent, inc.KillEvaluation,
    inc.SweepResult, inc.ParsedScope, inc.GateVerdict,
)


@pytest.mark.parametrize("cls", _VALUE_TYPES)
def test_value_types_are_frozen_and_slotted(cls: type) -> None:
    params = cls.__dataclass_params__  # type: ignore[attr-defined]
    assert params.frozen and hasattr(cls, "__slots__")


def test_no_event_field_can_carry_content_or_keys() -> None:
    names = {f.name for f in dataclasses.fields(inc.IncidentEvent)}
    assert not names & {"request_id", "prompt", "content", "key", "api_key", "token", "secret"}


def test_new_posture_codes_are_exported_with_one_spelling() -> None:
    assert (KILL_SWITCH_ENGAGED, MODEL_KILLED, MODEL_ISOLATED_NO_FALLBACK, REROUTE_REFUSED,
            STREAM_MODEL_ISOLATED) == ("kill_switch_engaged", "model_killed",
                                       "model_isolated_no_fallback", "reroute_refused",
                                       "stream_model_isolated")
    for name in ("KILL_SWITCH_ENGAGED", "MODEL_KILLED", "MODEL_ISOLATED_NO_FALLBACK",
                 "REROUTE_REFUSED", "STREAM_MODEL_ISOLATED"):
        assert name in posture_all


def test_protocol_limits_are_named_constants() -> None:
    assert (inc.MODEL_NAME_MAX, inc.SLUG_MAX, inc.REASON_MAX) == (128, 64, 200)
    assert inc.ROUTING_SENTINEL == "auto" and inc.KS_SCOPE_GENERATION == 2
    for cause in ("recheck", "gate_error", "unwired", "unresolvable_model", "unknown_model",
                  "reroute_unsupported", "cut_killed", "cut_key_revoked", "cut_snapshot_stale"):
        assert cause in inc.INCIDENT_CAUSES
    assert len(set(inc.INCIDENT_CAUSES)) == len(inc.INCIDENT_CAUSES)
    assert {"error_rate", "guard_rate", "probation_failed"} <= set(inc.AUTO_REASONS)


# -- GateVerdict -----------------------------------------------------------------------------


def test_gate_verdict_constructors() -> None:
    ok = inc.GateVerdict.proceed("gpt-4o")
    assert ok.refuse_code is None and ok.routed_model == "gpt-4o" and ok.reroute is None
    no = inc.GateVerdict.refuse(MODEL_KILLED, "model", 1.5)
    assert no.refuse_code == MODEL_KILLED and no.routed_model is None and no.retry_after_s == 1.5


@pytest.mark.parametrize(
    "args",
    [
        (None, None, None, None, None),  # neither
        ("c", "model", 1.0, "m", None),  # both
        ("c", None, 1.0, None, None),  # no cause
        ("c", "bogus", 1.0, None, None),  # cause outside the closed vocabulary
        ("c", "model", None, None, None),  # no retry-after
        ("c", "model", MIN_RETRY_AFTER_S / 2, None, None),  # below the floor
        ("c", "model", math.nan, None, None),
        ("c", "model", math.inf, None, None),
        (None, "model", None, "m", None),  # proceed with a cause
        (None, None, 1.0, "m", None),  # proceed with retry-after
        (None, None, None, "", None),  # empty routed model
    ],
)
def test_gate_verdict_invariant_rejects(args: tuple[object, ...]) -> None:
    with pytest.raises(ValueError):
        inc.GateVerdict(*args)  # type: ignore[arg-type]


def test_refusal_with_a_reroute_is_rejected() -> None:
    decision = inc.RouteDecision(inc.RouteOutcome.REROUTE, "b", "a", "b", "isolation", "r")
    with pytest.raises(ValueError):
        inc.GateVerdict(KILL_SWITCH_UNAVAILABLE, "stale", 1.0, None, decision)


def test_fail_closed_defaults_refuse_unwired() -> None:
    for verdict in (
        inc.fail_closed_incident(org_id="o", requested_model="m", request_id="r"),
        inc.fail_closed_recheck(org_id="o", model="m"),
        inc.fail_closed_org(org_id="o", request_id="r"),
    ):
        assert verdict.refuse_code == KILL_SWITCH_UNAVAILABLE and verdict.cause == "unwired"
        assert verdict.retry_after_s is not None and verdict.retry_after_s >= MIN_RETRY_AFTER_S


def test_kill_evaluation_helpers() -> None:
    ev = inc.KillEvaluation("ok", False, False, True, False, 0.1)
    assert ev.engaged and not ev.stale
    assert inc.KillEvaluation("stale", False, False, False, False, None).stale
