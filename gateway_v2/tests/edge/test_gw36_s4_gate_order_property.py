"""Property test — gate short-circuit order (GW36 S4, task 4.5).

# Feature: gw36-s4-admission-quota, Property 8: Gate short-circuit order
# Validates: Requirements 2.4, 2.6, 5.1, 5.2, 5.3, 5.6

Property 8 (design §P8): for any triple of gate verdicts ``(admission, gcra, budget)`` the gate
chain evaluates **admission first, then GCRA, then budget**, and stops at the first gate that
refuses:

* a shed refuses without evaluating GCRA or budget;
* a GCRA reject refuses without spending budget;
* budget is spent only when admission admitted AND GCRA allowed;
* only then does the request proceed to the provider (an ``Admitted`` is returned).

No downstream gate is evaluated and no budget is spent after an earlier refusal.

What each portion of the triple this file covers, and why
---------------------------------------------------------
Admission is UPSTREAM of ``QuotaGate`` — the §10.4 request lifecycle
(:class:`~gateway_v2.edge.openai_chat.ChatSurface.run_lifecycle`) runs ``admission.admit`` (phase 5)
and only when it returns ``Admitted`` does it reach the budget phase that calls ``quota.evaluate``
(the composed :class:`~gateway_v2.edge.quota_gate.QuotaGate`). So the property spans two seams and
is asserted at the level that is faithful AND testable without standing up a full ASGI server:

1. **admission → (GCRA / budget)** — a thin lifecycle harness (:func:`_run_admission_then_quota`)
   that mirrors the lifecycle's documented order EXACTLY: call the (spy) admission; on a
   ``ShedVerdict`` short-circuit and NEVER touch ``quota.evaluate``; only on ``Admitted`` call the
   (spy) quota. A shared call-order list proves admission is consulted first and that a shed means
   ``QuotaGate.evaluate`` (and therefore every gate inside it) is never reached and no budget is
   spent.
2. **GCRA → budget inside ``QuotaGate.evaluate``** — the REAL
   :class:`~gateway_v2.edge.quota_gate.QuotaGate` driven with instrumented spy collaborators: a spy
   :class:`~gateway_v2.admit.gcra_cache.GcraCache` returning a stub ``LocalGCRA`` whose ``admit()``
   records the call and returns the drawn GCRA verdict, and a spy
   :class:`~gateway_v2.admit.quota_registry.QuotaRegistry` returning a stub
   :class:`~gateway_v2.admit.quota.QuotaComponent` whose ``evaluate`` records the call (a spend) and
   returns the drawn budget outcome. The shared call-order list proves the GCRA spy is consulted
   before the budget spy, that a GCRA reject means the component's ``evaluate`` is NEVER called (no
   spend), and that a spend happens exactly once only when GCRA allowed.

Together the harness + the real gate cover the full ``admission → GCRA → budget`` triple: the 8
verdict combinations are enumerated and driven many times under a seeded ``random.Random`` loop.

House idiom (as in ``tests/admit/test_gw36_s4_gcra_cache_property.py`` /
``tests/admit/test_lgw06_gcra.py``): a seeded ``random.Random`` loop of ``>= 10,000`` iterations
with the seed logged in every assertion message; NO ``hypothesis``. Test files are not under the
import-linter layer contract, so importing ``edge`` + ``admit`` here is fine.
"""

from __future__ import annotations

import asyncio
import random
from typing import cast

from gateway_v2.admit.gcra_cache import GcraCache
from gateway_v2.admit.grant import Admitted, BudgetVerdict, ShedVerdict, budget_verdict
from gateway_v2.admit.quota import QuotaComponent
from gateway_v2.admit.quota_registry import QuotaRegistry
from gateway_v2.domain.identity import Principal
from gateway_v2.domain.posture import BUDGET_UNAVAILABLE, OVERLOAD_SHED, RATE_LIMITED
from gateway_v2.edge.quota_gate import QuotaGate

_ITERATIONS = 10_000

_OWNER = "acme"
_KEY_HASH = "hash-acme"
_REQUEST_ID = "req-1"
_COST = 7


def _principal() -> Principal:
    """A minimal valid principal; its fields are irrelevant to the spy GCRA (which is fixed)."""
    return Principal(key_id="k", org_id=_OWNER, rate_per_s=10.0, burst=5, feed_seq=0)


