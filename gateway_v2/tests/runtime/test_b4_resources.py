"""B4 task 1.2 — unit + property tests for the two new ResourceContract accessors.

# Validates: Requirements 4.4, 9.3

Card B4 (GW17-18 / C43) adds two derived capacity accessors to
:class:`~gateway_v2.runtime.resources.ResourceContract`:

* ``owner_queue_bound_s()`` — the C43 ``RV_OWNER_QUEUE_MS`` owner-queue bound, read from
  ``os.environ`` with the runbook RC-measured ~20 ms default, returned in seconds. An unset key
  uses the default silently; a present-but-unparseable or non-positive value warns naming the
  rejected value and falls back to the default (the same warn-and-default stance ``load_contract``
  / ``holdback_config`` take). Because it reads ``os.environ`` directly these tests drive the env
  with ``monkeypatch.setenv`` / ``monkeypatch.delenv`` so each case is isolated and restored.
* ``guard_wait_budget_s()`` — the GW19 / R2-07 guard-wait budget ``max(100, cap + 40)`` ms with
  ``cap`` the CoDel hard cap (60 ms) → ``max(0.100, 0.060 + 0.040) = 0.100`` s. A pure
  constant-derived value with no env input.

``runtime/resources.py`` is the one module permitted a capacity literal, so these tests read the
module constants (``resources._DEFAULT_OWNER_QUEUE_MS`` etc.) rather than restating a magic number.
The contract fixture is built via ``from_signals`` with the same ``HardwareSignals`` shape the
GW12 / GW16 / B3 runtime tests use.

The property test uses the house idiom (a seeded ``random.Random``, no ``hypothesis``,
>=10,000 iterations); the derivations are pure/synchronous so no event loop is needed.
"""

from __future__ import annotations

import math
import random
import warnings

import pytest

import gateway_v2.runtime.resources as resources
from gateway_v2.runtime.kinds import HardwareSignals
from gateway_v2.runtime.resources import ResourceContract, from_signals

_MS_PER_S = resources._MS_PER_S
_ENV_KEY = resources._OWNER_QUEUE_ENV_KEY
_DEFAULT_OWNER_QUEUE_MS = resources._DEFAULT_OWNER_QUEUE_MS
_DEFAULT_OWNER_QUEUE_S = _DEFAULT_OWNER_QUEUE_MS / _MS_PER_S


def _contract(
    *,
    target_p99_ms: float = 20.0,
    utilization_cap: float = 0.75,
    per_worker_rss: int = 400 * 1024 * 1024,
) -> ResourceContract:
    """A serviceable contract fixture via ``from_signals`` (same shape as the GW12/B3 tests).

    The signals clear ``from_signals``' refuse-to-start checks on both the CPU and RAM worker
    paths, so the contract is valid for any ``target_p99_ms`` the caller passes.
    """
    signals = HardwareSignals(
        cpu_quota=4.0,
        memory_limit=per_worker_rss * 8,
        fd_limit=4096,
        cpu_source="test",
        mem_source="test",
        fd_source="test",
    )
    return from_signals(
        signals,
        target_p99_ms=target_p99_ms,
        utilization_cap=utilization_cap,
        per_worker_rss=per_worker_rss,
    )


# ===========================================================================
# owner_queue_bound_s() — EXAMPLE / unit coverage (env-driven via monkeypatch)
# ===========================================================================


def test_owner_queue_bound_defaults_to_20ms_when_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """All-unset env → the measured ~20 ms default (0.020 s), with no warning (R4.4)."""
    monkeypatch.delenv(_ENV_KEY, raising=False)
    contract = _contract()
    with warnings.catch_warnings():
        warnings.simplefilter("error")  # a stray warning on the default path would fail here
        got = contract.owner_queue_bound_s()
    assert got == _DEFAULT_OWNER_QUEUE_S
    assert got == pytest.approx(0.020)


def test_owner_queue_bound_reads_valid_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A valid ``RV_OWNER_QUEUE_MS=35`` override is read through → 0.035 s (R4.4)."""
    monkeypatch.setenv(_ENV_KEY, "35")
    contract = _contract()
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        got = contract.owner_queue_bound_s()
    assert got == pytest.approx(0.035)


def test_owner_queue_bound_unparseable_falls_back_with_warning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A present-but-unparseable value → the default + a warning on the rejected value (R4.4)."""
    monkeypatch.setenv(_ENV_KEY, "abc")
    contract = _contract()
    with pytest.warns(UserWarning):
        got = contract.owner_queue_bound_s()
    assert got == _DEFAULT_OWNER_QUEUE_S


@pytest.mark.parametrize("raw", ["-5", "0"])
def test_owner_queue_bound_non_positive_falls_back_with_warning(
    monkeypatch: pytest.MonkeyPatch,
    raw: str,
) -> None:
    """A non-positive value (-5, 0) → the default + a warning (R4.4)."""
    monkeypatch.setenv(_ENV_KEY, raw)
    contract = _contract()
    with pytest.warns(UserWarning):
        got = contract.owner_queue_bound_s()
    assert got == _DEFAULT_OWNER_QUEUE_S


# ===========================================================================
# owner_queue_bound_s() — PROPERTY (house idiom)
# ===========================================================================

