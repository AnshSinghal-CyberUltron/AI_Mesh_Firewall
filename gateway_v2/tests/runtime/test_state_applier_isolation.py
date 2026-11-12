"""One hostile record among many good ones must not stop the state cycle.

Before the fix, `round_once` caught only `StoreDataUnavailable` and the plan applier only
`CompileError`, so one tenant's record that made the compiler raise anything else -- a
`RecursionError` from a deeply nested regex, an `OverflowError` from `a{4294967296}` -- escaped
the round, aborted `drain_all` before the other kinds ran, and did the same every cycle after it:
the cursor never moved past the record. A record that failed verification refused the whole kind
every cycle. Either way every tenant on every gateway reading that record was failed closed once
the kind aged past the freshness bound.

Every test here drives the REAL reader, synchroniser and plan applier over the feed suite's
in-memory store, publishes good tenants on both sides of one hostile record, and checks the four
things the fix promises:

1. the round completes and the cursor moves past the hostile record;
2. every good tenant is served its new plan in that same round;
3. the hostile tenant is handled as designed -- a VERIFIED record that does not compile keeps the
   previous valid plan (GW05, "never fail to an empty plan"); a record that FAILS VERIFICATION
   fails that tenant closed, never last-good;
4. the log names the org, the position and the exception type, and nothing the record said;
   and the per-kind counter moves.

Each case is run with the applier inline and offloaded to a thread, because a `RecursionError`
depends on how deep the stack already is and the two paths have very different stacks.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
from collections.abc import Awaitable, Callable, Iterator

import pytest

from gateway_v2.admit.killswitch import KillSwitchSnapshot, KillSwitchState, killswitch_applier
from gateway_v2.domain.plan import ExecutionPlan, PlanUnavailable, StreamingMode
from gateway_v2.domain.state import (
    START,
    SignedRecord,
    StateKind,
    StateOp,
    StoreDataUnavailable,
    Version,
)
from gateway_v2.plan import delta as delta_module
from gateway_v2.plan.applied import report_applied
from gateway_v2.plan.delta import PlanDeltaApplier, plan_applier
from gateway_v2.plan.document import (
    MAX_BODY_DEPTH,
    PlanDocument,
    decode_plan_body,
    encode_plan_body,
    nesting_depth,
)
from gateway_v2.plan.snapshot import ReplicaSnapshot
from gateway_v2.plan.store import PLAN_QUARANTINED, QUARANTINED, REJECTED, PlanStore
from gateway_v2.runtime.state_feed import FeedReader, FeedRound
from gateway_v2.runtime.state_metrics import StateMetricsRecorder
from gateway_v2.runtime.state_sig import body_hash, encode_record, make_record, record_signature
from gateway_v2.runtime.state_task import DeltaBudget, RoundReport, StateSynchroniser
from tests.plan.test_lgw05c_delta import _draft
from tests.runtime.test_lgw05c_feed import SECRET, FakeStore

CANARY = "CANARY tenant payload 7f3a"
"""Every hostile record carries this. It must never reach a log line."""

GOOD_BEFORE = 20
GOOD_AFTER = 20
HOSTILE = "org-hostile"
LOGGER = "amf.gateway.state"


def _run[T](coro: Awaitable[T]) -> T:
    return asyncio.run(coro)  # type: ignore[arg-type]


def _body(org: str) -> dict[str, object]:
    return encode_plan_body(PlanDocument(org, StreamingMode.INCREMENTAL, (_draft(),)))


def _signed_bytes(key: str, body: bytes, *, seq: int, feed_seq: int) -> SignedRecord:
    """A GENUINELY signed plan record whose body is exactly `body`, canonical or not."""
    digest = body_hash(body)
    version = Version(1, seq)
    return SignedRecord(
        kind=StateKind.PLAN,
        key=key,
        version=version,
        feed_seq=feed_seq,
        content_hash=digest,
        deleted=False,
        op=StateOp.PUT,
        signature=record_signature(
            SECRET, StateKind.PLAN, key, version, feed_seq, digest, False, StateOp.PUT,
        ),
        body=body,
    )


class Lab:
    """A store with the hostile tenant already serving a good plan, and a fresh delta queued."""

    def __init__(self, *, offload_above: int) -> None:
        self.store = FakeStore()
        self.plans = PlanStore()
        self.snapshot = ReplicaSnapshot(self.plans, clock=lambda: 1.0)
        self.sync = StateSynchroniser(
            FeedReader(self.store, SECRET),
            {StateKind.PLAN: plan_applier(self.plans, self.snapshot, clock=lambda: 1.0)},
            budget=DeltaBudget(records=256, offload_above=offload_above),
            isolate_kinds=frozenset({StateKind.PLAN}),
        )
        self.metrics = StateMetricsRecorder()
        self.position = 0
        self.publish(HOSTILE, _body(HOSTILE))
        first = self.drain()
        assert first.ok and isinstance(self.plans.read(HOSTILE), ExecutionPlan)
        self.previous = self.served(HOSTILE).sequence

    def publish(self, org: str, body: dict[str, object]) -> SignedRecord:
        self.position += 1
        record = make_record(
            SECRET, StateKind.PLAN, org, body, Version(1, self.position), self.position,
        )
        self.publish_record(record)
        return record

    def publish_record(self, record: SignedRecord) -> None:
        self.position = max(self.position, record.feed_seq)
        known = self.store._index.get(StateKind.PLAN, {})
        count = len(known) + (0 if record.key in known else 1)
        self.store.publish(record, count=count)

    def set_raw(self, org: str, raw: bytes) -> None:
        self.store._records[StateKind.PLAN][org] = raw

    def queue_delta(self, hostile: Callable[[Lab], None]) -> list[str]:
        good = [f"org-a{n}" for n in range(GOOD_BEFORE)]
        for org in good:
            self.publish(org, _body(org))
        hostile(self)
        after = [f"org-b{n}" for n in range(GOOD_AFTER)]
        for org in after:
            self.publish(org, _body(org))
        return good + after

    def drain(self) -> RoundReport:
        report = _run(self.sync.drain(StateKind.PLAN))
        self.metrics.observe_round(report)
        return report

    def served(self, org: str) -> ExecutionPlan:
        state = self.snapshot.lookup(org)
        assert isinstance(state, ExecutionPlan), (org, state)
        return state


# --- the hostile records, one per escape route -------------------------------------------------


def _deep_body(lab: Lab) -> None:
    """Genuinely signed, nested far past the recursion limit: refused before `json` recurses."""
    raw = (
        b'{"org_id":"' + HOSTILE.encode() + b'","note":"' + CANARY.encode() + b'","x":'
        + b"[" * 100_000 + b"]" * 100_000 + b"}"
    )
    lab.position += 1
    lab.publish_record(_signed_bytes(HOSTILE, raw, seq=lab.position, feed_seq=lab.position))


def _compile_error(lab: Lab) -> None:
    """Genuinely signed, a body the compiler refuses (no rules)."""
    lab.publish(HOSTILE, {"org_id": HOSTILE, "note": CANARY})


def _well_formed(lab: Lab) -> None:
    """Genuinely signed and well formed; the test's compiler raises for this tenant only."""
    body = _body(HOSTILE)
    body["note"] = CANARY
    lab.publish(HOSTILE, body)


