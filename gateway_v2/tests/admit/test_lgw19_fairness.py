"""Per-tenant fairness tests (R2-07 / GW19), task 8.4.

Covers the per-owner registry inside :class:`gateway_v2.admit.admission.AdmissionController`:

* **Property 3 — per-tenant fairness** (task 8.4): a saturating owner cannot push another owner's
  shed rate above that owner's own fair share. Fairness is structural, not emergent: each owner has
  its own :class:`CoDelController`, so one owner's observations only ever mutate its own state. The
  invariant is asserted as *isolation*: owner B's sequence of admit/shed decisions is byte-identical
  whether or not owner A is saturating in parallel.
* **Guard-owner parity** (Req 1.7): the guard owner gets a controller from the SAME factory as
  every other owner — no special global path.

House idiom: seeded ``random.Random`` loop of >= 10,000 iterations with the seed logged; NO
``hypothesis``. Async is driven by a local ``_run`` helper (``asyncio.run``). Test files are not
under the import-linter layer contract.
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable

from gateway_v2.admit.admission import AdmissionController
from gateway_v2.admit.codel import CoDelParams
from gateway_v2.admit.grant import Admitted
from gateway_v2.admit.metrics import AdmissionMetrics
from gateway_v2.admit.quota import AdmissionBounds
from gateway_v2.admit.supervisor import WorkerSupervisor

_ITERATIONS = 10_000


def _run[T](coro: Awaitable[T]) -> T:
    return asyncio.run(coro)  # type: ignore[arg-type]


def _bounds(depth: int = 1_000_000) -> AdmissionBounds:
    """Generous bounds so admission is decided by CoDel, not the queue depth (isolation)."""
    return AdmissionBounds(
        concurrency=depth,
        request_depth=depth,
        guard_depth=depth,
        dispatch_depth=depth,
        egress_depth=depth,
        audit_depth=depth,
    )


def _controller(box: dict[str, float]) -> AdmissionController:
    return AdmissionController(
        bounds=_bounds(),
        params=CoDelParams(),
        rng=random.Random(0),
        supervisor=WorkerSupervisor(),
        metrics=AdmissionMetrics(),
        drain_window_s=1.0,
        clock=lambda: box["t"],
    )


def _is_shed(outcome: object) -> bool:
    return not isinstance(outcome, Admitted)


# --------------------------------------------------------------------------- #
# Task 8.4 — Property 3: per-tenant fairness
# Feature: admission-control, Property 3
# Validates: Requirements 1.1, 1.7
# --------------------------------------------------------------------------- #


def test_property3_saturating_owner_does_not_change_a_peers_shed_decisions() -> None:
    """Owner B's decisions are identical with or without owner A saturating in parallel."""
    seed = 0x19_03
    rng = random.Random(seed)
    for i in range(_ITERATIONS):
        # A fixed timeline of B's requests, each with its own sojourn (ms converted to a clock
        # offset). We replay B alone, then B interleaved with a saturating A, and compare B's
        # decision sequence.
        n_b = rng.randint(1, 12)
        b_plan = [
            (rng.uniform(0.0, 0.2), rng.uniform(0.0, 70.0)) for _ in range(n_b)
        ]  # (dt_s, sojourn_ms)

        def _replay_b_only(plan: list[tuple[float, float]]) -> list[bool]:
            box = {"t": 0.0}
            ctl = _controller(box)
            decisions: list[bool] = []
            for dt_s, sojourn_ms in plan:
                box["t"] += dt_s
                enq = box["t"] - sojourn_ms / 1000.0
                decisions.append(_is_shed(_run(ctl.admit("owner-b", "r", enq))))
            return decisions

        def _replay_b_with_a(plan: list[tuple[float, float]]) -> list[bool]:
            box = {"t": 0.0}
            ctl = _controller(box)
            decisions: list[bool] = []
            for dt_s, sojourn_ms in plan:
                # Owner A hammers the controller with sustained-overload traffic BEFORE B's
                # request at the same instant -- a saturating neighbour.
                for _ in range(rng.randint(1, 5)):
                    _run(ctl.admit("owner-a", "a", box["t"] - 0.059))
                box["t"] += dt_s
                enq = box["t"] - sojourn_ms / 1000.0
                decisions.append(_is_shed(_run(ctl.admit("owner-b", "r", enq))))
            return decisions

        alone = _replay_b_only(b_plan)
        with_neighbour = _replay_b_with_a(b_plan)
        assert alone == with_neighbour, (
            f"seed={seed:#x} iter={i} a saturating neighbour changed owner B's decisions: "
            f"alone={alone} with_a={with_neighbour} plan={b_plan}"
        )


def test_property3_per_owner_state_is_isolated() -> None:
    """Driving owner A into dropping leaves a fresh owner B at the admitting baseline."""
    seed = 0x19_03B
    rng = random.Random(seed)
    for i in range(_ITERATIONS):
        box = {"t": 0.0}
        ctl = _controller(box)
        # Push owner A well into sustained overload.
        box["t"] = 0.0
        _run(ctl.admit("owner-a", "a0", box["t"] - 0.059))  # start excursion (59 ms sojourn)
        box["t"] = 0.2
        a_shed = _is_shed(_run(ctl.admit("owner-a", "a1", box["t"] - 0.059)))
        assert a_shed, f"seed={seed:#x} iter={i} owner A was not shedding as set up"
        # Owner B, first ever request at a tiny sojourn, must be admitted -- A's drop state is not
        # shared.
        b_sojourn = rng.uniform(0.0, 4.0) / 1000.0
        b_admit = _run(ctl.admit("owner-b", "b0", box["t"] - b_sojourn))
        assert isinstance(b_admit, Admitted), (
            f"seed={seed:#x} iter={i} owner B inherited owner A's drop state"
        )


# --------------------------------------------------------------------------- #
# Guard-owner parity (Req 1.7)
# --------------------------------------------------------------------------- #


def test_guard_owner_uses_the_same_factory_as_any_owner() -> None:
    """The guard owner is just another owner_id; its controller behaves identically (Req 1.7)."""
    box = {"t": 0.0}
    ctl = _controller(box)
    # Drive the guard owner and a plain owner through the identical sojourn timeline; the guard
    # owner must shed on the same schedule (no special global path).
    for owner in ("guard", "owner-x"):
        box["t"] = 0.0
        first = _run(ctl.admit(owner, "r0", box["t"] - 0.059))  # start excursion
        assert isinstance(first, Admitted)
        box["t"] = 0.2  # past a full Interval of above-target sojourn
        shed = _run(ctl.admit(owner, "r1", box["t"] - 0.059))
        assert _is_shed(shed), f"{owner} did not shed on the same schedule as a plain owner"
