"""GW36 S4 production-boot wiring: `AMF_Q_SAFE` resolution, the admission/rate/budget boot log,
`CapacityUnset`/`CapacityUnavailable` refuse-to-start propagation, and the DECLARED audit sizing
(env-or-default) that feeds `derive_bounds`.

These assertions are the S4-specific companions to `test_gw36_s3_boot.py` (which keeps the S3
state/identity selection cases). House idiom: plain pytest, example-based; Property 5 (the
fail-closed property test) is task 10.6, a separate file.

_Requirements: 1.3, 1.5, 1.6, 2.3, 2.7, 2.8_
"""

from __future__ import annotations

import pytest

from gateway_v2.admit.admission import AdmissionController
from gateway_v2.admit.quota import derive_bounds
from gateway_v2.edge import production_profile, serve
from gateway_v2.edge.chat_deps import fail_closed_admission, fail_closed_budget
from gateway_v2.edge.production_profile import (
    _DECLARED_AUDIT_BYTES_PER_RECORD,
    _DECLARED_AUDIT_DRAIN_RATE_PER_S,
    ProfileError,
)
from gateway_v2.edge.quota_gate import QuotaGate
from gateway_v2.runtime.errors import CapacityUnavailable, CapacityUnset
from tests.edge.test_dev_profile_boot import _contract

#: The valid state inputs a production boot needs beside `AMF_Q_SAFE` (no store I/O at build).
_STATE = {
    "AMF_STATE_VALKEY_URL": "redis://127.0.0.1:1/0",
    "AMF_STATE_HMAC_KEY": "secret",
}


def _prod_env(**overrides: str) -> dict[str, str]:
    """A valid production env (`AMF_Q_SAFE=200`); overrides win and `<unset>` drops a key."""
    env = {"AMF_PROFILE": "production", "AMF_Q_SAFE": "200", **_STATE}
    env.update(overrides)
    return {key: value for key, value in env.items() if value != "<unset>"}


def _boot(**overrides: str) -> tuple[dict[str, object], tuple[str, ...]]:
    return serve.build_boot_dependencies(contract=_contract(), env=_prod_env(**overrides))


# --- Req 1.3: a valid q_safe is parsed and the real bounds derive from it -------------------- #


def test_valid_q_safe_builds_the_real_admission_controller_from_derive_bounds() -> None:
    """A decimal `AMF_Q_SAFE > 0` wires a real `AdmissionController`, never the fail-closed shed."""
    deps, _logs = _boot(AMF_Q_SAFE="200")
    assert isinstance(deps["admission"], AdmissionController)
    assert deps["admission"] is not fail_closed_admission
    # The composed quota gate replaced the fail-closed budget too.
    assert isinstance(deps["quota"], QuotaGate)
    assert deps["quota"] is not fail_closed_budget


def test_logged_depths_match_derive_bounds_for_the_same_q_safe_and_audit_sizing() -> None:
    """The boot log's request_depth/concurrency/audit_depth equal `derive_bounds(...)` exactly."""
    contract = _contract()
    # The default audit sizing (env vars unset → shipped DECLARED defaults) feeds derive_bounds.
    expected = derive_bounds(
        contract,
        q_safe=200.0,
        audit_drain_rate_per_s=_DECLARED_AUDIT_DRAIN_RATE_PER_S,
        audit_bytes_per_record=_DECLARED_AUDIT_BYTES_PER_RECORD,
    )
    _deps, logs = serve.build_boot_dependencies(contract=contract, env=_prod_env())
    joined = "\n".join(logs)
    assert f"request_depth={expected.request_depth}" in joined
    assert f"concurrency={expected.concurrency}" in joined
    assert f"audit_depth={expected.audit_depth}" in joined


# --- Req 1.5: the boot log records q_safe is DECLARED and GW20 owns the measured value -------- #


def test_boot_log_records_q_safe_declared_and_gw20_ownership() -> None:
    _deps, logs = _boot(AMF_Q_SAFE="200")
    joined = "\n".join(logs)
    assert "boot: admission -> AdmissionController" in joined
    assert "q_safe DECLARED=200" in joined
    assert "GW20 owns the measured value" in joined


def test_boot_log_reproduces_the_declared_q_safe_value() -> None:
    """A different declared rate is reported verbatim (`%g`), not the 200 default."""
    _deps, logs = _boot(AMF_Q_SAFE="512.5")
    joined = "\n".join(logs)
    assert "q_safe DECLARED=512.5" in joined


# --- Req 1.6 / 2.2: refuse-to-start when q_safe is unset/blank/non-decimal/<=0 (CapacityUnset
#     is structurally unreachable here because _resolve_q_safe refuses first) ------------------ #


@pytest.mark.parametrize("value", ["<unset>", "", "   "])
def test_missing_q_safe_refuses_to_start_naming_the_variable(value: str) -> None:
    with pytest.raises(ProfileError, match="AMF_Q_SAFE"):
        _boot(AMF_Q_SAFE=value)


@pytest.mark.parametrize("value", ["abc", "1,0", "nan-ish", "0x10"])
def test_non_decimal_q_safe_refuses_and_reproduces_the_rejected_value(value: str) -> None:
    with pytest.raises(ProfileError, match=repr(value)):
        _boot(AMF_Q_SAFE=value)