# --------------------------------------------------------------------------- #
# Instrumented spies — every gate records (gate_name, event) into a shared log
# --------------------------------------------------------------------------- #


class _SpyGcra:
    """A stub ``LocalGCRA`` whose ``admit()`` records the call and returns a fixed verdict."""

    __slots__ = ("_calls", "_verdict")

    def __init__(self, *, allow: bool, calls: list[str]) -> None:
        self._verdict = allow
        self._calls = calls

    def admit(self, *, now: float | None = None) -> bool:
        del now
        self._calls.append("gcra")
        return self._verdict


class _SpyGcraCache:
    """A spy ``GcraCache`` yielding the stub GCRA (or ``None`` to model rate 0 = no per-key limit).

    ``for_principal`` records nothing itself — the recorded event is the stub GCRA's ``admit()``
    call, which is what "the GCRA gate was evaluated" means. Returning ``None`` models Req 3.5
    (rate 0 → skip the rate check) and lets the budget gate run.
    """

    __slots__ = ("_gcra",)

    def __init__(self, gcra: _SpyGcra | None) -> None:
        self._gcra = gcra

    def for_principal(self, key_hash: str, principal: Principal) -> _SpyGcra | None:
        del key_hash, principal
        return self._gcra


class _SpyComponent:
    """A stub ``QuotaComponent`` whose ``evaluate`` records a spend and returns a fixed outcome."""

    __slots__ = ("_admit", "_calls")

    def __init__(self, *, admit: bool, calls: list[str]) -> None:
        self._admit = admit
        self._calls = calls

    async def evaluate(
        self, cost: int, *, request_id: str, **_: object
    ) -> BudgetVerdict | Admitted:
        del cost
        # Reaching evaluate IS the spend (the component's internal lease decrement happens here);
        # recording it proves whether a spend occurred.
        self._calls.append("budget")
        if self._admit:
            return Admitted(owner_id=_OWNER, request_id=request_id, admitted_at=0.0)
        return budget_verdict(request_id)


class _SpyRegistry:
    """A spy ``QuotaRegistry`` yielding the stub component (or ``None`` for an unknown owner)."""

    __slots__ = ("_component",)

    def __init__(self, component: _SpyComponent | None) -> None:
        self._component = component

    def component_for(self, org: str) -> _SpyComponent | None:
        del org
        return self._component


class _SpyAdmission:
    """A spy admission decider mirroring the GW19 ``AdmissionController.admit`` surface.

    Records its ``admit`` call into the shared log and returns ``Admitted`` (pass) or a
    ``ShedVerdict`` (refuse) per the drawn verdict.
    """

    __slots__ = ("_admit", "_calls")

    def __init__(self, *, admit: bool, calls: list[str]) -> None:
        self._admit = admit
        self._calls = calls

    async def admit(
        self, owner_id: str, request_id: str, enqueued_at: float
    ) -> Admitted | ShedVerdict:
        del enqueued_at
        self._calls.append("admission")
        if self._admit:
            return Admitted(owner_id=owner_id, request_id=request_id, admitted_at=0.0)
        return ShedVerdict(
            code=OVERLOAD_SHED, retry_after_s=1.0, should_retry=False, request_id=request_id
        )


# --------------------------------------------------------------------------- #
# Lifecycle harness — admission FIRST, then (only if admitted) the quota gate
# --------------------------------------------------------------------------- #


async def _run_admission_then_quota(
    admission: _SpyAdmission, gate: QuotaGate, principal: Principal
) -> Admitted | ShedVerdict | BudgetVerdict:
    """Mirror the §10.4 lifecycle order: admission (phase 5) gates the quota phase (phase 6).

    This is the SAME control flow as ``ChatSurface.run_lifecycle`` — ``admission.admit`` runs first,
    a ``ShedVerdict`` short-circuits the request and the budget phase is NEVER reached, and only an
    ``Admitted`` falls through to ``quota.evaluate`` (the composed :class:`QuotaGate`). No provider
    call and no budget spend can happen after a shed because ``evaluate`` is simply not invoked.
    """
    verdict = await admission.admit(
        owner_id=_OWNER, request_id=_REQUEST_ID, enqueued_at=0.0
    )
    if isinstance(verdict, ShedVerdict):
        return verdict  # short-circuit: the quota gate is never reached (Req 2.4, 2.6, 5.1).
    return await gate.evaluate(
        _COST,
        request_id=_REQUEST_ID,
        owner=_OWNER,
        principal=principal,
        key_hash=_KEY_HASH,
    )


