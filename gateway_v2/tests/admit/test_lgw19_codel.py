"""Pure CoDel state-machine tests (R2-07 / GW19), tasks 2.3 / 2.4 / 2.5.

Covers the pure ``gateway_v2.admit.codel`` module only (no I/O, no event loop):

* **Property 6 — CoDel quiescence** (task 2.3): a sojourn stream whose values stay at/below
  ``Target`` for a full ``Interval`` never sheds.
* **Property 2 — shedding under sustained overload, controller half** (task 2.4): sustained
  sojourn above ``Target`` eventually sheds, and more overload does not reduce shedding.
* **Unit tests** (task 2.5): owner-signed constants (5 / 100 / 60), the hard cap sheds regardless
  of backoff state, decisions read the injected clock, there is no 12 ms instantaneous bound,
  admission is prompt-size independent (the signature takes only sojourn + now), and the backoff
  interval contracts as ``interval / sqrt(count)``.

House idiom: seeded ``random.Random`` loops of >= 10,000 iterations with the seed logged in the
assertion message; NO ``hypothesis``. Time is driven by an explicit ``now=`` or an injected fake
clock so the machine is deterministic. Test files are not under the import-linter layer contract.
"""

from __future__ import annotations

import math
import random

from gateway_v2.admit.codel import (
    CoDelController,
    CoDelDecision,
    CoDelParams,
    CoDelReason,
)

_ITERATIONS = 10_000


def _controller(now: float = 0.0) -> CoDelController:
    """A controller on owner-signed params with a trivial clock (tests mostly pass ``now=``)."""
    return CoDelController(CoDelParams(), clock=lambda: now)


# --------------------------------------------------------------------------- #
# Task 2.3 — Property 6: CoDel quiescence
# Feature: admission-control, Property 6
# Validates: Requirements 1.2
# --------------------------------------------------------------------------- #


def test_property6_quiescence_never_sheds_at_or_below_target() -> None:
    """A stream whose sojourn stays <= Target for a full Interval never sheds (Property 6)."""
    seed = 0x19_06
    rng = random.Random(seed)
    params = CoDelParams()
    for i in range(_ITERATIONS):
        controller = CoDelController(params, clock=lambda: 0.0)
        # Advance time arbitrarily far past a full Interval while every sojourn stays <= Target.
        # Quiescence must hold regardless of elapsed time: the excursion is never started.
        now = rng.uniform(0.0, 10.0)
        steps = rng.randint(1, 50)
        for _ in range(steps):
            # dt can exceed Interval (100 ms) so "a full Interval" has genuinely elapsed.
            now += rng.uniform(0.0, 250.0)
            sojourn = rng.uniform(0.0, params.target_ms)  # strictly within [0, Target]
            decision = controller.observe_and_decide(sojourn, now=now)
            assert decision.admit is True, (
                f"seed={seed:#x} iter={i} sojourn={sojourn} now={now} "
                f"sub-target sojourn was shed: {decision}"
            )
            assert decision.reason is CoDelReason.ADMIT, (
                f"seed={seed:#x} iter={i} sojourn={sojourn} reason={decision.reason}"
            )
            assert controller.dropping is False, (
                f"seed={seed:#x} iter={i} entered dropping under quiescence"
            )
            assert controller.first_above_at is None, (
                f"seed={seed:#x} iter={i} excursion started under quiescence"
            )


def test_property6_single_sub_target_sample_resets_an_excursion() -> None:
    """A single sub-Target sojourn clears the excursion, restoring quiescence (recovery)."""
    seed = 0x19_06A
    rng = random.Random(seed)
    params = CoDelParams()
    for i in range(_ITERATIONS):
        controller = CoDelController(params, clock=lambda: 0.0)
        now = 0.0
        # Drive it into dropping first.
        now += 1.0
        controller.observe_and_decide(params.target_ms + 1.0, now=now)  # start excursion
        now += params.interval_ms + rng.uniform(1.0, 50.0)
        shed = controller.observe_and_decide(params.target_ms + 1.0, now=now)
        assert shed.admit is False, f"seed={seed:#x} iter={i} expected a shed to arm dropping"
        # One sub-target sample must reset.
        now += rng.uniform(0.0, 50.0)
        recovered = controller.observe_and_decide(rng.uniform(0.0, params.target_ms), now=now)
        assert recovered.admit is True, f"seed={seed:#x} iter={i} did not recover: {recovered}"
        assert controller.dropping is False, f"seed={seed:#x} iter={i} dropping not cleared"
        assert controller.first_above_at is None, f"seed={seed:#x} iter={i} excursion not cleared"


# --------------------------------------------------------------------------- #
# Task 2.4 — Property 2: shedding under sustained overload (controller half)
# Feature: admission-control, Property 2
# Validates: Requirements 1.3, 5.4, 12.2
# --------------------------------------------------------------------------- #