@pytest.mark.parametrize("value", ["0", "0.0", "-1", "-0.5"])
def test_non_positive_q_safe_refuses_to_start(value: str) -> None:
    with pytest.raises(ProfileError, match="AMF_Q_SAFE"):
        _boot(AMF_Q_SAFE=value)


# --- Req 1.6: CapacityUnset from derive_bounds propagates as refuse-to-start, no fallback ----- #


def test_capacity_unset_from_derive_bounds_propagates_without_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`_resolve_q_safe` blocks `q_safe <= 0` with a `ProfileError` before `derive_bounds` can see
    it, so a real `CapacityUnset` is unreachable from the env path. We assert the PROPAGATION
    contract directly: when `derive_bounds` raises `CapacityUnset`, `apply_production` lets it out
    as a refuse-to-start and never falls back to `fail_closed_admission`.
    """

    def _raise_unset(*_args: object, **_kwargs: object) -> object:
        raise CapacityUnset("q_safe is unset; refuse to start")

    monkeypatch.setattr(production_profile, "derive_bounds", _raise_unset)
    with pytest.raises(CapacityUnset):
        _boot(AMF_Q_SAFE="200")


# --- Req 2.3: CapacityUnavailable propagates as refuse-to-start, no fallback (real trigger) --- #


def test_capacity_unavailable_propagates_without_fallback_via_oversized_audit_record() -> None:
    """A real `CapacityUnavailable`: an audit record larger than the whole audit-queue memory share
    (`AMF_AUDIT_BYTES_PER_RECORD` ≫ `memory_limit · util · 0.02`) drives `audit_queue_depth`'s
    by-memory bound to 0, so `derive_bounds` raises `CapacityUnavailable`, which `apply_production`
    propagates — no fallback to `fail_closed_admission`. 60_000_000 B exceeds the ~50 MiB share of
    the `_contract()` box.
    """
    with pytest.raises(CapacityUnavailable):
        _boot(AMF_AUDIT_BYTES_PER_RECORD="60000000")


# --- Req 2.7 / 2.8: audit sizing — declared env flows into derive_bounds; unset uses defaults -- #


def test_declared_audit_sizing_env_changes_the_derived_audit_depth() -> None:
    """A set `AMF_AUDIT_*` (DECLARED, since `AuditSink.calibrate` is not wired until S5) flows into
    `derive_bounds` and changes the logged `audit_depth` away from the shipped-default value.
    """
    contract = _contract()
    # A smaller byte budget per record → a larger memory-bound audit depth than the default.
    set_drain, set_bytes = 2000.0, 512
    expected = derive_bounds(
        contract,
        q_safe=200.0,
        audit_drain_rate_per_s=set_drain,
        audit_bytes_per_record=set_bytes,
    )
    default = derive_bounds(
        contract,
        q_safe=200.0,
        audit_drain_rate_per_s=_DECLARED_AUDIT_DRAIN_RATE_PER_S,
        audit_bytes_per_record=_DECLARED_AUDIT_BYTES_PER_RECORD,
    )
    assert expected.audit_depth != default.audit_depth, "the chosen inputs must move the bound"

    _deps, logs = serve.build_boot_dependencies(
        contract=contract,
        env=_prod_env(
            AMF_AUDIT_DRAIN_RATE_PER_S=str(set_drain),
            AMF_AUDIT_BYTES_PER_RECORD=str(set_bytes),
        ),
    )
    joined = "\n".join(logs)
    assert f"audit_depth={expected.audit_depth}" in joined
    assert f"audit_depth={default.audit_depth}" not in joined


def test_unset_audit_sizing_uses_the_shipped_declared_defaults() -> None:
    """With `AMF_AUDIT_*` unset the shipped DECLARED defaults size the audit queue (Req 2.8)."""
    contract = _contract()
    default = derive_bounds(
        contract,
        q_safe=200.0,
        audit_drain_rate_per_s=_DECLARED_AUDIT_DRAIN_RATE_PER_S,
        audit_bytes_per_record=_DECLARED_AUDIT_BYTES_PER_RECORD,
    )
    _deps, logs = serve.build_boot_dependencies(contract=contract, env=_prod_env())
    assert f"audit_depth={default.audit_depth}" in "\n".join(logs)


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("AMF_AUDIT_DRAIN_RATE_PER_S", "abc"),
        ("AMF_AUDIT_DRAIN_RATE_PER_S", "0"),
        ("AMF_AUDIT_DRAIN_RATE_PER_S", "-5"),
        ("AMF_AUDIT_BYTES_PER_RECORD", "not-an-int"),
        ("AMF_AUDIT_BYTES_PER_RECORD", "0"),
    ],
)
def test_present_but_malformed_audit_sizing_refuses_to_start(name: str, value: str) -> None:
    """A present-but-invalid declared value is a config error → `ProfileError`, not a silent
    fall back to the default (Req 2.8 reads the env *as the dev profile does*).
    """
    with pytest.raises(ProfileError, match=name):
        _boot(**{name: value})


# NOTE: the "audit sizing from AuditSink.calibrate when wired" branch (Req 2.7) is deferred to S5
# — no `AuditSink.calibrate` is wired in this slice (`_NoAudit` is the placeholder), so only the
# env/default branch is assertable here.