def _deep_regex(lab: Lab) -> None:
    """The #117 vector, through the gateway compiler: a REWRITE rule with 499 nested groups.

    On develop `check_template` raises `RecursionError`; with #117's depth bound it is a
    `TemplateError`, so a `CompileError`. Either way the tenant is rejected, not the round.
    """
    body = _body(HOSTILE)
    rules = body["rules"]
    assert isinstance(rules, list)
    rules.append({
        **rules[0], "rule_id": "deep", "action": "rewrite", "scope": "output",
        "category": "exfil", "rewrite_template": "Removed by policy {rule_id}",
        "condition": {
            "kind": "regex", "field": "", "direction": "output", "keywords": [],
            "pattern": "(" * 499 + CANARY + ")" * 499, "detector_class": "",
            "pipeline_stage": "",
        },
    })
    lab.publish(HOSTILE, body)


def _malformed_envelope(lab: Lab) -> None:
    lab.publish(HOSTILE, _body(HOSTILE))
    lab.set_raw(HOSTILE, b'{"kind":"plan","note":"' + CANARY.encode() + b'", truncated')


def _deep_envelope(lab: Lab) -> None:
    lab.publish(HOSTILE, _body(HOSTILE))
    lab.set_raw(
        HOSTILE,
        b'{"note":"' + CANARY.encode() + b'","x":' + b"[" * 100_000 + b"]" * 100_000 + b"}",
    )


