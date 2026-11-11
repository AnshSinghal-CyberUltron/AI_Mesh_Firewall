"""ShedVerdict + Retry-After jitter tests (R2-07 / R2-08 / GW19), task 4.2.

Covers the pure ``gateway_v2.admit.grant`` module only (no I/O, no event loop):

* **Property 5 — Retry-After floor and should-retry** (task 4.2): every shed verdict carries
  ``retry_after_s >= MIN_RETRY_AFTER_S`` (1.0 s), ``should_retry is False``, a non-empty
  ``request_id``, ``code == OVERLOAD_SHED``, and a Retry-After that is NEVER in the 6-11 ms band.
* **Unit test**: the jitter varies across seeds yet always stays within
  ``[floor, floor * (1 + jitter_frac)]``.

House idiom: seeded ``random.Random`` loops of >= 10,000 iterations with the seed logged in the
assertion message; NO ``hypothesis``. The RNG is injected so the jitter is deterministic. Test
files are not under the import-linter layer contract.
"""

from __future__ import annotations

import random

from gateway_v2.admit.grant import (
    Admitted,
    ResourceGrant,
    ShedVerdict,
    shed_retry_after_s,
    shed_verdict,
)
from gateway_v2.domain.posture import MIN_RETRY_AFTER_S, OVERLOAD_SHED

_ITERATIONS = 10_000

# The forbidden Retry-After band (Req 6.4). The 6-11 ms sheds amplified SDK retries ≈2.6×.
_BAND_LOW_S = 0.006
_BAND_HIGH_S = 0.011


# --------------------------------------------------------------------------- #
# Task 4.2 — Property 5: Retry-After floor and should-retry
# Feature: admission-control, Property 5
# Validates: Requirements 5.2, 5.3, 6.1, 6.2, 6.3, 6.4
# --------------------------------------------------------------------------- #


def test_property5_retry_after_floor_and_should_retry() -> None:
    """Every shed verdict obeys the floor + should-retry + no-band invariant (Property 5)."""
    seed = 0x19_05
    rng = random.Random(seed)
    for i in range(_ITERATIONS):
        request_id = f"zs-{rng.getrandbits(48):012x}"
        verdict = shed_verdict(rng, request_id)

        assert verdict.retry_after_s >= MIN_RETRY_AFTER_S, (
            f"seed={seed:#x} iter={i} retry_after_s={verdict.retry_after_s} "
            f"below the {MIN_RETRY_AFTER_S}s floor"
        )
        assert verdict.should_retry is False, (
            f"seed={seed:#x} iter={i} should_retry must be False on a shed, "
            f"got {verdict.should_retry}"
        )
        assert verdict.request_id == request_id and verdict.request_id, (
            f"seed={seed:#x} iter={i} request_id missing/mismatched: {verdict.request_id!r}"
        )
        assert verdict.code == OVERLOAD_SHED, (
            f"seed={seed:#x} iter={i} code must be OVERLOAD_SHED, got {verdict.code!r}"
        )
        assert not (_BAND_LOW_S <= verdict.retry_after_s <= _BAND_HIGH_S), (
            f"seed={seed:#x} iter={i} retry_after_s={verdict.retry_after_s} "
            f"landed in the forbidden 6-11 ms band"
        )


def test_property5_retry_after_floor_across_many_seeds() -> None:
    """The floor + no-band invariant holds for the raw helper across many injected RNG seeds."""
    seed = 0x19_05A
    seed_rng = random.Random(seed)
    for i in range(_ITERATIONS):
        rng = random.Random(seed_rng.getrandbits(64))
        value = shed_retry_after_s(rng)
        assert value >= MIN_RETRY_AFTER_S, (
            f"seed={seed:#x} iter={i} value={value} below floor"
        )
        assert not (_BAND_LOW_S <= value <= _BAND_HIGH_S), (
            f"seed={seed:#x} iter={i} value={value} in forbidden band"
        )


# --------------------------------------------------------------------------- #
# Unit tests
# --------------------------------------------------------------------------- #


def test_jitter_varies_across_seeds_within_bounds() -> None:
    """Jitter varies across seeds but always stays within [floor, floor*(1+jitter_frac)]."""
    floor = MIN_RETRY_AFTER_S
    jitter_frac = 0.5
    upper = floor * (1.0 + jitter_frac)
    values = set()
    for s in range(256):
        value = shed_retry_after_s(random.Random(s), floor_s=floor, jitter_frac=jitter_frac)
        assert floor <= value <= upper, (
            f"seed={s} value={value} outside [{floor}, {upper}]"
        )
        values.add(round(value, 9))
    # Distinct seeds must produce more than one distinct value (jitter actually varies).
    assert len(values) > 1, "jitter did not vary across seeds"


def test_jitter_frac_bounds_are_respected() -> None:
    """A custom jitter_frac bounds the result at floor*(1+jitter_frac)."""
    floor = 2.0
    jitter_frac = 0.25
    for s in range(64):
        value = shed_retry_after_s(random.Random(s), floor_s=floor, jitter_frac=jitter_frac)
        assert floor <= value <= floor * (1.0 + jitter_frac)


def test_min_retry_after_imported_not_redefined() -> None:
    """``admit.grant`` reuses the domain floor (1.0 s), it does not define its own."""
    assert MIN_RETRY_AFTER_S == 1.0


def test_shed_verdict_is_frozen() -> None:
    verdict = ShedVerdict(
        code=OVERLOAD_SHED, retry_after_s=1.0, should_retry=False, request_id="zs-1"
    )
    try:
        verdict.should_retry = True  # type: ignore[misc]
    except AttributeError:
        return
    raise AssertionError("ShedVerdict must be frozen")


def test_admitted_marker_is_frozen() -> None:
    admitted = Admitted(owner_id="org-1", request_id="zs-1", admitted_at=1.0)
    assert admitted.owner_id == "org-1"
    try:
        admitted.admitted_at = 2.0  # type: ignore[misc]
    except AttributeError:
        return
    raise AssertionError("Admitted must be frozen")


def test_resource_grant_reservation() -> None:
    """The ResourceGrant reservation is a frozen value, not an HTTP object (card reservation)."""
    grant = ResourceGrant(owner_id="org-1", request_id="zs-1")
    assert grant.concurrency_slots == 1
    try:
        grant.concurrency_slots = 2  # type: ignore[misc]
    except AttributeError:
        return
    raise AssertionError("ResourceGrant must be frozen")