def _run_overload(
    params: CoDelParams,
    *,
    overload_ms: float,
    steps: int,
    dt_ms: float,
) -> int:
    """Drive a controller with a constant above-target sojourn; return the number of sheds.

    ``overload_ms`` is kept strictly below ``Hard_Cap`` so sheds are attributable to CoDel backoff,
    not the hard cap — this isolates the sustained-overload control law (Property 2).
    """
    controller = CoDelController(params, clock=lambda: 0.0)
    now = 0.0
    sheds = 0
    for _ in range(steps):
        now += dt_ms
        decision = controller.observe_and_decide(overload_ms, now=now)
        if not decision.admit:
            assert decision.reason is CoDelReason.SHED_BACKOFF
            sheds += 1
    return sheds


def test_property2_sustained_overload_eventually_sheds() -> None:
    """Sustained sojourn above Target eventually produces a non-empty shed set (Property 2)."""
    seed = 0x19_02
    rng = random.Random(seed)
    params = CoDelParams()
    for i in range(_ITERATIONS):
        # overload strictly in (Target, Hard_Cap) so the hard cap never fires.
        overload = rng.uniform(params.target_ms + 0.5, params.hard_cap_ms - 0.5)
        # Enough steps spanning well past a full Interval to guarantee at least one drop.
        dt = rng.uniform(5.0, 40.0)
        steps = rng.randint(20, 80)
        # Guarantee the run spans more than one Interval regardless of dt.
        min_steps = int(params.interval_ms / dt) + 5
        steps = max(steps, min_steps)
        sheds = _run_overload(params, overload_ms=overload, steps=steps, dt_ms=dt)
        assert sheds >= 1, (
            f"seed={seed:#x} iter={i} overload={overload} dt={dt} steps={steps} "
            f"sustained overload never shed (shed set empty)"
        )


def test_property2_more_overload_does_not_reduce_shedding() -> None:
    """A heavier overload never sheds fewer requests than a lighter one (Property 2, monotone)."""
    seed = 0x19_02B
    rng = random.Random(seed)
    params = CoDelParams()
    for i in range(_ITERATIONS):
        dt = rng.uniform(5.0, 40.0)
        min_steps = int(params.interval_ms / dt) + 5
        steps = max(rng.randint(20, 80), min_steps)
        light = rng.uniform(params.target_ms + 0.5, params.hard_cap_ms - 10.0)
        heavier = rng.uniform(light, params.hard_cap_ms - 0.5)
        sheds_light = _run_overload(params, overload_ms=light, steps=steps, dt_ms=dt)
        sheds_heavier = _run_overload(params, overload_ms=heavier, steps=steps, dt_ms=dt)
        # The CoDel backoff schedule depends on timing, not on the magnitude of the above-target
        # sojourn, so for identical timing the shed counts are equal; crucially, raising the
        # overload NEVER reduces the shed count.
        assert sheds_heavier >= sheds_light, (
            f"seed={seed:#x} iter={i} dt={dt} steps={steps} light={light} heavier={heavier} "
            f"more overload reduced shedding: light={sheds_light} heavier={sheds_heavier}"
        )


# --------------------------------------------------------------------------- #
# Task 2.5 — unit tests
# Validates: Requirements 1.4, 1.5, 1.6, 3.1, 3.2
# --------------------------------------------------------------------------- #


def test_owner_signed_constant_defaults() -> None:
    """Target = 5 ms, Interval = 100 ms, Hard_Cap = 60 ms (Requirement 1.5)."""
    params = CoDelParams()
    assert params.target_ms == 5.0
    assert params.interval_ms == 100.0
    assert params.hard_cap_ms == 60.0


def test_reason_values() -> None:
    assert CoDelReason.ADMIT == "admit"
    assert CoDelReason.SHED_BACKOFF == "shed_backoff"
    assert CoDelReason.SHED_HARD_CAP == "shed_hard_cap"


def test_hard_cap_sheds_regardless_of_backoff_state() -> None:
    """A sojourn >= Hard_Cap sheds with SHED_HARD_CAP from the clean baseline (Req 1.4)."""
    controller = _controller()
    decision = controller.observe_and_decide(60.0, now=1.0)
    assert decision.admit is False
    assert decision.reason is CoDelReason.SHED_HARD_CAP


def test_hard_cap_sheds_even_while_already_dropping() -> None:
    """The hard cap short-circuits even when CoDel is already in backoff (Req 1.4)."""
    params = CoDelParams()
    controller = CoDelController(params, clock=lambda: 0.0)
    controller.observe_and_decide(params.target_ms + 1.0, now=1.0)  # start excursion
    armed = controller.observe_and_decide(params.target_ms + 1.0, now=1.0 + params.interval_ms + 1)
    assert armed.admit is False and controller.dropping is True
    # Now a hard-cap sojourn: shed via the cap, not the backoff reason.
    capped = controller.observe_and_decide(params.hard_cap_ms + 5.0, now=500.0)
    assert capped.admit is False
    assert capped.reason is CoDelReason.SHED_HARD_CAP


