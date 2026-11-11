"""GW36 S2 — the control-to-state projector, end to end over the real writer and readers.

Every scenario runs the real `StateWriter` over the transactional twin and checks the result
with the gateway's own `FeedReader` and appliers (`Gateway`), so "projected" means "a worker
accepted it", not "a row exists".
"""

from __future__ import annotations

import asyncio
import json
import random

import pytest

from gateway_v2.admit.identity import principal_of
from gateway_v2.domain.plan import ExecutionPlan, PlanUnknownTenant, StreamingMode
from gateway_v2.domain.state import StateKind, StateOp
from gateway_v2.domain.state_knobs import ProjectorKnobs
from gateway_v2.plan.document import PlanDocument, encode_plan_body
from gateway_v2.runtime.state_sig import decode_record
from state_control.db import MemoryControlDB
from state_control.projector import Projector, desired_state, validate_plan
from state_control.publisher import MemoryStore
from state_control.rehydrate import Rehydrator
from state_control.source import MemorySource
from state_control.writer import StateWriter
from tests.plan.test_lgw05c_delta import _draft
from tests.state_control.test_lgw05c_writer import SECRET, Gateway

KNOBS = ProjectorKnobs()


def _hash(n: int) -> str:
    return f"{n:064x}"


def _doc(slug: str) -> str:
    return json.dumps(encode_plan_body(PlanDocument(slug, StreamingMode.INCREMENTAL, (_draft(),))))


class Clock:
    def __init__(self) -> None:
        self.now = 10_000.0

    def __call__(self) -> float:
        return self.now


class Lab:
    def __init__(self, *, chunk: int = 500, knobs: ProjectorKnobs = KNOBS) -> None:
        self.clock = Clock()
        self.db = MemoryControlDB(clock=self.clock)
        self.store = MemoryStore()
        self.source = MemorySource(clock=self.clock)
        self.writer = StateWriter(self.db, self.store, SECRET, actor="amf.projector:a")
        self.projector = Projector(
            self.db, self.source, self.writer, holder="a", knobs=knobs, chunk=chunk,
        )

    def org(self, slug: str, *, keys: int = 1, plan: bool = True) -> list[str]:
        self.source.org(slug)
        if plan:
            self.source.plan(slug, _doc(slug))
        hashes = []
        for _ in range(keys):
            key_hash = _hash(len(self.source.read().keys) + 1)
            self.source.key(key_hash, key_id=f"id-{key_hash[-4:]}", org_slug=slug)
            hashes.append(key_hash)
        return hashes

    def live(self, kind: StateKind) -> dict[str, object]:
        return {
            record.key: json.loads(record.body)
            for record in self.db.snapshot(kind)[1]
            if not record.deleted
        }

    def principal(self, key_hash: str) -> object:
        (raw,) = asyncio.run(self.store.records(StateKind.KEY, (key_hash,)))
        assert raw is not None
        return principal_of(decode_record(SECRET, StateKind.KEY, raw))


# --- the happy path, checked by the gateway's own readers -----------------------------------


def test_a_round_projects_keys_plans_and_switches_that_a_worker_accepts() -> None:
    lab = Lab()
    (key,) = lab.org("acme")
    lab.source.switch("acme")
    summary = lab.projector.round_once()

    assert summary.leader and summary.heartbeat and summary.complete
    gateway = Gateway(lab.store)
    for kind in (StateKind.KEY, StateKind.PLAN, StateKind.KS):
        gateway.poll(kind)
    principal = lab.principal(key)
    assert principal is not None and principal.org_id == "acme"  # type: ignore[attr-defined]
    assert isinstance(gateway.plans.read("acme"), ExecutionPlan)
    assert gateway.switches.org_killed("acme")
    assert lab.db.source_lag_s() == pytest.approx(0.0)


