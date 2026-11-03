"""GW36 S4, task 4.2: unit tests for ``QuotaGate`` (Req 5.3, 5.4, 6.4, 6.5).

``QuotaGate`` is the composed ``BudgetDecider`` injected as ``ChatRoute.quota``. Admission is
UPSTREAM of this gate, so inside ``evaluate`` the fixed order is per-key GCRA then per-org budget,
stopping at the first refusal:

* GCRA-reject skips the spend — the component's ``evaluate`` is never reached (Req 5.3).
* An unknown / missing owner (``component_for`` ⇒ ``None``) fails closed to ``budget_unavailable``,
  spending zero tokens and never building a component (Req 6.5).
* On admit the delegated ``Admitted`` is returned unchanged and its ``owner_id`` equals the owner
  passed to ``evaluate`` (Req 6.4).
* ``refund`` returns exactly the spent cost to the owner's component; an unknown owner is a no-op,
  so another org is never credited (Req 5.4, 6.3).
* The whole ``evaluate`` body is fail-closed: a delegate that raises refuses
  ``budget_unavailable`` and never admits (Req 5.5 bonus).

House idiom: plain pytest, ``from __future__ import annotations``, no ``hypothesis`` (example-based;
the properties are tasks 4.3 / 4.4 / 4.5), async driven by ``asyncio.run`` (per ``test_lgw06_*``).
The GCRA cache, the quota registry, and the delegated component are test doubles the test fully
controls — no store, no real lease.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from typing import cast

from gateway_v2.admit.gcra_cache import GcraCache
from gateway_v2.admit.grant import Admitted, BudgetVerdict
from gateway_v2.admit.quota_registry import QuotaRegistry
from gateway_v2.domain import posture
from gateway_v2.domain.identity import Principal
from gateway_v2.edge.quota_gate import QuotaGate

_KEY_HASH = "sha256:deadbeef"
_REQUEST_ID = "zs-req-0001"


def _run(coro: Awaitable[object]) -> object:
    return asyncio.run(coro)  # type: ignore[arg-type]


def _principal(*, org: str = "acme", rate_per_s: float = 10.0, burst: float = 5.0) -> Principal:
    """A resolved principal with a positive rate (the GCRA path is driven by the stub cache)."""
    return Principal(
        key_id="key-1",
        org_id=org,
        rate_per_s=rate_per_s,
        burst=burst,
        feed_seq=1,
    )


class _StubGcra:
    """A ``LocalGCRA`` stand-in whose ``admit()`` returns a fixed verdict and counts calls."""

    def __init__(self, *, admits: bool) -> None:
        self._admits = admits
        self.admit_calls = 0

    def admit(self) -> bool:
        self.admit_calls += 1
        return self._admits


class _FakeGcraCache:
    """A ``GcraCache`` stand-in returning a controlled GCRA (or ``None``) from ``for_principal``.

    ``gcra`` is what ``for_principal`` yields: ``None`` means rate 0 (skip the rate check), a
    ``_StubGcra`` drives the admit / reject branch. Satisfies the structural surface ``QuotaGate``
    uses; cast to ``GcraCache`` for typing only.
    """

    def __init__(self, gcra: _StubGcra | None) -> None:
        self._gcra = gcra
        self.for_principal_calls: list[tuple[str, Principal]] = []

    def for_principal(self, key_hash: str, principal: Principal) -> _StubGcra | None:
        self.for_principal_calls.append((key_hash, principal))
        return self._gcra


class _StubComponent:
    """A ``QuotaComponent`` stand-in recording ``evaluate`` / ``refund`` and returning a verdict.

    ``evaluate`` is async (matching the real façade) and returns ``outcome`` — an ``Admitted`` on
    the admit path or a ``BudgetVerdict`` otherwise; if ``raises`` is set it raises to exercise the
    fail-closed catch-all. Both methods record the ``(cost, request_id)`` they were called with so a
    test can assert the spend was skipped, delegated, or refunded with the exact cost.
    """

    def __init__(
        self,
        *,
        outcome: Admitted | BudgetVerdict | None = None,
        raises: bool = False,
    ) -> None:
        self._outcome = outcome
        self._raises = raises
        self.evaluate_calls: list[tuple[int, str]] = []
        self.refund_calls: list[tuple[int, str]] = []

    async def evaluate(self, cost: int, *, request_id: str) -> Admitted | BudgetVerdict:
        self.evaluate_calls.append((cost, request_id))
        if self._raises:
            msg = "injected delegate failure"
            raise RuntimeError(msg)
        assert self._outcome is not None
        return self._outcome

    def refund(self, cost: int, *, request_id: str) -> None:
        self.refund_calls.append((cost, request_id))


class _FakeRegistry:
    """A ``QuotaRegistry`` stand-in resolving a controlled component (or ``None``) per owner.

    ``components`` maps owner → the stub to return; a missing owner yields ``None`` (the unknown /
    missing owner path). ``component_for`` records every lookup so a test can prove the component
    was never built for an unknown owner.
    """

    def __init__(self, components: dict[str, _StubComponent] | None = None) -> None:
        self.components: dict[str, _StubComponent] = dict(components or {})
        self.component_for_calls: list[str] = []

    def component_for(self, org: str) -> _StubComponent | None:
        self.component_for_calls.append(org)
        return self.components.get(org)


def _gate(cache: _FakeGcraCache, registry: _FakeRegistry) -> QuotaGate:
    return QuotaGate(cast(GcraCache, cache), cast(QuotaRegistry, registry))


def _admitted(owner: str) -> Admitted:
    return Admitted(owner_id=owner, request_id=_REQUEST_ID, admitted_at=0.0)


# -- Admit path: GCRA admits, registry delegates, Admitted.owner_id == owner (Req 6.4) -----------


def test_admit_path_delegates_and_owner_id_equals_owner() -> None:
    """GCRA admits and the component admits: the ``Admitted`` returns with ``owner_id==owner``."""
    gcra = _StubGcra(admits=True)
    cache = _FakeGcraCache(gcra)
    component = _StubComponent(outcome=_admitted("acme"))
    registry = _FakeRegistry({"acme": component})
    gate = _gate(cache, registry)

    result = _run(
        gate.evaluate(
            7,
            request_id=_REQUEST_ID,
            owner="acme",
            principal=_principal(org="acme"),
            key_hash=_KEY_HASH,
        )
    )

    assert isinstance(result, Admitted)
    assert result.owner_id == "acme"  # Req 6.4: owner on Admitted equals the passed owner
    assert gcra.admit_calls == 1  # GCRA was consulted (order: GCRA before budget)
    assert component.evaluate_calls == [(7, _REQUEST_ID)]  # delegated with the same cost


def test_admit_path_with_no_gcra_skips_the_rate_check_and_delegates() -> None:
    """A ``None`` GCRA (rate 0, no per-key limit) skips the rate check and delegates (Req 3.5)."""
    cache = _FakeGcraCache(None)  # rate 0 -> for_principal returns None
    component = _StubComponent(outcome=_admitted("acme"))
    registry = _FakeRegistry({"acme": component})
    gate = _gate(cache, registry)

    result = _run(
        gate.evaluate(
            3,
            request_id=_REQUEST_ID,
            owner="acme",
            principal=_principal(org="acme", rate_per_s=0.0),
            key_hash=_KEY_HASH,
        )
    )

    assert isinstance(result, Admitted)
    assert component.evaluate_calls == [(3, _REQUEST_ID)]


# -- GCRA reject skips the spend (Req 5.3) -------------------------------------------------------


def test_gcra_reject_returns_rate_limited_and_skips_the_spend() -> None:
    """A GCRA reject refuses ``rate_limited`` and NEVER reaches the component spend (Req 5.3)."""
    gcra = _StubGcra(admits=False)
    cache = _FakeGcraCache(gcra)
    component = _StubComponent(outcome=_admitted("acme"))
    registry = _FakeRegistry({"acme": component})
    gate = _gate(cache, registry)

    result = _run(
        gate.evaluate(
            9,
            request_id=_REQUEST_ID,
            owner="acme",
            principal=_principal(org="acme"),
            key_hash=_KEY_HASH,
        )
    )

    assert isinstance(result, BudgetVerdict)
    assert result.code == posture.RATE_LIMITED
    assert result.request_id == _REQUEST_ID
    # The spend was skipped: the budget component's evaluate was never called (Req 5.3), and the
    # registry was never consulted (short-circuit before the budget phase).
    assert component.evaluate_calls == []
    assert registry.component_for_calls == []


# -- Unknown / missing owner fails closed to budget_unavailable (Req 6.5) ------------------------


def test_unknown_owner_fails_closed_to_budget_unavailable_and_builds_nothing() -> None:
    """No served component ⇒ ``budget_unavailable``, zero tokens, no build (Req 6.5)."""
    gcra = _StubGcra(admits=True)
    cache = _FakeGcraCache(gcra)
    registry = _FakeRegistry({})  # nothing served for "ghost"
    gate = _gate(cache, registry)

    result = _run(
        gate.evaluate(
            5,
            request_id=_REQUEST_ID,
            owner="ghost",
            principal=_principal(org="ghost"),
            key_hash=_KEY_HASH,
        )
    )

    assert isinstance(result, BudgetVerdict)
    assert result.code == posture.BUDGET_UNAVAILABLE
    assert result.code != posture.SHARED_STATE_UNAVAILABLE  # narrow posture, never the broad one
    assert registry.component_for_calls == ["ghost"]  # looked up, found nothing, no component built


# -- refund returns exactly the spent cost (Req 5.4, 6.3) ----------------------------------------


def test_refund_returns_exactly_the_spent_cost_to_the_owner() -> None:
    """``refund`` calls the owner's component with the SAME cost and request id (Req 5.4)."""
    component = _StubComponent(outcome=_admitted("acme"))
    registry = _FakeRegistry({"acme": component})
    gate = _gate(_FakeGcraCache(None), registry)

    gate.refund(42, request_id=_REQUEST_ID, owner="acme")

    assert component.refund_calls == [(42, _REQUEST_ID)]  # exact cost returned to the owner's lease