def _bad_signature(lab: Lab) -> None:
    record = lab.publish(HOSTILE, {**_body(HOSTILE), "note": CANARY})
    raw = encode_record(record).replace(b'"sig":"', b'"sig":"0', 1)
    lab.set_raw(HOSTILE, raw)


def _older_record_under_newer_score(lab: Lab) -> None:
    """C36's regress, one record: an OLDER genuinely-signed record served under a NEWER score."""
    stale = lab.store._records[StateKind.PLAN][HOSTILE]
    lab.publish(HOSTILE, _body(HOSTILE))
    lab.set_raw(HOSTILE, stale)


REJECTED_CASES: dict[str, Callable[[Lab], None]] = {
    "deep_body_json": _deep_body,
    "compile_error": _compile_error,
    "deep_regex_rewrite": _deep_regex,
}
QUARANTINED_CASES: dict[str, Callable[[Lab], None]] = {
    "malformed_envelope": _malformed_envelope,
    "deep_envelope_recursion": _deep_envelope,
    "signature_mismatch": _bad_signature,
    "older_record_newer_score": _older_record_under_newer_score,
}
RAISED: dict[str, BaseException] = {
    "compiler_recursion_error": RecursionError("maximum recursion depth exceeded " + CANARY),
    "compiler_overflow_error": OverflowError("the repetition number is too large " + CANARY),
    "compiler_generic_exception": RuntimeError("unanticipated " + CANARY),
    "compiler_key_error": KeyError(CANARY),
}


@pytest.fixture(params=[1_000, 0], ids=["inline", "offloaded"])
def offload_above(request: pytest.FixtureRequest) -> int:
    value = request.param
    assert isinstance(value, int)
    return value


def _assert_cycle_completed(lab: Lab, report: RoundReport, good: list[str]) -> None:
    assert report.ok, report.error
    assert not report.truncated
    assert report.cursor.feed_seq == lab.position, "the cursor moved past the hostile record"
    for org in good:
        assert lab.served(org).feed_seq > 0, org
    assert len(good) == GOOD_BEFORE + GOOD_AFTER


def _assert_logs_clean(caplog: pytest.LogCaptureFixture) -> str:
    text = "\n".join(record.getMessage() for record in caplog.records)
    assert CANARY not in text, "a log line quoted the hostile record"
    assert "maximum recursion" not in text and "repetition number" not in text, (
        "a log line carried exception text"
    )
    return text


@pytest.mark.parametrize("case", sorted(REJECTED_CASES))
def test_a_verified_record_that_does_not_compile_keeps_the_previous_plan(
    case: str, offload_above: int, caplog: pytest.LogCaptureFixture,
) -> None:
    lab = Lab(offload_above=offload_above)
    good = lab.queue_delta(REJECTED_CASES[case])

    with caplog.at_level(logging.DEBUG, logger=LOGGER):
        report = lab.drain()

    _assert_cycle_completed(lab, report, good)
    served = lab.served(HOSTILE)
    assert served.sequence == lab.previous, "GW05: the previous valid plan keeps serving"
    held = lab.snapshot.held_back(HOSTILE)
    assert held is not None and held.state == REJECTED
    assert report.rejected == 1 and report.quarantined == 0
    series = lab.metrics.snapshot().series()
    assert series['amf_state_records_rejected_total{kind="plan"}'] == 1
    assert series['amf_state_records_quarantined_total{kind="plan"}'] == 0
    text = _assert_logs_clean(caplog)
    assert f"plan record rejected: org={HOSTILE!r} feed_seq={held.feed_seq}" in text
    assert f"error={held.error}" in text