def test_an_unchanged_estate_writes_nothing_and_still_heartbeats() -> None:
    lab = Lab()
    lab.org("acme", keys=3)
    lab.projector.round_once()
    before = len(lab.db.log())
    lab.clock.now += 1.0
    again = lab.projector.round_once()
    assert again.writes == 0 and again.heartbeat
    assert len(lab.db.log()) == before, "no amf_state_log row for an unchanged estate"
    assert lab.db.source_lag_s() == pytest.approx(0.0)


def test_the_actor_names_the_projector() -> None:
    lab = Lab()
    lab.org("acme")
    lab.projector.round_once()
    assert {entry.actor for entry in lab.db.log()} == {"amf.projector:a"}


# --- revocation ------------------------------------------------------------------------------


@pytest.mark.parametrize("how", ["deactivate", "expire", "delete", "org_off", "org_gone"])
def test_every_way_a_key_stops_being_live_writes_an_explicit_revoke(how: str) -> None:
    lab = Lab()
    (key,) = lab.org("acme")
    lab.projector.round_once()
    if how == "deactivate":
        lab.source.key(key, key_id="id", org_slug="acme", active=False)
    elif how == "expire":
        lab.source.key(key, key_id="id", org_slug="acme", expires_at=lab.clock.now + 5)
        lab.projector.round_once()
        lab.clock.now += 10
    elif how == "delete":
        lab.source.drop_key(key)
    elif how == "org_off":
        lab.source.org("acme", active=False)
    else:
        lab.source.drop_org("acme")

    summary = lab.projector.round_once()

    record = {r.key: r for r in lab.db.snapshot(StateKind.KEY)[1]}[key]
    assert record.deleted and record.op is StateOp.REVOKE, how
    assert summary.revokes == 1
    gateway = Gateway(lab.store)
    gateway.poll(StateKind.KEY)
    assert lab.principal(key) is None, f"{how}: a revoked key must not resolve"
    if how in ("org_off", "org_gone"):
        gateway.poll(StateKind.PLAN)
        assert isinstance(gateway.plans.read("acme"), PlanUnknownTenant)


def test_a_revoked_key_comes_back_when_reactivated() -> None:
    lab = Lab()
    (key,) = lab.org("acme")
    lab.projector.round_once()
    lab.source.key(key, key_id="id", org_slug="acme", active=False)
    lab.projector.round_once()
    lab.source.key(key, key_id="id", org_slug="acme", active=True)
    lab.projector.round_once()
    assert lab.principal(key) is not None


def test_a_slug_rename_moves_keys_plan_and_switch() -> None:
    lab = Lab()
    (key,) = lab.org("acme")
    lab.source.switch("acme")
    lab.projector.round_once()
    lab.source.rename_org("acme", "acme-2")
    lab.source.plan("acme-2", _doc("acme-2"))  # control republishes on the org save
    lab.projector.round_once()

    assert lab.live(StateKind.KEY)[key] == {
        "key_id": f"id-{key[-4:]}", "org_id": "acme-2", "rate_per_s": 0.0, "burst": 0.0,
    }
    assert set(lab.live(StateKind.PLAN)) == {"acme-2"}
    assert lab.db.snapshot(StateKind.KS)[2] == ("org:acme-2",)


# --- the round's order is the safety argument -----------------------------------------------


def test_the_round_order_is_switch_on_revoke_plan_heartbeat_switch_off_add() -> None:
    lab = Lab()
    (old_key,) = lab.org("old")
    lab.source.switch("old")
    lab.projector.round_once()
    start = len(lab.db.log())
    lab.source.org("old", active=False)  # revoke its key, offboard its plan, its switch off
    lab.org("new")  # a new plan and a new key
    lab.source.switch("new")  # a switch on

    lab.projector.round_once()

    order = [(entry.record.kind, entry.record.key) for entry in lab.db.log()[start:]]
    kinds = [kind for kind, _ in order]
    ks_on = order.index((StateKind.KS, "org:new"))
    revoke = order.index((StateKind.KEY, old_key))
    plan = kinds.index(StateKind.PLAN)
    ks_off = order.index((StateKind.KS, "org:old"))
    add = max(i for i, (kind, key) in enumerate(order) if kind is StateKind.KEY and key != old_key)
    assert ks_on < revoke < plan < ks_off < add, order