def test_property8_gate_short_circuit_order() -> None:
    """For all 8 ``(admission, gcra, budget)`` verdict triples: fixed order, first-refusal stop.

    Enumerate every combination of ``admission ∈ {pass, refuse}``, ``gcra ∈ {pass, refuse}``,
    ``budget ∈ {pass, refuse}`` across ``>= 10,000`` seeded iterations. For each triple, drive the
    real :class:`QuotaGate` (GCRA → budget) through the lifecycle harness (admission → gate) with
    instrumented spies and assert, from the shared call-order log and the returned verdict:

    * **admission refuse (shed)** → log is exactly ``["admission"]``: GCRA and budget are NEVER
      evaluated, no spend, a ``ShedVerdict`` is returned (Req 2.4, 2.6, 5.1).
    * **admission pass, GCRA refuse** → log is ``["admission", "gcra"]``: budget is NEVER evaluated
      (no spend), a ``rate_limited`` ``BudgetVerdict`` is returned (Req 5.2, 5.3).
    * **admission pass, GCRA pass, budget refuse** → log is ``["admission", "gcra", "budget"]``: the
      budget gate IS evaluated exactly once (a spend occurred), a ``budget_unavailable``
      ``BudgetVerdict`` is returned (Req 5.2, 5.3).
    * **all pass** → log is ``["admission", "gcra", "budget"]``: budget spent exactly once and an
      ``Admitted`` is returned — the request proceeds to the provider (Req 5.6).

    The order is read directly off the shared log, so "admission before GCRA before budget" and "no
    downstream gate after a refusal" are both proved structurally, not merely by the final code.
    """
    seed = 0x5_4_08
    rng = random.Random(seed)
    combos = [(a, g, b) for a in (True, False) for g in (True, False) for b in (True, False)]

    for i in range(_ITERATIONS):
        admission_pass, gcra_pass, budget_pass = rng.choice(combos)
        calls: list[str] = []

        admission = _SpyAdmission(admit=admission_pass, calls=calls)
        gcra = _SpyGcra(allow=gcra_pass, calls=calls)
        component = _SpyComponent(admit=budget_pass, calls=calls)
        gate = QuotaGate(
            cast(GcraCache, _SpyGcraCache(gcra)),
            cast(QuotaRegistry, _SpyRegistry(cast(QuotaComponent, component))),
        )

        verdict = asyncio.run(_run_admission_then_quota(admission, gate, _principal()))
        ctx = (
            f"seed={seed:#x} iter={i} triple=(admission={admission_pass} gcra={gcra_pass} "
            f"budget={budget_pass}) calls={calls} verdict={verdict!r}"
        )

        if not admission_pass:
            # Shed at the door: no downstream gate evaluated, no spend (Req 2.4, 2.6, 5.1).
            assert calls == ["admission"], f"{ctx}: shed must short-circuit before GCRA/budget"
            assert "budget" not in calls, f"{ctx}: no spend after an admission shed"
            assert isinstance(verdict, ShedVerdict), f"{ctx}: a shed returns a ShedVerdict"
            assert verdict.code == OVERLOAD_SHED, f"{ctx}: shed carries overload_shed"
            continue

        if not gcra_pass:
            # GCRA reject: budget is never evaluated — no spend (Req 5.2, 5.3).
            assert calls == ["admission", "gcra"], (
                f"{ctx}: GCRA reject must stop before the budget spend"
            )
            assert "budget" not in calls, f"{ctx}: no spend after a GCRA reject"
            assert isinstance(verdict, BudgetVerdict), f"{ctx}: GCRA reject returns a verdict"
            assert verdict.code == RATE_LIMITED, f"{ctx}: GCRA reject carries rate_limited"
            continue

        # Admission admitted AND GCRA allowed: budget is spent exactly once (Req 5.2, 5.6).
        assert calls == ["admission", "gcra", "budget"], (
            f"{ctx}: all three gates run in order when admission + GCRA pass"
        )
        assert calls.count("budget") == 1, f"{ctx}: budget spent exactly once"

        if budget_pass:
            # Only now does the request proceed to the provider (Req 5.6).
            assert isinstance(verdict, Admitted), f"{ctx}: all-pass proceeds (Admitted)"
            assert verdict.owner_id == _OWNER, f"{ctx}: Admitted attributed to the owner"
        else:
            assert isinstance(verdict, BudgetVerdict), f"{ctx}: budget refuse returns a verdict"
            assert verdict.code == BUDGET_UNAVAILABLE, (
                f"{ctx}: budget refuse carries budget_unavailable"
            )


