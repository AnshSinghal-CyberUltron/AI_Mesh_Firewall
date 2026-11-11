"""BudgetVerdict example tests (R2-09 / GW06), task 6.4 (BudgetVerdict half).

Covers the pure ``gateway_v2.admit.grant.BudgetVerdict`` value only (no I/O, no event loop):

* ``code == BUDGET_UNAVAILABLE`` — the one shared posture spelling, reused from
  ``domain/posture.py``, never a second budget-outage code.
* ``retry_after_s >= MIN_RETRY_AFTER_S`` — floored by ``gap_retry_after_s`` so a budget refusal
  can never carry a sub-second Retry-After that amplifies SDK retries.
* ``should_retry is False`` — always, on a budget refusal.
* ``request_id`` echoed from the caller.
* the value is frozen (code-vs-render split; ``edge`` renders the HTTP).
* no HTTP object is constructed — ``BudgetVerdict`` is a plain frozen dataclass.

The watermark half of task 6.4 (``derive_low_watermark`` is a function of the chunk only) lives
with the ``admit/quota.py`` chunk-derivation task, not here. Test files are not under the
import-linter layer contract.

Validates: Requirements 5.5, 5.6
"""

from __future__ import annotations

import dataclasses

from gateway_v2.admit.grant import BudgetVerdict, budget_verdict
from gateway_v2.domain.posture import BUDGET_UNAVAILABLE, MIN_RETRY_AFTER_S

# --------------------------------------------------------------------------- #
# Task 6.4 — BudgetVerdict value
# Feature: budget-lease
# Validates: Requirements 5.5, 5.6
# --------------------------------------------------------------------------- #


def test_budget_verdict_defaults_carry_the_narrow_posture() -> None:
    """A default budget verdict carries the BUDGET_UNAVAILABLE code and the floored Retry-After."""
    verdict = budget_verdict("zs-abc123")
    assert verdict.code == BUDGET_UNAVAILABLE
    assert verdict.retry_after_s >= MIN_RETRY_AFTER_S
    assert verdict.should_retry is False
    assert verdict.request_id == "zs-abc123"


def test_budget_verdict_retry_after_floors_at_min_with_periods() -> None:
    """Even with real re-hydrate/refresh periods, Retry-After stays at or above the floor."""
    for rehydrate_period_ms, refresh_ms in (
        (0.0, 0.0),
        (10.0, 10.0),
        (500.0, 250.0),
        (16_000.0, 2_000.0),
    ):
        verdict = budget_verdict(
            "zs-floor",
            rehydrate_period_ms=rehydrate_period_ms,
            refresh_ms=refresh_ms,
        )
        assert verdict.retry_after_s >= MIN_RETRY_AFTER_S, (
            f"rehydrate={rehydrate_period_ms} refresh={refresh_ms} "
            f"retry_after_s={verdict.retry_after_s} below floor"
        )
        assert verdict.code == BUDGET_UNAVAILABLE
        assert verdict.should_retry is False


def test_budget_verdict_retry_after_grows_with_the_gap() -> None:
    """A larger re-hydrate/refresh gap yields a Retry-After above the bare floor."""
    verdict = budget_verdict("zs-gap", rehydrate_period_ms=16_000.0, refresh_ms=2_000.0)
    # 18 s gap >> 1.0 s floor, so the sum (not the floor) governs.
    assert verdict.retry_after_s == 18.0


def test_budget_verdict_request_id_echoed() -> None:
    """The caller's request_id is echoed verbatim onto the verdict."""
    for request_id in ("zs-1", "zs-000000000001", "zs-DEADBEEF"):
        assert budget_verdict(request_id).request_id == request_id


def test_budget_verdict_reuses_shared_posture_code() -> None:
    """``admit.grant`` reuses the domain budget code, it does not define its own spelling."""
    assert BUDGET_UNAVAILABLE == "budget_unavailable"
    assert budget_verdict("zs-x").code is BUDGET_UNAVAILABLE


def test_budget_verdict_is_frozen() -> None:
    """BudgetVerdict is immutable (frozen slotted dataclass), like ShedVerdict."""
    verdict = BudgetVerdict(
        code=BUDGET_UNAVAILABLE,
        retry_after_s=1.0,
        should_retry=False,
        request_id="zs-1",
    )
    try:
        verdict.should_retry = True  # type: ignore[misc]
    except AttributeError:
        return
    raise AssertionError("BudgetVerdict must be frozen")


def test_budget_verdict_constructs_no_http_object() -> None:
    """The verdict is a plain frozen dataclass value — not an HTTP object (edge renders the 503)."""
    verdict = budget_verdict("zs-nohttp")
    assert dataclasses.is_dataclass(verdict)
    assert type(verdict).__name__ == "BudgetVerdict"
    # A code/value carries only the four render inputs; no status, headers, or response body.
    field_names = {f.name for f in dataclasses.fields(verdict)}
    assert field_names == {"code", "retry_after_s", "should_retry", "request_id"}