def test_bulk_onboarding_is_spread_over_rounds_after_the_heartbeat() -> None:
    lab = Lab(chunk=2, knobs=ProjectorKnobs(add_chunks_per_round=1))
    lab.org("acme", keys=5)
    rounds = []
    for _ in range(4):
        summary = lab.projector.round_once()
        rounds.append((summary.adds, summary.adds_deferred, summary.heartbeat))
    assert rounds == [(2, 3, True), (2, 1, True), (1, 0, True), (0, 0, True)]
    assert len(lab.live(StateKind.KEY)) == 5


# --- data that must not stop the round ------------------------------------------------------


def test_an_invalid_document_keeps_the_previous_plan_and_does_not_stop_the_round() -> None:
    lab = Lab()
    lab.org("acme")
    lab.org("other")
    lab.projector.round_once()
    previous = lab.live(StateKind.PLAN)["acme"]
    lab.source.plan("acme", '{"org_id": "acme", "rules": "not-a-list"}')
    lab.source.org("third")
    lab.source.plan("third", _doc("third"))

    summary = lab.projector.round_once()

    assert summary.invalid_plans == 1 and summary.heartbeat
    assert lab.live(StateKind.PLAN)["acme"] == previous
    assert "third" in lab.live(StateKind.PLAN), "one bad tenant does not block another"


@pytest.mark.parametrize(
    "raw",
    [
        "[]",
        '{"org_id": "other", "streaming_mode": "incremental", "rules": []}',
        '{"org_id": "acme", "streaming_mode": "incremental", "rules": [], "x": NaN}',
        '{"org_id": "acme", "streaming_mode": "incremental", "rules": [], "x": "\\ud800"}',
        "not json",
    ],
)
def test_validate_plan_refuses_what_would_poison_the_round(raw: str) -> None:
    body, _refused, why = validate_plan("acme", raw)
    assert body is None and why


def test_a_nul_in_a_document_is_escaped_and_therefore_harmless() -> None:
    """Review 2's M2 assumed a NUL could reach Postgres text; JSON escapes it."""
    raw = '{"org_id": "acme", "streaming_mode": "incremental", "rules": [], "x": "\\u0000"}'
    body, _refused, why = validate_plan("acme", raw)
    assert body is not None and why == ""
    from gateway_v2.runtime.state_sig import canonical_body

    assert b"\x00" not in canonical_body(body)


def test_a_refused_customer_regex_is_counted_not_rejected() -> None:
    body = encode_plan_body(PlanDocument("acme", StreamingMode.INCREMENTAL, (_draft(),)))
    rules = body["rules"]
    assert isinstance(rules, list)
    rules.append(
        {
            **rules[0],
            "rule_id": "bad-regex",
            "condition": {
                "kind": "regex", "field": "", "direction": rules[0]["scope"], "keywords": [],
                "pattern": "(a+)+$", "detector_class": "", "pipeline_stage": "",
            },
        },
    )
    projected, refused, why = validate_plan("acme", json.dumps(body))
    assert projected is not None and refused == 1 and why == ""


def test_names_the_reader_would_refuse_are_skipped_and_counted() -> None:
    lab = Lab()
    lab.source.org("")
    lab.source.org("has space")
    lab.source.key("not-a-hash", key_id="x", org_slug="acme")
    lab.org("acme")
    lab.source.key(_hash(99), key_id="orgless", org_slug=None)
    summary = lab.projector.round_once()
    assert summary.invalid_names == 3
    assert summary.keys_without_org == 1
    assert summary.heartbeat
    assert lab.db.snapshot(StateKind.KS)[1] == (), "an `org:` scope would refuse the whole kind"
    assert set(lab.live(StateKind.PLAN)) == {"acme"}