# Feature: provider-egress-gw-b4, Property: owner_queue_bound totality
# Validates: Requirements 4.4, 9.3

_SEED = 0x0B4_0001
_ITERATIONS = 10_000

# Genuinely non-numeric strings Python's float() rejects. Deliberately excludes whitespace-only
# values (those strip to blank → silent default, no exception path) and ASCII-digit strings.
_GARBAGE = ("abc", "nan-ish", "12x", "1.2.3", "0x10", "1e", "--", "queue", "ms", "")


def _draw_owner_queue(rng: random.Random) -> tuple[str, float]:
    """Return ``(raw, expected_s)`` for a drawn ``RV_OWNER_QUEUE_MS`` value.

    The expected result is computed independently of the production parser: a validly-parsed
    POSITIVE finite float yields ``value_ms / 1000``; everything else (garbage, non-positive,
    nan/inf, blank) yields the ~20 ms default in seconds. The accessor must never raise and must
    always return a positive finite float.
    """
    kind = rng.choice(
        (
            "valid",
            "valid_padded",
            "garbage",
            "negative",
            "zero",
            "blank",
            "nan",
            "inf",
        )
    )
    if kind == "valid":
        value = round(rng.uniform(0.001, 5000.0), 3)
        return repr(value), value / _MS_PER_S
    if kind == "valid_padded":
        value = round(rng.uniform(0.001, 5000.0), 3)
        return f"  {value!r}  ", value / _MS_PER_S
    if kind == "garbage":
        return rng.choice(_GARBAGE), _DEFAULT_OWNER_QUEUE_S
    if kind == "negative":
        value = -round(rng.uniform(0.001, 5000.0), 3)
        return repr(value), _DEFAULT_OWNER_QUEUE_S
    if kind == "zero":
        return rng.choice(("0", "0.0", "-0.0")), _DEFAULT_OWNER_QUEUE_S
    if kind == "blank":
        return rng.choice(("", "   ", "\t")), _DEFAULT_OWNER_QUEUE_S
    if kind == "nan":
        return rng.choice(("nan", "NaN", "-nan")), _DEFAULT_OWNER_QUEUE_S
    # inf
    return rng.choice(("inf", "Infinity", "-inf")), _DEFAULT_OWNER_QUEUE_S


def test_property_owner_queue_bound_totality(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """owner_queue_bound_s() is total: parsed-positive/1000 or default, always positive finite."""
    rng = random.Random(_SEED)
    contract = _contract()
    for i in range(_ITERATIONS):
        raw, expected_s = _draw_owner_queue(rng)
        monkeypatch.setenv(_ENV_KEY, raw)
        # A rejected value emits a warning on the fail-closed path; swallow it here (the warning
        # presence itself is asserted by the example unit tests above). The property is on the
        # returned value, which must never raise.
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            got = contract.owner_queue_bound_s()
        ctx = f"seed={_SEED:#x} iter={i} raw={raw!r}"
        assert isinstance(got, float), ctx
        assert got > 0.0, ctx
        assert math.isfinite(got), ctx
        assert got == pytest.approx(expected_s), ctx


def test_property_owner_queue_bound_never_raises_on_blank_delenv(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The unset path (delenv) is also total — the default is returned without raising."""
    monkeypatch.delenv(_ENV_KEY, raising=False)
    contract = _contract()
    got = contract.owner_queue_bound_s()
    assert got == _DEFAULT_OWNER_QUEUE_S
    assert got > 0.0 and math.isfinite(got)


# ===========================================================================
# guard_wait_budget_s() — EXAMPLE / unit coverage (pure constant-derived)
# ===========================================================================


def test_guard_wait_budget_equals_100ms_at_signed_defaults() -> None:
    """``max(100, cap + 40)`` ms with cap=60 ms → 0.100 s, independent of env (R4.4, R9.3)."""
    contract = _contract()
    got = contract.guard_wait_budget_s()
    assert got == pytest.approx(0.100)


def test_guard_wait_budget_derivation_matches_module_constants() -> None:
    """The value is derived from the module's capacity constants, not a literal in the test.

    budget_ms = max(_GUARD_WAIT_FLOOR_MS, _CODEL_HARD_CAP_MS + _GUARD_WAIT_HEADROOM_MS); at the
    signed defaults the floor (100 ms) wins over cap+headroom (60 + 40 = 100 ms) — the clamp is
    exactly at its binding point, which is what proves the budget tracks the floor.
    """
    expected_ms = max(
        resources._GUARD_WAIT_FLOOR_MS,
        resources._CODEL_HARD_CAP_MS + resources._GUARD_WAIT_HEADROOM_MS,
    )
    contract = _contract()
    assert contract.guard_wait_budget_s() == expected_ms / _MS_PER_S


def test_guard_wait_budget_is_env_independent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``guard_wait_budget_s()`` is pure constant-derived — the owner-queue env does not move it."""
    contract = _contract()
    monkeypatch.setenv(_ENV_KEY, "999")
    assert contract.guard_wait_budget_s() == pytest.approx(0.100)
    monkeypatch.delenv(_ENV_KEY, raising=False)
    assert contract.guard_wait_budget_s() == pytest.approx(0.100)