def test_refund_for_unknown_owner_is_a_noop_and_credits_no_other_org() -> None:
    """An unknown owner refund is a no-op: no other org's component is ever credited (Req 6.3)."""
    other = _StubComponent(outcome=_admitted("acme"))
    registry = _FakeRegistry({"acme": other})
    gate = _gate(_FakeGcraCache(None), registry)

    gate.refund(42, request_id=_REQUEST_ID, owner="ghost")

    assert registry.component_for_calls == ["ghost"]  # looked up the owner named, found nothing
    assert other.refund_calls == []  # the known org was never credited


# -- Fail-closed catch-all (Req 5.5 bonus) -------------------------------------------------------


def test_delegate_raising_fails_closed_to_budget_unavailable() -> None:
    """A component whose ``evaluate`` raises refuses ``budget_unavailable`` and never admits."""
    gcra = _StubGcra(admits=True)
    cache = _FakeGcraCache(gcra)
    component = _StubComponent(raises=True)
    registry = _FakeRegistry({"acme": component})
    gate = _gate(cache, registry)

    result = _run(
        gate.evaluate(
            11,
            request_id=_REQUEST_ID,
            owner="acme",
            principal=_principal(org="acme"),
            key_hash=_KEY_HASH,
        )
    )

    assert isinstance(result, BudgetVerdict)  # never an Admitted on an undecidable path
    assert result.code == posture.BUDGET_UNAVAILABLE
    assert component.evaluate_calls == [(11, _REQUEST_ID)]  # the delegate was reached, then raised