def test_decisions_read_the_injected_clock() -> None:
    """With no explicit ``now``, the controller reads the injected clock (Req 1.6)."""
    fake = {"t": 0.0}
    params = CoDelParams()
    controller = CoDelController(params, clock=lambda: fake["t"])
    # First above-target sample starts the excursion at clock=0.
    fake["t"] = 0.0
    assert controller.observe_and_decide(params.target_ms + 1.0).admit is True
    assert controller.first_above_at == 0.0
    # Advance the injected clock past a full Interval; the next above-target sample must shed,
    # proving the decision used the clock (not a frozen/zero time).
    fake["t"] = params.interval_ms + 1.0
    decision = controller.observe_and_decide(params.target_ms + 1.0)
    assert decision.admit is False
    assert decision.reason is CoDelReason.SHED_BACKOFF
    assert decision.next_drop_at == fake["t"] + params.interval_ms


def test_no_twelve_ms_instantaneous_bound() -> None:
    """There is no fixed 12 ms instantaneous latency bound; 12 ms sojourn is NOT auto-shed (3.2).

    A single 12 ms sojourn (above Target but below Hard_Cap) is admitted: it merely starts an
    excursion. Only a sustained-above-target min for a full Interval sheds — never an instantaneous
    12 ms rule.
    """
    controller = _controller()
    first = controller.observe_and_decide(12.0, now=1.0)
    assert first.admit is True, "a lone 12 ms sojourn must not be shed (no instantaneous bound)"
    # Even a few more 12 ms samples within one Interval keep admitting.
    second = controller.observe_and_decide(12.0, now=50.0)
    assert second.admit is True


def test_prompt_size_independence_signature() -> None:
    """Admission is a function of sojourn + now only; prompt size cannot enter (Req 3.1).

    ``observe_and_decide`` takes exactly ``self``, ``sojourn_ms`` and the keyword-only ``now`` —
    there is no prompt-size parameter, so holding sojourn fixed yields identical decisions whatever
    a (non-existent) size would be. We assert the signature structurally.
    """
    import inspect

    sig = inspect.signature(CoDelController.observe_and_decide)
    params = list(sig.parameters)
    assert params == ["self", "sojourn_ms", "now"], (
        f"observe_and_decide must depend on sojourn + now only, got {params}"
    )
    # Metamorphic sanity: same (sojourn, now) on fresh controllers -> identical decision.
    a = _controller().observe_and_decide(20.0, now=5.0)
    b = _controller().observe_and_decide(20.0, now=5.0)
    assert a == b


def test_backoff_contracts_as_sqrt_count() -> None:
    """``drop_next_at`` advances by ``interval / sqrt(count)`` on each subsequent drop (Req 1.3)."""
    params = CoDelParams()
    controller = CoDelController(params, clock=lambda: 0.0)
    interval = params.interval_ms
    # Start the excursion.
    controller.observe_and_decide(params.target_ms + 1.0, now=0.0)
    # First drop fires once a full Interval has elapsed; count -> 1, drop_next_at = now + interval.
    first_drop_time = interval + 0.0
    d1 = controller.observe_and_decide(params.target_ms + 1.0, now=first_drop_time)
    assert d1.reason is CoDelReason.SHED_BACKOFF
    assert controller.count == 1
    assert math.isclose(d1.next_drop_at, first_drop_time + interval)
    # Second drop: advance to the scheduled drop time; count -> 2, gap contracts by sqrt(2).
    d2 = controller.observe_and_decide(params.target_ms + 1.0, now=d1.next_drop_at)
    assert d2.reason is CoDelReason.SHED_BACKOFF
    assert controller.count == 2
    assert math.isclose(d2.next_drop_at, d1.next_drop_at + interval / math.sqrt(2))
    # Third drop: count -> 3, gap contracts by sqrt(3).
    d3 = controller.observe_and_decide(params.target_ms + 1.0, now=d2.next_drop_at)
    assert controller.count == 3
    assert math.isclose(d3.next_drop_at, d2.next_drop_at + interval / math.sqrt(3))


def test_between_scheduled_drops_admits() -> None:
    """While dropping but before the next scheduled drop, requests are admitted (control law)."""
    params = CoDelParams()
    controller = CoDelController(params, clock=lambda: 0.0)
    controller.observe_and_decide(params.target_ms + 1.0, now=0.0)
    first_drop = controller.observe_and_decide(params.target_ms + 1.0, now=params.interval_ms)
    assert first_drop.admit is False
    # Just before the next scheduled drop: admit.
    between = controller.observe_and_decide(
        params.target_ms + 1.0,
        now=first_drop.next_drop_at - 1.0,
    )
    assert between.admit is True
    assert between.reason is CoDelReason.ADMIT


def test_reset_clears_state() -> None:
    params = CoDelParams()
    controller = CoDelController(params, clock=lambda: 0.0)
    controller.observe_and_decide(params.target_ms + 1.0, now=0.0)
    controller.observe_and_decide(params.target_ms + 1.0, now=params.interval_ms + 1.0)
    assert controller.dropping is True
    controller.reset()
    assert controller.dropping is False
    assert controller.first_above_at is None
    assert controller.count == 0
    assert controller.drop_next_at == 0.0


def test_decision_is_frozen() -> None:
    decision = CoDelDecision(admit=True, reason=CoDelReason.ADMIT, next_drop_at=0.0)
    try:
        decision.admit = False  # type: ignore[misc]
    except AttributeError:
        return
    raise AssertionError("CoDelDecision must be frozen")