def test_property8_rate_zero_skips_gcra_and_reaches_budget() -> None:
    """Rate 0 (``for_principal`` → ``None``) skips the GCRA check and the budget gate still runs.

    Req 3.5 / 5.1: a ``None`` GCRA (the principal's ``rate_per_s == 0``, no per-key limit) is not a
    refusal — the chain skips the rate check and proceeds to the budget gate. So with admission
    admitted and a ``None`` GCRA, the log carries NO ``"gcra"`` event (nothing to evaluate) but the
    budget gate IS evaluated exactly once. Driven across ``>= 10,000`` seeded iterations with the
    budget outcome drawn at random.
    """
    seed = 0x5_4_08_0
    rng = random.Random(seed)
    for i in range(_ITERATIONS):
        budget_pass = rng.random() < 0.5
        calls: list[str] = []

        admission = _SpyAdmission(admit=True, calls=calls)
        component = _SpyComponent(admit=budget_pass, calls=calls)
        gate = QuotaGate(
            cast(GcraCache, _SpyGcraCache(None)),  # rate 0 → no per-key GCRA
            cast(QuotaRegistry, _SpyRegistry(cast(QuotaComponent, component))),
        )

        verdict = asyncio.run(_run_admission_then_quota(admission, gate, _principal()))
        ctx = (
            f"seed={seed:#x} iter={i} budget={budget_pass} calls={calls} verdict={verdict!r}"
        )

        # No GCRA event (None skips the check), budget evaluated exactly once (Req 3.5, 5.1).
        assert calls == ["admission", "budget"], f"{ctx}: None GCRA skips the rate check"
        assert calls.count("budget") == 1, f"{ctx}: budget still spent exactly once"
        if budget_pass:
            assert isinstance(verdict, Admitted), f"{ctx}: proceeds on budget pass"
        else:
            assert isinstance(verdict, BudgetVerdict), f"{ctx}: budget refuse returns a verdict"
            assert verdict.code == BUDGET_UNAVAILABLE, f"{ctx}: budget_unavailable on refuse"


def test_property8_unknown_owner_fails_closed_before_spend() -> None:
    """An unknown owner (``component_for`` → ``None``) fails closed to ``budget_unavailable``.

    Req 5.3 / 6.5: when admission admitted and GCRA allowed but the owner has no served component,
    the gate refuses narrowly with ``budget_unavailable`` and NO spend occurs (the ``"budget"``
    event never appears because ``evaluate`` is never delegated to). Driven across ``>= 10,000``
    seeded iterations; the GCRA always allows so the budget phase is the one under test.
    """
    seed = 0x5_4_08_1
    rng = random.Random(seed)
    for i in range(_ITERATIONS):
        calls: list[str] = []
        gcra = _SpyGcra(allow=True, calls=calls)
        gate = QuotaGate(
            cast(GcraCache, _SpyGcraCache(gcra)),
            cast(QuotaRegistry, _SpyRegistry(None)),  # unknown owner → no component
        )
        admission = _SpyAdmission(admit=True, calls=calls)

        verdict = asyncio.run(_run_admission_then_quota(admission, gate, _principal()))
        ctx = f"seed={seed:#x} iter={i} calls={calls} verdict={verdict!r}"

        # GCRA evaluated, then the missing component fails closed BEFORE any spend (Req 5.3, 6.5).
        assert calls == ["admission", "gcra"], f"{ctx}: no spend on an unknown owner"
        assert "budget" not in calls, f"{ctx}: a missing component never spends"
        assert isinstance(verdict, BudgetVerdict), f"{ctx}: unknown owner returns a verdict"
        assert verdict.code == BUDGET_UNAVAILABLE, (
            f"{ctx}: never shared_state_unavailable — narrow budget_unavailable"
        )
        # rng is advanced so the seed drives a deterministic-but-varied loop.
        rng.random()
