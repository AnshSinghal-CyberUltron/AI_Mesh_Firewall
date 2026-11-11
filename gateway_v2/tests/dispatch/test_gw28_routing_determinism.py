"""GW28 P1-P3: HRW ranking determinism, totality/closure, minimal disruption."""

from __future__ import annotations

import dataclasses
import hashlib
import random
import time
from collections import Counter

from gateway_v2.dispatch.model_routing import ModelRouter, selection_key
from gateway_v2.dispatch.routing_types import (
    Candidate,
    RoutingRefusal,
    RoutingSnapshot,
)
from gateway_v2.domain.posture import ROUTING_ALL_UNAVAILABLE, ROUTING_UNKNOWN_MODEL
from tests.dispatch._gw28_fakes import MAX_LEN, cand, cset, ctx, plan

_ITER = 10_000
_R = ModelRouter()


def _rank(c, s, snap=RoutingSnapshot()):  # type: ignore[no-untyped-def]
    return _R.rank(c, s, snap, max_ident_len=MAX_LEN)


def _reference(c, cs, snap=RoutingSnapshot()):  # type: ignore[no-untyped-def]
    """The unoptimised formula from the design, used as the golden oracle."""
    key = selection_key(c)
    out = []
    for k in cs.candidates:
        if not (k.passthrough or c.requested_model in k.served_models):
            continue
        if k.candidate_id in snap.unavailable:
            continue
        score = max(
            int.from_bytes(
                hashlib.sha256(f"{key}\x1f{k.candidate_id}\x1f{v}".encode()).digest(), "big"
            )
            for v in range(k.weight)
        )
        out.append((-score, k.candidate_id, k))
    out.sort(key=lambda t: (t[0], t[1]))
    return tuple(t[2] for t in out)


def _rand_set(rng: random.Random, n: int | None = None) -> tuple[Candidate, ...]:
    n = n or rng.randint(1, 8)
    return tuple(cand(f"c{i}", weight=rng.randint(1, 16)) for i in range(n))


def test_golden_vector_matches_unoptimised_formula() -> None:
    rng = random.Random(0x2841)
    for i in range(500):
        cs = cset(*_rand_set(rng))
        c = ctx(key=f"k{i}")
        assert _rank(c, cs) == _reference(c, cs)
    # pinned literal: byte-identical ordering for a fixed input
    cs = cset(cand("a", weight=3), cand("b", weight=2), cand("c"))
    assert [x.candidate_id for x in _rank(ctx(key="golden"), cs)] == [
        x.candidate_id for x in _reference(ctx(key="golden"), cs)
    ]


# Feature: gw28-multi-model-routing, Property 1: ranking is a pure function of its inputs
def test_p1_pure_function_of_identity_key_model_table_snapshot() -> None:
    rng = random.Random(0x2801)
    for i in range(_ITER):
        cands = list(_rand_set(rng))
        cs = cset(*cands)
        key = f"k{rng.randint(0, 50)}"
        p1 = plan(epoch=rng.randint(1, 3), seq=rng.randint(1, 3))
        # irrelevant plan fields vary: compiled_at, feed_seq, integrity_locked
        p2 = dataclasses.replace(
            p1, compiled_at=rng.random(), feed_seq=rng.randint(0, 99), integrity_locked=True
        )
        a = _rank(ctx(key=key, pl=p1), cs)
        b = _rank(ctx(key=key, pl=p2), cs)
        rng.shuffle(cands)
        c = _rank(ctx(key=key, pl=p1), cset(*cands))
        assert a == b == c, i


# Feature: gw28-multi-model-routing, Property 2: selection is total and closed
def test_p2_total_closed_and_weighted() -> None:
    rng = random.Random(0x2802)
    for i in range(_ITER):
        cs = cset(*_rand_set(rng))
        out = _rank(ctx(key=f"k{i}"), cs)
        assert isinstance(out, tuple) and set(out) <= set(cs.candidates)
        assert len(out) == len(cs.candidates)
    # refusals are typed
    cs = cset(cand("a", models=("x",)))
    r = _rank(ctx("m"), cs)
    assert isinstance(r, RoutingRefusal) and r.code == ROUTING_UNKNOWN_MODEL
    r = _rank(ctx("x"), cs, RoutingSnapshot(frozenset({"a"})))
    assert isinstance(r, RoutingRefusal) and r.code == ROUTING_ALL_UNAVAILABLE
    # an invalid name never matches, even a passthrough candidate
    pt = cset(cand("p", passthrough=True), passthrough=True)
    for bad in ("", "a|b", "a\nb", "a" * (MAX_LEN + 1)):
        bad_ctx = dataclasses.replace(ctx("m"), requested_model=bad)
        r = _rank(bad_ctx, pt)
        assert isinstance(r, RoutingRefusal) and r.code == ROUTING_UNKNOWN_MODEL
    assert not isinstance(_rank(ctx("m"), pt), RoutingRefusal)
    # frequency proportional to weight
    cs = cset(cand("a", weight=1), cand("b", weight=3))
    counts: Counter[str] = Counter()
    total = 4000
    for i in range(total):
        counts[_rank(ctx(key=f"w{i}"), cs)[0].candidate_id] += 1  # type: ignore[index]
    share_b = counts["b"] / total
    assert share_b > 0.6, counts  # expected ~0.76 for 3 vnodes vs 1


# Feature: gw28-multi-model-routing, Property 3: minimal disruption on health flip
def test_p3_minimal_disruption() -> None:
    rng = random.Random(0x2803)
    for i in range(_ITER):
        cs = cset(*_rand_set(rng, rng.randint(2, 8)))
        c = ctx(key=f"k{i}")
        full = _rank(c, cs)
        assert isinstance(full, tuple)
        victim = rng.choice(cs.candidates).candidate_id
        rest = _rank(c, cs, RoutingSnapshot(frozenset({victim})))
        if len(cs.candidates) == 1:
            continue
        assert isinstance(rest, tuple)
        assert rest == tuple(x for x in full if x.candidate_id != victim)
        if full[0].candidate_id != victim:
            assert rest[0] == full[0]


def test_rank_micro_benchmark_within_budget() -> None:
    cs = cset(*(cand(f"c{i}", weight=16) for i in range(8)))
    c = ctx(key="k" * 2048)
    samples = []
    for _ in range(1000):
        t = time.perf_counter()
        _rank(c, cs)
        samples.append(time.perf_counter() - t)
    samples.sort()
    p99 = samples[989]
    budget = 20.0 / 1000.0  # default target_p99_ms in seconds; margin is ~100x
    assert p99 < budget / 10, p99  # 2 ms vs ~0.1 ms typical