@pytest.mark.parametrize("case", sorted(RAISED))
def test_any_exception_from_the_compiler_is_a_rejection_not_a_failed_round(
    case: str,
    offload_above: int,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    exc = RAISED[case]
    real = delta_module.compile_plan

    def compile_plan(org_id: str, *args: object, **kwargs: object) -> ExecutionPlan:
        if org_id == HOSTILE:
            raise exc
        return real(org_id, *args, **kwargs)  # type: ignore[arg-type]

    lab = Lab(offload_above=offload_above)
    monkeypatch.setattr(delta_module, "compile_plan", compile_plan)
    good = lab.queue_delta(_well_formed)

    with caplog.at_level(logging.DEBUG, logger=LOGGER):
        report = lab.drain()

    _assert_cycle_completed(lab, report, good)
    assert lab.served(HOSTILE).sequence == lab.previous
    held = lab.snapshot.held_back(HOSTILE)
    assert held is not None and (held.state, held.error) == (REJECTED, type(exc).__name__)
    assert report.rejected == 1
    text = _assert_logs_clean(caplog)
    assert f"error={type(exc).__name__}" in text


@pytest.mark.parametrize("case", sorted(QUARANTINED_CASES))
def test_a_record_that_fails_verification_fails_only_its_tenant_closed(
    case: str, offload_above: int, caplog: pytest.LogCaptureFixture,
) -> None:
    lab = Lab(offload_above=offload_above)
    good = lab.queue_delta(QUARANTINED_CASES[case])

    with caplog.at_level(logging.DEBUG, logger=LOGGER):
        report = lab.drain()

    _assert_cycle_completed(lab, report, good)
    state = lab.snapshot.lookup(HOSTILE)
    assert isinstance(state, PlanUnavailable), "never the superseded last-good plan"
    assert state.reason == PLAN_QUARANTINED
    held = lab.snapshot.held_back(HOSTILE)
    assert held is not None and held.state == QUARANTINED
    assert report.quarantined == 1 and report.rejected == 0
    series = lab.metrics.snapshot().series()
    assert series['amf_state_records_quarantined_total{kind="plan"}'] == 1
    text = _assert_logs_clean(caplog)
    assert f"plan record quarantined: org={HOSTILE!r} feed_seq={held.feed_seq}" in text


def test_a_quarantined_tenant_is_restored_by_the_next_verified_record() -> None:
    lab = Lab(offload_above=1_000)
    lab.queue_delta(_bad_signature)
    lab.drain()
    assert isinstance(lab.snapshot.lookup(HOSTILE), PlanUnavailable)

    lab.publish(HOSTILE, _body(HOSTILE))
    report = lab.drain()

    assert report.ok and report.quarantined == 0
    assert lab.served(HOSTILE).feed_seq == lab.position
    assert lab.snapshot.held_back(HOSTILE) is None


def test_a_rejection_after_a_quarantine_serves_the_previous_valid_plan() -> None:
    """The newest record is verified again, so GW05's rejection rule decides, not the fault."""
    lab = Lab(offload_above=1_000)
    lab.queue_delta(_bad_signature)
    lab.drain()

    _compile_error(lab)
    lab.drain()

    assert lab.served(HOSTILE).sequence == lab.previous
    held = lab.snapshot.held_back(HOSTILE)
    assert held is not None and held.state == REJECTED


def test_a_quarantined_tenant_is_not_served_last_good_even_inside_the_freshness_window() -> None:
    """The snapshot's last-known-good grace is for an unreachable store, not a failed record."""
    plans = PlanStore()
    snapshot = ReplicaSnapshot(plans, clock=lambda: 1.0)
    applier = PlanDeltaApplier(plans, clock=lambda: 1.0)  # no snapshot: nothing is forgotten
    applier.apply([make_record(SECRET, StateKind.PLAN, "org", _body("org"), Version(1, 1), 1)])
    assert isinstance(snapshot.lookup("org"), ExecutionPlan)  # cached as last-known-good

    plans.quarantine("org", feed_seq=2, error="StoreDataUnavailable")

    state = snapshot.lookup("org")
    assert isinstance(state, PlanUnavailable) and state.reason == PLAN_QUARANTINED


def test_a_newer_publish_landing_mid_round_is_skipped_not_quarantined() -> None:
    """The writer replaced the record between the index read and the record read."""
    lab = Lab(offload_above=1_000)
    newer = make_record(SECRET, StateKind.PLAN, HOSTILE, _body(HOSTILE), Version(1, 999), 999)

    def race(lab: Lab) -> None:
        lab.publish(HOSTILE, _body(HOSTILE))
        lab.set_raw(HOSTILE, encode_record(newer))  # index + manifest still say the older one

    lab.queue_delta(race)
    report = lab.drain()

    assert report.ok and report.quarantined == 0
    assert lab.served(HOSTILE).sequence == lab.previous, "not applied yet: above the manifest"
    assert lab.snapshot.held_back(HOSTILE) is None
    lab.publish_record(newer)  # the writer's manifest and index catch up
    lab.drain()
    assert lab.served(HOSTILE).sequence == 999


def test_without_isolation_one_bad_record_still_refuses_the_round() -> None:
    """KS and BUDGET keep the whole-round refusal; the reader's default is unchanged."""
    lab = Lab(offload_above=1_000)
    lab.queue_delta(_bad_signature)
    reader = FeedReader(lab.store, SECRET)

    with pytest.raises(StoreDataUnavailable, match="fails its signature"):
        _run(reader.poll(StateKind.PLAN, lab.sync.cursor(StateKind.PLAN), limit=256))


def test_the_applied_version_report_tells_the_console() -> None:
    lab = Lab(offload_above=1_000)
    lab.queue_delta(_bad_signature)
    lab.drain()
    _compile_error(lab)
    lab.publish("org-q", _body("org-q"))
    lab.drain()
    lab.publish("org-q", _body("org-q"))
    lab.set_raw("org-q", b"{}")
    lab.drain()

    rejected = report_applied(
        replica_id="r", snapshot=lab.snapshot, org_id=HOSTILE, now=1.0, fresh_s=5.0,
    )
    quarantined = report_applied(
        replica_id="r", snapshot=lab.snapshot, org_id="org-q", now=1.0, fresh_s=5.0,
    )
    healthy = report_applied(
        replica_id="r", snapshot=lab.snapshot, org_id="org-a0", now=1.0, fresh_s=5.0,
    )

    assert rejected["version"] is not None
    assert rejected["held_back"]["state"] == REJECTED
    assert rejected["held_back"]["error"] == "CompileError"
    assert quarantined["version"] is None
    assert quarantined["held_back"]["state"] == QUARANTINED
    assert healthy["held_back"] is None
    assert CANARY not in json.dumps([rejected, quarantined, healthy], default=str)


# --- kind isolation: whatever one kind raises, every other kind is drained ---------------------


class _Boom(Exception):
    pass


def test_an_unexpected_exception_in_one_kind_never_stops_the_others(
    caplog: pytest.LogCaptureFixture,
) -> None:
    store = FakeStore()
    record = make_record(SECRET, StateKind.PLAN, "org-a", _body("org-a"), Version(1, 1), 1)
    store.publish(record, count=1)
    store.publish(
        make_record(SECRET, StateKind.KS, "org:org-a", {"on": False}, Version(1, 1), 1), count=1,
    )
    plans = PlanStore()
    calls: list[StateKind] = []

    def boom(round_: FeedRound) -> None:
        calls.append(round_.kind)
        raise _Boom(CANARY)

    sync = StateSynchroniser(
        FeedReader(store, SECRET),
        {
            StateKind.KS: boom,  # first: before the fix it aborted the drain right here
            StateKind.PLAN: plan_applier(plans, clock=lambda: 1.0),
        },
    )
    with caplog.at_level(logging.DEBUG, logger=LOGGER):
        first = _run(sync.drain_all())
        second = _run(sync.drain_all())

    assert [report.kind for report in first] == [StateKind.KS, StateKind.PLAN]
    assert not first[0].ok and first[0].error == "_Boom"
    assert first[1].ok and isinstance(plans.read("org-a"), ExecutionPlan)
    assert sync.cursor(StateKind.KS).feed_seq == 0, "a failed round never advances its cursor"
    assert not second[0].ok and calls == [StateKind.KS, StateKind.KS]
    text = "\n".join(entry.getMessage() for entry in caplog.records)
    assert CANARY not in text
    assert text.count("state round failed: kind=ks error=_Boom") == 1, "a standing fault logs once"


def test_an_unexpected_exception_reading_the_feed_is_a_failed_round() -> None:
    class Reader(FeedReader):
        async def poll(self, *args: object, **kwargs: object) -> FeedRound:
            raise RecursionError(CANARY)

    sync = StateSynchroniser(Reader(FakeStore(), SECRET), {StateKind.PLAN: lambda _r: None})

    report = _run(sync.round_once(StateKind.PLAN))

    assert not report.ok and report.error == "RecursionError"


def test_a_kill_switch_parser_fault_latches_the_snapshot_stale_at_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Drift latch on ANY exception: a half-applied delta must not read FRESH for `stale_ms`."""
    from gateway_v2.admit import killswitch as ks_module

    switches = KillSwitchSnapshot(stale_ms=5_000, clock=lambda: 100.0)
    apply = killswitch_applier(switches)
    ok = make_record(SECRET, StateKind.KS, "org:a", {"on": True}, Version(1, 1), 1)
    store = FakeStore()
    store.publish(ok, count=1, on_count=1)
    round_ = _run(FeedReader(store, SECRET).poll(StateKind.KS, START, limit=8))
    apply(round_)
    assert switches.state(100.0) is not KillSwitchState.STALE

    def explode(record: SignedRecord) -> bool:
        raise RecursionError(CANARY)

    monkeypatch.setattr(ks_module, "scope_is_on", explode)
    with pytest.raises(RecursionError):
        apply(round_)
    assert switches.state(100.0) is KillSwitchState.STALE


# --- the depth bound: exact on valid JSON, linear, never recursive ------------------------------


def _random_json(rng: random.Random, depth: int) -> object:
    if depth == 0 or rng.random() < 0.2:
        return rng.choice(
            ["s", 'q"uote', "back\\slash", "[not]", "{nor}", "\\\"]", 1, 2.5, True, None],
        )
    if rng.random() < 0.5:
        return [_random_json(rng, depth - 1) for _ in range(rng.randint(0, 3))]
    return {f"k{n}]{{": _random_json(rng, depth - 1) for n in range(rng.randint(0, 3))}


def _true_depth(value: object) -> int:
    if isinstance(value, list):
        return 1 + max((_true_depth(item) for item in value), default=0)
    if isinstance(value, dict):
        return 1 + max((_true_depth(item) for item in value.values()), default=0)
    return 0


def _cases() -> Iterator[object]:
    rng = random.Random(0x5EED)
    for _ in range(400):
        yield _random_json(rng, rng.randint(0, 12))


def test_nesting_depth_is_exact_on_valid_json() -> None:
    for value in _cases():
        raw = json.dumps(value, ensure_ascii=False).encode()
        assert nesting_depth(raw) == _true_depth(json.loads(raw)), raw[:120]


@pytest.mark.parametrize(
    "raw,depth",
    [
        (b"", 0),
        (b'"[[["', 0),
        (b'"\\"[[["', 0),
        (b'"\\\\"[[', 2),
        (b"[[[", 3),
        (b"]]][", 0),  # json raises at byte 0 and never reaches the "["
        (b'{"a":"\\u005b","b":[[]]}', 3),
    ],
)
def test_nesting_depth_edge_cases(raw: bytes, depth: int) -> None:
    assert nesting_depth(raw) == depth


def test_a_body_one_level_over_the_bound_is_a_compile_error_and_at_the_bound_is_not_one() -> None:
    from gateway_v2.plan.compiler import CompileError

    def body(levels: int) -> bytes:
        # the body object itself is level 1
        return b'{"x":' + b"[" * (levels - 1) + b"]" * (levels - 1) + b"}"

    with pytest.raises(CompileError, match="nests"):
        decode_plan_body(body(MAX_BODY_DEPTH + 1))
    with pytest.raises(CompileError) as caught:
        decode_plan_body(body(MAX_BODY_DEPTH))
    assert "nests" not in str(caught.value), "at the bound the body is parsed (then refused)"


def test_a_published_body_is_far_below_the_bound() -> None:
    document = PlanDocument("org", StreamingMode.INCREMENTAL, (_draft(),))
    raw = json.dumps(encode_plan_body(document)).encode()
    assert nesting_depth(raw) <= 6 < MAX_BODY_DEPTH


def test_a_deeply_nested_stamp_is_an_invalid_stamp_not_an_aborted_cycle() -> None:
    """`_envelope` again: the namespace-wide stamp goes through the same decoder."""
    from gateway_v2.runtime.state_stamp import StampView

    store = FakeStore()
    store.publish(
        make_record(SECRET, StateKind.PLAN, "org-a", _body("org-a"), Version(1, 1), 1), count=1,
    )
    store.stamp_raw = b"[" * 100_000 + b"]" * 100_000
    plans = PlanStore()
    stamp = StampView(SECRET)
    sync = StateSynchroniser(
        FeedReader(store, SECRET),
        {StateKind.PLAN: plan_applier(plans, clock=lambda: 1.0)},
        stamp=stamp,
    )

    reports = _run(sync.drain_all())

    assert [report.ok for report in reports] == [True]
    assert stamp.invalid == 1 and isinstance(plans.read("org-a"), ExecutionPlan)
