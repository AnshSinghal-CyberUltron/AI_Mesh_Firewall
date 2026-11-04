# Feature: gw29-killswitch-isolation, Property 12: auto-isolation tightens only, needs a fallback
# Validates: Requirements 5.3, 5.6, 6.4 (never override an operator; needs a destination)
"""P12 over random fallback/kill/isolation/constraint worlds."""

from __future__ import annotations

import random

from gateway_v2.domain.incident import (
    IncidentEventKind as K,
)
from gateway_v2.domain.incident import (
    IsolationOrigin,
    ModelProfile,
    RequestConstraints,
)
from tests.admit._lgw29_support import Rig, iso_body, ks, load

SEED = 29012
ITERATIONS = 3_000
_TAGS = ("hipaa", "pci", "gdpr")


def _tags(rng: random.Random) -> frozenset[str]:
    return frozenset(t for t in _TAGS if rng.random() < 0.5)


def _oracle_eligible(rig: Rig, fallback: str | None, constraints: RequestConstraints,
                     fb_killed: bool, fb_isolated: bool) -> bool:
    if fallback is None or fb_killed or fb_isolated or not constraints.known:
        return False
    pa, pb = rig.profiles.get("a"), rig.profiles.get(fallback)
    if pa is None or pb is None:
        return False
    return (pb.compliance_tags >= pa.compliance_tags | constraints.required_tags
            and pb.sensitivity_ceiling >= max(pa.sensitivity_ceiling, constraints.sensitivity_rank))


def test_property_12_tightening_only_and_an_eligible_destination() -> None:
    rng = random.Random(SEED)
    isolated = skipped = 0
    for i in range(ITERATIONS):
        tag = f"seed={SEED} i={i}"
        fallback = rng.choice(("b", "c", None))
        profiles = {name: ModelProfile(name, "p", _tags(rng), rng.randint(0, 3))
                    for name in ("a", "b", "c") if rng.random() < 0.9}
        constraints = RequestConstraints(rng.random() < 0.8, None,
                                         _tags(rng) if rng.random() < 0.3 else frozenset(),
                                         rng.randint(0, 3))
        rig = Rig(fallbacks={"a": fallback} if fallback else {}, profiles=profiles,
                  constraints=constraints)
        records = []
        fb_killed = fallback is not None and rng.random() < 0.2
        fb_isolated = fallback is not None and rng.random() < 0.2
        a_operator = rng.random() < 0.2
        seq = 0
        if fb_killed:
            seq += 1
            records.append(ks(f"model:{fallback}", feed_seq=seq))
        if fb_isolated:
            seq += 1
            records.append(ks(f"iso:{fallback}", body=iso_body(fallback="a", expires_at=9e9),
                              feed_seq=seq))
        if a_operator:
            seq += 1
            records.append(ks("iso:a", body=iso_body(fallback="b", expires_at=9e9), feed_seq=seq))
        load(rig.snap, records)
        before = (rig.snap.view().models, rig.snap.view().orgs, rig.snap.view().engaged,
                  rig.snap.view().count)
        rig.trip()
        result = rig.isolator.evaluate("a")
        after = rig.snap.view()
        now = (after.models, after.orgs, after.engaged, after.count)
        assert now == before, f"{tag} kill changed"
        active = rig.state.active_isolation("a")
        if a_operator:
            assert active is not None and active.origin is IsolationOrigin.OPERATOR, tag
            assert result == "none" and not rig.events, f"{tag} overrode an operator isolation"
            continue
        if _oracle_eligible(rig, fallback, constraints, fb_killed, fb_isolated):
            isolated += 1
            assert result == "isolated", f"{tag} eligible fallback not used"
            assert active is not None and active.fallback == fallback, tag
            assert [e.kind for e in rig.events] == [K.ISOLATE], tag
        else:
            skipped += 1
            assert result.startswith("skipped:"), f"{tag} {result}"
            assert active is None, f"{tag} isolated without an eligible fallback"
            assert [e.kind for e in rig.events] == [K.AUTO_ISOLATE_SKIPPED], tag
            assert rig.events[0].reason in ("no_fallback", "fallback_ineligible"), tag
    assert isolated > 100 and skipped > 100, "the generator must exercise both branches"


def test_a_model_kill_blocks_a_local_isolation_but_not_the_skip_accounting() -> None:
    rig = Rig()
    load(rig.snap, [ks("model:a", feed_seq=1)])
    rig.trip()
    assert rig.isolator.evaluate("a") == "suppressed"
    assert rig.state.active_isolation("a") is None and rig.events == []
    assert rig.state.local_suppressed_total == 1


def test_a_stale_snapshot_never_creates_an_isolation() -> None:
    rig = Rig()
    rig.mono.now += 60.0  # the snapshot aged out: kill facts about the fallback are unknown
    rig.trip()
    assert rig.isolator.evaluate("a") == "skipped:fallback_ineligible"
    assert rig.state.active_isolation("a") is None
