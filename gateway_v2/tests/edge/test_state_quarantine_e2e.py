"""End to end: a quarantined key or tenant, through the gateway's real state runtime (PR #136 B1).

The control plane is real (`StateWriter` signs into fakeredis, `Rehydrator` stamps) and so is the
gateway (`StateRuntime.cycle`, its gates and metrics, requests through the assembled app), as in
`test_gw36_s3_state_identity`.

The B1 attack, found by both reviewers: the control plane revokes a key; something that can write
the store puts the key's OLDER, genuinely signed ACTIVE record back under the revocation's index
entry. The old record verifies on its own. Before the fix the running worker logged "the key is
refused" and admitted it on the next request -- for good, even after the store was repaired --
and a newly started worker admitted it too (the request path never paired a record with its index
entry). Now: 503 on every worker, never 200; the key's in-flight streams are cut; and once the
store is repaired the revocation is enforced (401).
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import time

import fakeredis
import pytest

from gateway_v2.domain.plan import ExecutionPlan, PlanUnavailable
from gateway_v2.domain.state import StateKind
from gateway_v2.domain.state_knobs import StateKnobs
from gateway_v2.edge import serve
from gateway_v2.edge.incident_wiring import build_incident
from gateway_v2.edge.state_runtime import StateRuntime, StateRuntimeConfig
from gateway_v2.plan import delta as delta_module
from gateway_v2.plan.store import PLAN_QUARANTINED, QUARANTINED, REJECTED
from tests.edge.test_dev_profile_boot import _contract
from tests.edge.test_gw36_s3_state_identity import (
    NAMESPACE,
    SECRET,
    Lab,
    _call,
    _console_key,
    _error,
    _sha,
)
from tests.edge.test_state_applier_isolation_e2e import _body, _deep_regex_body, _plan_key

KEY_GAUGE = 'amf_state_held_back_quarantined{kind="key"}'
PLAN_GAUGE = 'amf_state_held_back_quarantined{kind="plan"}'


async def _models(app: object, raw: str) -> tuple[int, str | None]:
    status, _h, body = await _call(app, "GET", "/v1/models", auth=f"Bearer {raw}")
    return status, None if status == 200 else str(_error(body)["code"])


class KeyLab(Lab):
    """A lab with two keys admitted and cached, and the victim's raw KEY records at hand."""

    def __init__(self) -> None:
        super().__init__()
        self.victim, self.other = _console_key(), _console_key()
        self.hash_name = self.runtime._keys.record_hash(StateKind.KEY)  # noqa: SLF001
        self.index_name = self.runtime._keys.index(StateKind.KEY)  # noqa: SLF001
        self.sync = self.publisher._client  # noqa: SLF001 - the store the writer published into
        self.invalidated: list[str] = []
        self.runtime.set_gcra_invalidate(lambda hashes: self.invalidated.extend(hashes))
        self.wiring = build_incident(
            self.runtime, _contract(), env={}, clock=time.monotonic, wall=time.time,
            rng=random.Random(1),
        )
        self.runtime.set_revocation_ledger(self.wiring.ledger)

    async def admit_both(self) -> int:
        """Both keys added and cached as admitted. Returns the victim's admitted position."""
        self.plan()
        self.writer.key_add(_sha(self.victim), "acme", key_id="victim", rate_per_s=0.0, burst=0.0)
        self.key(self.other)
        await self.settle()
        for raw in (self.victim, self.other):
            assert (await _models(self.app, raw))[0] == 200
        principal = self.runtime.identity_cache.cached(_sha(self.victim))
        assert principal is not None
        return principal.feed_seq

    def raw(self) -> bytes:
        value = self.sync.hget(self.hash_name, _sha(self.victim))
        assert isinstance(value, bytes)
        return value

    def put(self, raw: bytes) -> None:
        self.sync.hset(self.hash_name, _sha(self.victim), raw)

    def gauge(self, name: str) -> float:
        return self.runtime.metrics.snapshot().series()[name]


