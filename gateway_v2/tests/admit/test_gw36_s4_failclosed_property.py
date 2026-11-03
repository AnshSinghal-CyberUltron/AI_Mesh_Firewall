"""Property test — fail closed on any undecidable input (GW36 S4, task 10.6).

# Feature: gw36-s4-admission-quota, Property 5: Fail closed on any undecidable input
# Validates: Requirements 1.1, 1.2, 1.4, 1.6, 2.2, 2.3, 4.5, 5.5, 6.5, 7.4

Property 5 (design §P5): for any undecidable input the decision is a REFUSAL, never an admit, and
no provider call / no budget spend happens. S4's three decision seams each own an undecidable
boundary, and the invariant is the SAME at all three — refuse, change nothing:

* **boot q_safe seam** — a blank / whitespace / non-decimal / non-positive ``AMF_Q_SAFE`` (or a
  ``derive_bounds`` that raises ``CapacityUnset`` / ``CapacityUnavailable``) refuses to start with
  ``ProfileError`` and builds no dependency, applying no default and no literal (Req 1.1, 1.2, 1.4,
  1.6, 2.2, 2.3).
* **request-path owner / budget seam** — an unknown / missing owner, or an org with no served
  ``BudgetRecord`` (``component_for`` → ``None``), refuses the request with ``budget_unavailable``
  (NEVER ``shared_state_unavailable``), spends zero tokens, and never reaches the delegate's
  ``evaluate`` — so no provider call and no spend occur (Req 4.5, 5.5, 6.5).
* **state-path applier seam** — a drift or unverifiable BUDGET delta (a record ``record_of``
  rejects: body ``org_id`` ≠ key, negative ``pool_tokens``, wrong kind, deleted) is refused with
  ``StoreDataUnavailable``, leaving the served records and the re-acquire hook unchanged / unfired
  so the cursor stays put and the next round retries (Req 7.4).

The test is a single seeded loop over a TAGGED UNION of undecidable inputs: each iteration draws a
random tag, draws a random instance of that tag's undecidable input, runs the matching small
harness, and asserts the one common post-condition — ``outcome is a refusal and no admit / no
provider-visible spend occurred``. Every tag is driven many times across ``>= 10,000`` iterations
and the final distribution is asserted so no tag is silently skipped.

House idiom (as in ``test_gw36_s4_gate_order_property.py`` / ``test_gw36_s4_gcra_cache_property.py``
/ ``test_lgw06_gcra.py``): a seeded ``random.Random`` loop with the seed logged in every assertion
message; ``_ITERATIONS = 10_000``; NO ``hypothesis``; async driven by ``asyncio.run``. Test files
are not under the import-linter layer contract, so importing ``edge`` + ``admit`` here is fine. The
collaborators (fake GCRA cache, fake registry, spy component, the ``BudgetSnapshot`` + signed
records) are the SAME doubles the sibling S4 tests use.
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import Mapping
from typing import cast

import pytest

from gateway_v2.admit.budget_state import BudgetRecord, BudgetSnapshot, budget_applier
from gateway_v2.admit.gcra_cache import GcraCache
from gateway_v2.admit.grant import Admitted, BudgetVerdict
from gateway_v2.admit.quota_registry import QuotaRegistry
from gateway_v2.domain import posture
from gateway_v2.domain.identity import Principal
from gateway_v2.domain.state import (
    Cursor,
    Manifest,
    SignedRecord,
    StateKind,
    StateOp,
    StoreDataUnavailable,
    Version,
)
from gateway_v2.edge.production_profile import ProfileError, _resolve_q_safe
from gateway_v2.edge.quota_gate import QuotaGate
from gateway_v2.runtime.state_feed import FeedRound
from gateway_v2.runtime.state_sig import make_record

_ITERATIONS = 10_000

_SECRET = b"gw36-s4-failclosed-property-secret"

# The three undecidable-input tags of the union.
_TAG_Q_SAFE = "q_safe"
_TAG_OWNER = "owner"
_TAG_DELTA = "delta"
_TAGS = (_TAG_Q_SAFE, _TAG_OWNER, _TAG_DELTA)


# --------------------------------------------------------------------------- #
# Tag "q_safe": a bad AMF_Q_SAFE refuses to start with ProfileError (Req 1.1/1.2/1.4/1.6/2.2/2.3)
# --------------------------------------------------------------------------- #


def _draw_bad_q_safe(rng: random.Random) -> Mapping[str, str]:
    """Draw an env mapping whose ``AMF_Q_SAFE`` is undecidable, so ``_resolve_q_safe`` refuses.

    A tagged sub-union over the whole rejected string-space ``_resolve_q_safe`` guards:

    * absent — the key is simply not present (blank → "missing");
    * blank / whitespace-only — ``""`` or runs of spaces/tabs/newlines;
    * non-decimal — a token that ``float()`` rejects;
    * non-positive — ``0`` or a negative decimal (``<= 0`` is undecidable for a rate).

    All four land on the same post-condition: ``ProfileError`` raised, no ``q_safe`` returned.
    """
    kind = rng.choice(("absent", "blank", "non_decimal", "non_positive"))
    if kind == "absent":
        # No AMF_Q_SAFE key at all; an unrelated var may or may not be present.
        return {} if rng.random() < 0.5 else {"AMF_WORKERS": "4"}
    if kind == "blank":
        raw = rng.choice(("", " ", "   ", "\t", "\n", " \t \n "))
        return {"AMF_Q_SAFE": raw}
    if kind == "non_decimal":
        raw = rng.choice(("abc", "1.2.3", "ten", "1,000", "0x10", "nan%", "--3", "1e", "+", "."))
        return {"AMF_Q_SAFE": raw}
    # non_positive: 0, negatives, and 0.0 in various spellings (all <= 0 → undecidable rate).
    raw = rng.choice(("0", "0.0", "-0.0", "-1", "-0.5", f"-{rng.uniform(0.01, 999.0):.4f}", "-1e3"))
    return {"AMF_Q_SAFE": raw}


def _run_q_safe_tag(rng: random.Random, seed: int, i: int) -> None:
    """Refuse to start on a bad q_safe: ``ProfileError`` raised, no value returned (no deps built).

    The common post-condition for this seam — "refuse, build nothing" — is that ``_resolve_q_safe``
    (run before any component is constructed in ``apply_production``) raises ``ProfileError`` rather
    than returning a (defaulted / guessed) float. No ``q_safe`` return means ``apply_production``
    never reaches ``derive_bounds`` or any ``AdmissionController`` / ``QuotaGate`` construction, so
    no dependency is built and startup does not continue (Req 1.4, 2.2).
    """
    env = _draw_bad_q_safe(rng)
    ctx = f"seed={seed:#x} iter={i} tag={_TAG_Q_SAFE} env={dict(env)!r}"

    with pytest.raises(ProfileError) as excinfo:
        _resolve_q_safe(env)

    # The refusal names AMF_Q_SAFE (Req 1.1 / 1.2 boot-log contract: the missing / rejected var).
    assert "AMF_Q_SAFE" in str(excinfo.value), f"{ctx}: the refusal must name AMF_Q_SAFE"


def _draw_good_q_safe(rng: random.Random) -> Mapping[str, str]:
    """A decidable (valid, strictly-positive) ``AMF_Q_SAFE`` — the negative control for this tag."""
    value = rng.uniform(0.001, 100_000.0)
    spelling = rng.choice((repr(value), f"{value:.6f}", f"  {value:.3f}  ", str(int(value) + 1)))
    return {"AMF_Q_SAFE": spelling}


# --------------------------------------------------------------------------- #
# Tag "owner": an unknown / missing owner fails closed to budget_unavailable (Req 4.5, 5.5, 6.5)
# --------------------------------------------------------------------------- #


class _SpyComponent:
    """A ``QuotaComponent`` stand-in recording any ``evaluate`` (a spend) / ``refund`` it receives.

    For this property the component must NEVER be reached — an unknown owner short-circuits before
    the delegate. ``evaluate`` recording proves whether a spend / provider-bound call happened.
    """

    __slots__ = ("evaluate_calls", "refund_calls")

    def __init__(self) -> None:
        self.evaluate_calls: list[tuple[int, str]] = []
        self.refund_calls: list[tuple[int, str]] = []

    async def evaluate(
        self, cost: int, *, request_id: str, **_: object
    ) -> BudgetVerdict | Admitted:
        self.evaluate_calls.append((cost, request_id))
        return Admitted(owner_id="unexpected", request_id=request_id, admitted_at=0.0)

    def refund(self, cost: int, *, request_id: str) -> None:
        self.refund_calls.append((cost, request_id))


class _FakeGcraCache:
    """A ``GcraCache`` stand-in: ``None`` (rate 0 → skip the rate check) so the budget seam is under
    test, i.e. the GCRA never refuses first and mask the owner path."""

    __slots__ = ()

    def for_principal(self, key_hash: str, principal: Principal) -> None:
        del key_hash, principal
        return None


class _FakeRegistry:
    """A ``QuotaRegistry`` stand-in resolving a component only for the known orgs it was given.

    ``component_for`` records every lookup and returns ``None`` for any org not in ``components`` —
    the unknown / missing-owner path (Req 6.5) and the "org with no served ``BudgetRecord``" path
    (Req 4.5) are the same ``None`` return here.
    """

    __slots__ = ("components", "component_for_calls")

    def __init__(self, components: dict[str, _SpyComponent]) -> None:
        self.components = components
        self.component_for_calls: list[str] = []

    def component_for(self, org: str) -> _SpyComponent | None:
        self.component_for_calls.append(org)
        return self.components.get(org)


def _principal(org: str) -> Principal:
    return Principal(key_id="k", org_id=org, rate_per_s=0.0, burst=0.0, feed_seq=0)


def _run_owner_tag(rng: random.Random, seed: int, i: int) -> None:
    """An unknown owner refuses ``budget_unavailable`` with no spend and no provider call (Req 6.5).

    Build a ``QuotaGate`` over a registry that serves a (possibly empty) set of KNOWN orgs, then
    evaluate for an owner id drawn to be ABSENT from that set. The common post-condition: the result
    is a ``BudgetVerdict`` with code ``budget_unavailable`` (never ``shared_state_unavailable``),
    the delegate's ``evaluate`` was never called (zero tokens spent, zero provider calls), and no
    ``Admitted`` is ever returned on this undecidable path.
    """
    known = {f"org-{k}": _SpyComponent() for k in range(rng.randint(0, 3))}
    registry = _FakeRegistry(known)
    gate = QuotaGate(cast(GcraCache, _FakeGcraCache()), cast(QuotaRegistry, registry))

    # Draw an owner id guaranteed to be unknown (absent from `known`): either a fresh "ghost-*" id
    # or the empty string (a missing owner).
    unknown = rng.choice(
        ("", f"ghost-{rng.randint(0, 1_000_000)}", "  ", f"org-{rng.randint(4, 9)}")
    )
    cost = rng.randint(0, 10_000)
    request_id = f"zs-{i:08d}"
    ctx = (
        f"seed={seed:#x} iter={i} tag={_TAG_OWNER} owner={unknown!r} cost={cost} "
        f"known={sorted(known)!r}"
    )

    result = asyncio.run(
        gate.evaluate(
            cost,
            request_id=request_id,
            owner=unknown,
            principal=_principal(unknown or "missing"),
            key_hash=f"hash-{i}",
        )
    )

    # Refusal, never an admit.
    assert isinstance(result, BudgetVerdict), f"{ctx}: an unknown owner must refuse, never admit"
    assert result.code == posture.BUDGET_UNAVAILABLE, f"{ctx}: narrow budget_unavailable posture"
    assert result.code != posture.SHARED_STATE_UNAVAILABLE, (
        f"{ctx}: never the broad shared_state_unavailable posture"
    )
    assert result.request_id == request_id, f"{ctx}: the request id is echoed into the refusal"
    # No spend / no provider call: the delegate was never reached for ANY known org.
    for org, component in known.items():
        assert component.evaluate_calls == [], f"{ctx}: known org {org!r} was never spent"
        assert component.refund_calls == [], f"{ctx}: known org {org!r} was never refunded"
    assert registry.component_for_calls == [unknown], (
        f"{ctx}: only the named (unknown) owner was looked up, and no component was built"
    )


# --------------------------------------------------------------------------- #
# Tag "delta": a drift / unverifiable BUDGET delta refuses with StoreDataUnavailable (Req 7.4)
# --------------------------------------------------------------------------- #


def _round(*records: SignedRecord, feed_seq: int) -> FeedRound:
    """A drained ``FeedRound`` carrying ``records``; the applier reads only ``.records``."""
    return FeedRound(
        kind=StateKind.BUDGET,
        manifest=Manifest(StateKind.BUDGET, Version(1, feed_seq), feed_seq, len(records), 0, "s"),
        records=records,
        cursor=Cursor(Version(1, feed_seq), feed_seq),
        truncated=False,
    )


def _draw_bad_record(rng: random.Random, feed_seq: int) -> SignedRecord:
    """Draw a single BUDGET delta record that ``record_of`` must reject (an undecidable delta).

    A tagged sub-union over the defect classes the applier owns: a body ``org_id`` that disagrees
    with the record key, a negative ``pool_tokens``, a wrong ``StateKind``, and a deleted record.
    Each makes the drained round undecidable, so the applier raises ``StoreDataUnavailable``.
    """
    key = rng.choice(("A", "B", "acme", f"org-{rng.randint(0, 9)}"))
    kind = rng.choice(("org_mismatch", "negative_pool", "wrong_kind", "deleted"))
    if kind == "org_mismatch":
        other = key + "-other"
        return make_record(
            _SECRET, StateKind.BUDGET, key,
            {"org_id": other, "pool_tokens": rng.randint(0, 10_000)},
            Version(1, feed_seq), feed_seq,
        )
    if kind == "negative_pool":
        return make_record(
            _SECRET, StateKind.BUDGET, key,
            {"org_id": key, "pool_tokens": -rng.randint(1, 10_000)},
            Version(1, feed_seq), feed_seq,
        )
    if kind == "wrong_kind":
        # A KS record reaching the BUDGET applier is a kind mismatch (undefined shape here).
        return SignedRecord(
            StateKind.KS, key, Version(1, feed_seq), feed_seq, "h", False, StateOp.PUT, "s",
            b'{"on": true}',
        )
    # deleted: an OFFBOARD / deleted BUDGET record is an undefined shape for a pool value.
    return make_record(
        _SECRET, StateKind.BUDGET, key,
        {"org_id": key, "pool_tokens": rng.randint(0, 10_000)},
        Version(1, feed_seq), feed_seq, deleted=True, op=StateOp.OFFBOARD,
    )


def _run_delta_tag(rng: random.Random, seed: int, i: int) -> None:
    """A drift / unverifiable BUDGET delta refuses, leaving served records + hook unchanged.

    Req 7.4. Seed the snapshot with a random set of served records, then hand the applier a round
    whose LAST record is undecidable (optionally preceded by one good record, to prove the
    all-or-nothing decode leaves even a would-be-good update untouched). The common post-condition:
    the applier raises ``StoreDataUnavailable`` (refuse), every served ``BudgetRecord`` is
    byte-for-byte what it was, and the re-acquire hook never fired — so the cursor stays put and the
    next round retries.
    """
    snapshot = BudgetSnapshot()
    seeded: dict[str, BudgetRecord] = {}
    for k in range(rng.randint(0, 3)):
        rec = BudgetRecord(
            org=f"seed-{k}",
            pool_tokens=rng.randint(0, 10_000),
            generation=rng.randint(1, 50),
        )
        snapshot.replace(rec)
        seeded[rec.org] = rec
    hook_calls: list[str] = []
    apply = budget_applier(snapshot, hook_calls.append)

    feed_seq = rng.randint(1, 100)
    records: list[SignedRecord] = []
    # Optionally prepend a well-formed record for a seeded org (would advance it) to prove the whole
    # round decodes before any mutation (Req 7.4 all-or-nothing).
    if seeded and rng.random() < 0.5:
        victim = rng.choice(sorted(seeded))
        records.append(
            make_record(
                _SECRET, StateKind.BUDGET, victim,
                {"org_id": victim, "pool_tokens": seeded[victim].pool_tokens + 1},
                Version(1, feed_seq), feed_seq,
            )
        )
    records.append(_draw_bad_record(rng, feed_seq + 1))

    ctx = (
        f"seed={seed:#x} iter={i} tag={_TAG_DELTA} seeded={sorted(seeded)!r} "
        f"n_records={len(records)}"
    )

    with pytest.raises(StoreDataUnavailable):
        apply(_round(*records, feed_seq=feed_seq + 1))

    # Refusal left EVERY served record exactly as seeded (same identity object) — nothing mutated.
    for org, rec in seeded.items():
        assert snapshot.record_for(org) is rec, f"{ctx}: served record for {org!r} is untouched"
    # The re-acquire hook never fired (no generation moved; the round was refused whole).
    assert hook_calls == [], f"{ctx}: a refused delta never marks an org for re-acquire"


# --------------------------------------------------------------------------- #
# The tagged-union property: one seeded loop, one common post-condition per draw
# --------------------------------------------------------------------------- #


def test_property5_fail_closed_on_any_undecidable_input() -> None:
    """For any undecidable input across all three S4 seams: refuse, never admit, no spend / no call.

    A single seeded ``random.Random`` loop of ``>= 10,000`` iterations. Each iteration draws one of
    the three undecidable-input tags — ``q_safe`` (boot), ``owner`` (request path), ``delta`` (state
    path) — draws a random instance of that tag's undecidable input, runs the matching harness, and
    asserts the common post-condition that harness owns (``ProfileError`` + no value / a refusing
    ``BudgetVerdict`` + no spend / ``StoreDataUnavailable`` + served records unchanged). The per-tag
    draw counts are asserted at the end so every tag is exercised many times (no tag is silently
    skipped) and together they cover the full Req set
    (1.1, 1.2, 1.4, 1.6, 2.2, 2.3, 4.5, 5.5, 6.5, 7.4).
    """
    seed = 0x5_4_05
    rng = random.Random(seed)
    counts = {tag: 0 for tag in _TAGS}

    for i in range(_ITERATIONS):
        tag = rng.choice(_TAGS)
        counts[tag] += 1
        if tag == _TAG_Q_SAFE:
            _run_q_safe_tag(rng, seed, i)
        elif tag == _TAG_OWNER:
            _run_owner_tag(rng, seed, i)
        else:
            _run_delta_tag(rng, seed, i)

    # Every tag of the union was drawn many times — the property genuinely spans all three seams.
    for tag in _TAGS:
        assert counts[tag] >= 1000, (
            f"seed={seed:#x} tag {tag!r} drawn only {counts[tag]} times; the union must exercise "
            f"every seam (counts={counts})"
        )
    assert sum(counts.values()) == _ITERATIONS, (
        f"seed={seed:#x} every iteration must run exactly one tag (counts={counts})"
    )


def test_property5_decidable_q_safe_is_the_negative_control() -> None:
    """Negative control: a VALID strictly-positive ``AMF_Q_SAFE`` is decidable → it does NOT refuse.

    The fail-closed property only means something if a decidable input is admitted. Across
    ``>= 10,000`` seeded iterations a valid ``AMF_Q_SAFE`` resolves to a strictly-positive float
    without raising, so the ``q_safe`` refusal is a reaction to the undecidable input and not a
    blanket refusal (Req 1.3 boundary, bounding Req 1.1 / 1.2).
    """
    seed = 0x5_4_05_1
    rng = random.Random(seed)
    for i in range(_ITERATIONS):
        env = _draw_good_q_safe(rng)
        ctx = f"seed={seed:#x} iter={i} env={dict(env)!r}"
        q_safe = _resolve_q_safe(env)
        assert isinstance(q_safe, float), f"{ctx}: a valid q_safe resolves to a float"
        assert q_safe > 0.0, f"{ctx}: a decidable q_safe is strictly positive, not refused"