def test_unsupported_switches_are_counted_and_never_widened() -> None:
    lab = Lab()
    lab.org("acme")
    lab.source.switch("acme", model_name="gpt-4o")
    lab.source.switch("acme", api_key_prefix="abcd1234")
    lab.source.switch(None)
    lab.org("route")
    lab.source.switch("route", action="reroute")
    summary = lab.projector.round_once()
    assert summary.unsupported_switches == 4
    assert lab.db.snapshot(StateKind.KS)[2] == (), "no model/global scope may be invented"


def test_an_active_org_without_a_document_gets_no_plan_and_is_counted() -> None:
    lab = Lab()
    lab.org("acme", plan=False)
    summary = lab.projector.round_once()
    assert summary.orgs_without_plan == 1
    assert "acme" not in lab.live(StateKind.PLAN)


# --- the lease and the heartbeat ------------------------------------------------------------


def test_a_standby_writes_nothing_and_takes_over_after_the_ttl() -> None:
    lab = Lab()
    lab.org("acme")
    standby = Projector(
        lab.db, lab.source, StateWriter(lab.db, lab.store, SECRET), holder="b", knobs=KNOBS,
    )
    assert lab.projector.round_once().leader
    assert not standby.round_once().leader
    lab.clock.now += KNOBS.lease_ms / 1000 + 0.1
    lab.source.org("next")
    lab.source.plan("next", _doc("next"))
    took = standby.round_once()
    assert took.leader and took.heartbeat and "next" in lab.live(StateKind.PLAN)
    assert not lab.projector.round_once().leader


def test_a_clean_stop_hands_the_lease_over_without_waiting_the_ttl() -> None:
    lab = Lab()
    lab.org("acme")
    standby = Projector(
        lab.db, lab.source, StateWriter(lab.db, lab.store, SECRET), holder="b", knobs=KNOBS,
    )
    assert lab.projector.round_once().leader
    lab.projector.release()
    assert standby.round_once().leader, "no TTL wait after a clean stop"
    standby.release()
    lab.projector.release()  # releasing a lease it no longer holds changes nothing
    assert lab.db.lease_holder() is None


def test_losing_the_lease_mid_round_stops_writing_and_skips_the_heartbeat() -> None:
    lab = Lab()
    lab.org("acme")
    lab.projector.round_once()
    heartbeat = lab.db.source_lag_s()
    real_read = lab.source.read

    def read_then_lose():  # type: ignore[no-untyped-def]
        view = real_read()
        lab.clock.now += KNOBS.lease_ms / 1000 + 0.1
        assert lab.db.claim_lease("b", KNOBS.lease_ms)
        return view

    lab.source.read = read_then_lose  # type: ignore[method-assign]
    lab.source.org("acme", active=False)
    summary = lab.projector.round_once()
    assert summary.lease_lost and not summary.heartbeat
    assert "acme" in lab.live(StateKind.PLAN), "nothing written after the takeover"
    assert lab.db.source_lag_s() is not None and lab.db.source_lag_s() > (heartbeat or 0)


def _rehydrator(lab: Lab, lag_ms: int) -> Rehydrator:
    return Rehydrator(
        lab.db, lab.store, lab.writer, SECRET, stale_grace_s=0.0, clock=lab.clock,
        source_lag=lab.db.source_lag_s, source_lag_ms=lag_ms,
    )


def test_the_stamp_needs_a_fresh_projector_heartbeat() -> None:
    lab = Lab()
    lab.org("acme")
    rehydrator = _rehydrator(lab, KNOBS.lag_ms)
    never = rehydrator.round_once()
    assert never.stamped is None and never.source_lagging, "no heartbeat yet: withheld"

    lab.projector.round_once()
    fresh = rehydrator.round_once()
    assert fresh.stamped is not None and not fresh.source_lagging

    lab.clock.now += KNOBS.lag_ms / 1000 + 1
    stale = rehydrator.round_once()
    assert stale.stamped is None and stale.source_lagging
    assert stale.source_lag_s is not None and stale.source_lag_s > KNOBS.lag_ms / 1000

    lab.projector.round_once()
    assert rehydrator.round_once().stamped is not None, "stamping resumes with the projector"