def _cold_runtime(server: fakeredis.FakeServer) -> tuple[StateRuntime, object]:
    """A worker that starts AFTER the store was tampered with: KEY cold-starts by fast-forward."""
    runtime = StateRuntime(
        StateRuntimeConfig("redis://unused", SECRET, NAMESPACE, StateKnobs()),
        client=fakeredis.FakeAsyncRedis(server=server),
    )
    deps, _logs = serve.build_boot_dependencies(contract=_contract(), env={})
    deps.update(
        identity=runtime.identity_resolver(),
        snapshot_source=runtime.snapshot_source,
        readiness=runtime.ready,
    )
    return runtime, serve._assemble_app(deps)


def test_a_replayed_pre_revocation_key_record_is_refused_by_a_running_worker(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Reviewer A's key_replay and reviewer B's p8: the replay lands before the worker reads."""

    async def go() -> None:
        lab = KeyLab()
        admitted = await lab.admit_both()
        older = lab.raw()
        revoke = lab.writer.key_revoke(_sha(lab.victim))
        revoked = lab.raw()
        lab.put(older)  # the older ACTIVE record, under the revocation's index entry
        lab.rehydrator.round_once()

        with caplog.at_level(logging.DEBUG, logger="amf"):
            for _ in range(4):
                reports = await lab.runtime.cycle()
                assert all(report.ok for report in reports), [(r.kind, r.error) for r in reports]
                assert lab.runtime.ready() == (True, None)
                assert await _models(lab.app, lab.victim) == (503, "shared_state_unavailable")
                assert (await _models(lab.app, lab.other))[0] == 200
                lab.rehydrator.round_once()

        victim = _sha(lab.victim)
        assert lab.runtime.identity_cache.quarantined_at(victim) == revoke.feed_seq
        assert lab.wiring.ledger.revoked_since(victim, admitted), "in-flight streams are cut"
        assert victim in lab.invalidated, "the GCRA entry goes with the identity entry (MA7)"
        assert lab.gauge(KEY_GAUGE) == 1
        series = lab.runtime.metrics.snapshot().series()
        assert series['amf_state_records_quarantined_total{kind="key"}'] == 1
        assert f"key record quarantined: key={victim!r} feed_seq={revoke.feed_seq}" in caplog.text

        lab.put(revoked)  # the re-hydrator's repair: the revocation's bytes, at the same position
        assert await _models(lab.app, lab.victim) == (401, "invalid_api_key"), (
            "the revocation is enforced once the record verifies and pairs again"
        )
        await lab.runtime.cycle()
        assert lab.runtime.identity_cache.quarantined_at(victim) is None
        assert lab.gauge(KEY_GAUGE) == 0

    asyncio.run(go())


def test_a_replayed_pre_revocation_key_record_is_refused_by_a_newly_started_worker() -> None:
    """Reviewer A's key_cold: pre-existing on develop; the request path now pairs the record."""

    async def go() -> None:
        lab = KeyLab()
        await lab.admit_both()
        older = lab.raw()
        lab.writer.key_revoke(_sha(lab.victim))
        revoked = lab.raw()
        lab.put(older)

        runtime, app = _cold_runtime(lab.server)
        for _ in range(2):
            lab.rehydrator.round_once()  # a stamp from a round that started after the worker
            reports = await runtime.cycle()
            assert all(report.ok for report in reports)
            assert await _models(app, lab.victim) == (503, "shared_state_unavailable")
            assert (await _models(app, lab.other))[0] == 200

        lab.put(revoked)
        assert await _models(app, lab.victim) == (401, "invalid_api_key")

    asyncio.run(go())


def test_a_key_replayed_after_its_revocation_was_applied_is_refused() -> None:
    """Reviewer B's p7: the worker applied the revocation; the replay comes later, in no delta."""

    async def go() -> None:
        lab = KeyLab()
        await lab.admit_both()
        older = lab.raw()
        lab.writer.key_revoke(_sha(lab.victim))
        await lab.settle()
        assert await _models(lab.app, lab.victim) == (401, "invalid_api_key")
        lab.runtime.identity_cache._negative.clear()  # noqa: SLF001 - the negative entry expires

        lab.put(older)

        assert await _models(lab.app, lab.victim) == (503, "shared_state_unavailable")
        assert lab.runtime.identity_cache.quarantined_at(_sha(lab.victim)) is None, (
            "no fault was seen: the pairing alone refuses it"
        )

    asyncio.run(go())


def test_moving_the_index_entry_too_does_not_lift_a_key_quarantine() -> None:
    """After the fault, the index entry is moved to the older record's position, so the pair
    agrees again. The quarantine still refuses anything below the faulted position."""

    async def go() -> None:
        lab = KeyLab()
        await lab.admit_both()
        older = lab.raw()
        lab.writer.key_revoke(_sha(lab.victim))
        lab.put(older)
        lab.rehydrator.round_once()
        await lab.runtime.cycle()
        older_at = lab.runtime.identity_cache.quarantined_at(_sha(lab.victim))
        assert older_at is not None

        lab.sync.zadd(lab.index_name, {_sha(lab.victim): json.loads(older)["feed_seq"]})

        assert await _models(lab.app, lab.victim) == (503, "shared_state_unavailable")
        assert await lab.runtime.recheck_quarantined() == (0, 0)
        assert lab.runtime.identity_cache.quarantined_at(_sha(lab.victim)) == older_at

    asyncio.run(go())


def test_a_key_whose_record_vanished_under_its_index_entry_is_a_503_not_a_401() -> None:
    """Reviewer A's N3: "cannot verify" is 503, never a 401 and a negative-cache entry.

    Twice: the record vanishes inside a delta the worker reads (a fault, so a quarantine), and
    after the worker applied it (no fault; the request path's own pairing refuses it).
    """

    async def go() -> None:
        lab = KeyLab()
        await lab.admit_both()
        lab.writer.key_add(_sha(lab.victim), "acme", key_id="v2", rate_per_s=0.0, burst=0.0)
        lab.sync.hdel(lab.hash_name, _sha(lab.victim))
        lab.rehydrator.round_once()
        reports = await lab.runtime.cycle()
        assert all(report.ok for report in reports)

        for _ in range(2):
            assert await _models(lab.app, lab.victim) == (503, "shared_state_unavailable")
        assert lab.runtime.identity_cache.stats().negative == 0

        lab.runtime.identity_cache.evict([_sha(lab.other)], 0)  # not cached: the next ask reads
        lab.sync.hdel(lab.hash_name, _sha(lab.other))  # applied long ago, no delta announces it
        assert lab.runtime.identity_cache.quarantined_at(_sha(lab.other)) is None
        assert await _models(lab.app, lab.other) == (503, "shared_state_unavailable")
        assert lab.runtime.identity_cache.stats().negative == 0

    asyncio.run(go())


def test_a_newer_verified_key_record_lifts_the_quarantine_on_the_delta_path() -> None:
    """Before any request reads the key: the delta that carries the verified record lifts it."""

    async def go() -> None:
        lab = KeyLab()
        await lab.admit_both()
        older = lab.raw()
        lab.writer.key_revoke(_sha(lab.victim))
        lab.put(older)
        lab.rehydrator.round_once()
        await lab.runtime.cycle()
        assert lab.runtime.identity_cache.quarantined_at(_sha(lab.victim)) is not None

        lab.writer.key_add(_sha(lab.victim), "acme", key_id="again", rate_per_s=0.0, burst=0.0)
        lab.rehydrator.round_once()
        await lab.runtime.cycle()

        assert lab.runtime.identity_cache.quarantined_at(_sha(lab.victim)) is None
        await lab.runtime.cycle()
        assert lab.gauge(KEY_GAUGE) == 0
        assert (await _models(lab.app, lab.victim))[0] == 200, "re-issued by the control plane"

    asyncio.run(go())


@pytest.mark.parametrize("kind", [StateKind.KS, StateKind.BUDGET])
def test_a_tampered_kill_switch_or_budget_record_refuses_its_whole_kind_and_says_so(
    kind: StateKind, caplog: pytest.LogCaptureFixture,
) -> None:
    """Reviewer B's S4 / A's N1: both kinds keep their whole-round refusal BY DESIGN (an
    unauthenticated index key cannot name a switch scope; budget rounds are all-or-nothing), so
    one bad record still refuses the kind -- and, after FRESH_MS, readiness, on every worker.
    What changed is that it is no longer silent: one line names the record, once."""

    async def go() -> None:
        lab = Lab()
        lab.plan("acme")
        lab.plan("other")
        await lab.settle()
        if kind is StateKind.KS:
            lab.writer.put(StateKind.KS, "org:other", {"on": True}, engaged=True)
            key = "org:other"
        else:
            lab.writer.budget_set("other", 500)
            key = "other"
        name = lab.runtime._keys.record_hash(kind)  # noqa: SLF001
        sync = lab.publisher._client  # noqa: SLF001
        raw = sync.hget(name, key)
        assert isinstance(raw, bytes)
        sync.hset(name, key, raw.replace(b'"sig":"', b'"sig":"0', 1))
        lab.rehydrator.round_once()

        with caplog.at_level(logging.DEBUG, logger="amf"):
            for _ in range(3):
                reports = {report.kind: report for report in await lab.runtime.cycle()}
                assert not reports[kind].ok, "the whole kind is refused, never applied in part"
                assert all(r.ok for k, r in reports.items() if k is not kind)
                lab.rehydrator.round_once()

        assert reports[kind].quarantined == 0
        messages = [record.getMessage() for record in caplog.records]
        refused = [message for message in messages if "state round refused" in message]
        assert refused == [
            f"state round refused: kind={kind.value} error=StoreDataUnavailable "
            f"detail={kind.value} record {key!r} fails its signature",
        ]

    asyncio.run(go())


def test_production_wires_the_whole_ledger() -> None:
    """A KEY record that fails verification cuts streams in production, not only a revoke."""
    from gateway_v2.runtime.state_feed import FeedRound, RecordFault
    from tests.edge.test_lgw29_production_wiring import PROD, _boot, _wiring

    deps, _logs = _boot(PROD)
    runtime = deps["state_runtime"]
    assert isinstance(runtime, StateRuntime)
    manifest = type("M", (), {"feed_seq": 9})()
    round_ = FeedRound(
        StateKind.KEY, manifest, (), runtime.sync.cursor(StateKind.KEY), truncated=False,  # type: ignore[arg-type]
        faults=(RecordFault(key="h" * 64, feed_seq=9, error="StoreDataUnavailable"),),
    )

    runtime.sync._appliers[StateKind.KEY](round_)  # noqa: SLF001 - the applier the cycle runs

    assert _wiring(deps).ledger.revoked_since("h" * 64, 8)


# --- PLAN: the bounded re-check, and the multi-round counter --------------------------------------


def _tamper(lab: Lab, org: str) -> bytes:
    sync = lab.publisher._client  # noqa: SLF001
    raw = sync.get(_plan_key(lab, org))
    assert isinstance(raw, bytes)
    sync.set(_plan_key(lab, org), raw.replace(b'"sig":"', b'"sig":"0', 1))
    return raw


def test_a_same_position_repair_restores_a_quarantined_tenant_without_a_restart(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Reviewer A's S4 / P7: the cursor passed the entry, so only a re-check can notice."""

    async def go() -> None:
        lab = Lab()
        lab.plan("victim")
        await lab.settle()
        lab.writer.plan_set("victim", _body("victim"))
        genuine = _tamper(lab, "victim")
        lab.rehydrator.round_once()
        await lab.runtime.cycle()
        assert isinstance(lab.runtime.snapshot.lookup("victim"), PlanUnavailable)

        with caplog.at_level(logging.DEBUG, logger="amf"):
            assert await lab.runtime.recheck_quarantined() == (0, 0), "still tampered"
        assert "quarantine lifted" not in caplog.text

        lab.publisher._client.set(_plan_key(lab, "victim"), genuine)  # noqa: SLF001
        with caplog.at_level(logging.DEBUG, logger="amf"):
            assert await lab.runtime.recheck_quarantined() == (0, 1)
        state = lab.runtime.snapshot.lookup("victim")
        assert isinstance(state, ExecutionPlan) and state.feed_seq == lab.runtime.sync.cursor(
            StateKind.PLAN,
        ).feed_seq
        assert lab.runtime.snapshot.held_back("victim") is None
        assert "plan record quarantine lifted: org='victim'" in caplog.text
        lab.rehydrator.round_once()
        await lab.runtime.cycle()
        assert lab.runtime.metrics.snapshot().series()[PLAN_GAUGE] == 0

    asyncio.run(go())


def test_a_repaired_record_that_does_not_compile_is_a_rejection_after_the_re_check() -> None:
    """The quarantined record itself now verifies and is refused by the compiler: GW05 applies."""

    async def go() -> None:
        lab = Lab()
        lab.plan("victim")
        await lab.settle()
        first = lab.runtime.snapshot.lookup("victim")
        assert isinstance(first, ExecutionPlan)
        lab.writer.plan_set("victim", _deep_regex_body("victim"))
        genuine = _tamper(lab, "victim")
        lab.rehydrator.round_once()
        await lab.runtime.cycle()
        assert lab.runtime.snapshot.held_back("victim").state == QUARANTINED  # type: ignore[union-attr]

        lab.publisher._client.set(_plan_key(lab, "victim"), genuine)  # noqa: SLF001
        assert await lab.runtime.recheck_quarantined() == (0, 1)

        state = lab.runtime.snapshot.lookup("victim")
        assert isinstance(state, ExecutionPlan) and state.sequence == first.sequence
        assert lab.runtime.snapshot.held_back("victim").state == REJECTED  # type: ignore[union-attr]

    asyncio.run(go())


def test_a_quarantine_in_an_early_round_of_a_multi_round_drain_is_counted_and_gauged() -> None:
    """Reviewer A's e2e_counter: 301 records, the hostile one in the first round of 256."""

    async def go() -> None:
        lab = Lab()
        lab.plan("hostile")
        await lab.settle()
        lab.plan("hostile")
        for n in range(300):
            lab.plan(f"good-{n}")
        _tamper(lab, "hostile")
        lab.rehydrator.round_once()

        await lab.runtime.cycle()

        series = lab.runtime.metrics.snapshot().series()
        assert series['amf_state_records_quarantined_total{kind="plan"}'] == 1
        assert series['amf_state_records_applied_total{kind="plan"}'] == 1 + 300
        assert series[PLAN_GAUGE] == 1
        state = lab.runtime.snapshot.lookup("hostile")
        assert isinstance(state, PlanUnavailable) and state.reason == PLAN_QUARANTINED

        restarted, _app = _cold_runtime(lab.server)  # a restart re-detects it: the gauge says so
        lab.rehydrator.round_once()
        await restarted.cycle()
        assert restarted.metrics.snapshot().series()[PLAN_GAUGE] == 1

    asyncio.run(go())


def test_the_re_check_runs_at_most_once_per_freshness_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def go() -> None:
        lab = Lab()
        lab.plan()
        calls: list[float] = []
        now = [100.0]

        async def recheck(*, limit: int = 8) -> tuple[int, int]:
            del limit
            calls.append(now[0])
            return 0, 0

        monkeypatch.setattr(lab.runtime, "recheck_quarantined", recheck)
        lab.runtime._clock = lambda: now[0]  # noqa: SLF001 - the runtime's injectable clock
        window = lab.runtime.fresh_ms / 1000
        for step in (0.0, 0.5, window - 0.6, 0.2, 0.5):
            now[0] += step
            lab.rehydrator.round_once()
            await lab.runtime.cycle()

        assert calls == [100.0, pytest.approx(100.0 + window + 0.1)], "not at +0.5 or +window-0.1"

    asyncio.run(go())


def test_a_replayed_older_plan_record_stays_quarantined_through_the_re_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PLAN's version of B1: the tenant's older genuine record under its newer index entry."""

    async def go() -> None:
        lab = Lab()
        lab.plan("victim")
        await lab.settle()
        sync = lab.publisher._client  # noqa: SLF001
        older = sync.get(_plan_key(lab, "victim"))
        lab.writer.plan_set("victim", _body("victim"))
        sync.set(_plan_key(lab, "victim"), older)
        lab.rehydrator.round_once()
        await lab.runtime.cycle()
        assert lab.runtime.snapshot.held_back("victim").state == QUARANTINED  # type: ignore[union-attr]
        compiled: list[str] = []
        real = delta_module.compile_plan

        def counting(org_id: str, *args: object, **kwargs: object) -> object:
            compiled.append(org_id)
            return real(org_id, *args, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(delta_module, "compile_plan", counting)

        for _ in range(3):
            assert await lab.runtime.recheck_quarantined() == (0, 0)

        state = lab.runtime.snapshot.lookup("victim")
        assert isinstance(state, PlanUnavailable) and state.reason == PLAN_QUARANTINED
        assert compiled == [], "a record that does not pair is never compiled, pass after pass"

    asyncio.run(go())