def test_an_unreadable_heartbeat_withholds_and_off_means_off() -> None:
    lab = Lab()
    lab.projector.round_once()

    def broken() -> float | None:
        raise RuntimeError("table missing")

    withheld = Rehydrator(
        lab.db, lab.store, lab.writer, SECRET, stale_grace_s=0.0, clock=lab.clock,
        source_lag=broken, source_lag_ms=KNOBS.lag_ms,
    ).round_once()
    assert withheld.stamped is None
    assert _rehydrator(lab, 0).round_once().stamped is not None


def test_a_failing_source_read_is_a_failed_round_without_heartbeat() -> None:
    lab = Lab()
    lab.org("acme")
    lab.projector.round_once()
    lab.clock.now += 1.0
    lab.source.fail_next = True
    with pytest.raises(ConnectionError):
        lab.projector.round_once()
    assert lab.db.source_lag_s() == pytest.approx(1.0)


# --- the property: after a round, state equals what control implies ------------------------


def _mutate(rng: random.Random, source: MemorySource, clock: Clock, orgs: list[str]) -> None:
    choice = rng.randrange(9)
    slug = rng.choice(orgs)
    key_hash = _hash(rng.randrange(1, 12))
    if choice == 0:
        source.org(slug, active=rng.random() < 0.7)
    elif choice == 1:
        source.key(
            key_hash, key_id=key_hash[-4:], org_slug=rng.choice([*orgs, None]),
            active=rng.random() < 0.8,
            expires_at=None if rng.random() < 0.7 else clock.now + rng.uniform(-5, 5),
        )
    elif choice == 2:
        source.drop_key(key_hash)
    elif choice == 3:
        source.plan(slug, _doc(slug) if rng.random() < 0.85 else '{"org_id": 1}')
    elif choice == 4:
        source.drop_plan(slug)
    elif choice == 5:
        source.switch(slug, engaged=rng.random() < 0.5)
    elif choice == 6:
        source.switch(slug, model_name="m", engaged=rng.random() < 0.5)
    elif choice == 7:
        source.drop_org(slug)
    else:
        clock.now += rng.uniform(0, 4)


def test_after_every_round_state_equals_what_control_implies() -> None:
    seed = 0x36_5202
    rng = random.Random(seed)
    for iteration in range(10_000):
        lab = Lab()
        orgs = ["o1", "o2", "o3"]
        kept: dict[str, object] = {}
        for _step in range(rng.randint(1, 3)):
            for _ in range(rng.randint(1, 4)):
                _mutate(rng, lab.source, lab.clock, orgs)
            view = lab.source.read()
            desired = desired_state(view)
            summary = lab.projector.round_once()
            where = f"seed={seed} iteration={iteration}"
            assert summary.complete and summary.adds_deferred == 0, where
            live_keys = lab.live(StateKind.KEY)
            assert live_keys == dict(desired.keys), where
            plans = lab.live(StateKind.PLAN)
            for slug in desired.active_orgs:
                if slug in desired.plans:
                    assert plans.get(slug) == desired.plans[slug], where
                    kept[slug] = desired.plans[slug]
                elif slug in plans:
                    assert plans[slug] == kept.get(slug), f"{where}: an invalid doc keeps"
            assert set(plans) <= set(desired.active_orgs), where
            assert set(lab.db.snapshot(StateKind.KS)[2]) == set(desired.switches_on), where
            for slug in [s for s in kept if s not in desired.active_orgs]:
                del kept[slug]
            assert lab.projector.round_once().writes == 0, f"{where}: a second round is a no-op"
