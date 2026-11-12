"""Repro: one hostile PLAN record among many good ones, through the REAL gateway state runtime.

Run from gateway_v2/ with PYTHONPATH=. (it imports the test helpers for a valid rule draft):

    PYTHONPATH=. python ../docs/plans/evidence/2026-11-12-state-applier-isolation/repro_state_applier.py

The control-plane side is real: `StateWriter` signs and publishes into fakeredis and `Rehydrator`
stamps. The gateway side is the real `StateRuntime` (`cycle()` and the `_run` loop's handler).
Each scenario publishes 50 good tenants, ONE hostile record, then 50 more good tenants, plus one
KEY, one BUDGET and one kill switch, and reports what the gateway did with them:

* rewrite_deep_regex  - a REWRITE rule whose condition is 499 nested groups (RecursionError
                        out of the compiler on develop; #117 makes it a TemplateError)
* rewrite_overflow    - `a{4294967296}` in a REWRITE condition (OverflowError on develop)
* signature           - one stored plan record's bytes tampered (fails its HMAC)
* deep_envelope       - one stored plan record replaced by 100,000 nested arrays
                        (RecursionError inside the envelope decoder)

Nothing here prints a payload: the log lines are captured and checked for a canary string that
every hostile body carries, and only whether the canary leaked is reported.
"""

from __future__ import annotations

import asyncio
import io
import logging
import sys
from collections.abc import Callable, Mapping

import fakeredis

from gateway_v2.domain.plan import ExecutionPlan, StreamingMode
from gateway_v2.domain.state import StateKind
from gateway_v2.domain.state_knobs import StateKnobs
from gateway_v2.edge.state_runtime import StateRuntime, StateRuntimeConfig
from gateway_v2.plan.document import PlanDocument, encode_plan_body
from gateway_v2.runtime.store_keys import StoreKeys
from state_control.db import MemoryControlDB
from state_control.rehydrate import Rehydrator
from state_control.valkey import ValkeyPublisher
from state_control.writer import StateWriter
from tests.plan.test_lgw05c_delta import _draft

SECRET = b"repro-state-applier"
NAMESPACE = "{rv2}"
CANARY = "CANARY tenant secret 7f3a"
GOOD_BEFORE = 50
GOOD_AFTER = 50
KNOBS = StateKnobs(fresh_ms=1_500, rehydrate_period_ms=500)
"""A short freshness bound, so the repro can show readiness after the bound without waiting 5 s."""


def _body(org: str) -> dict[str, object]:
    return encode_plan_body(PlanDocument(org, StreamingMode.INCREMENTAL, (_draft(),)))


def _rewrite_body(org: str, pattern: str) -> dict[str, object]:
    body = _body(org)
    rules = body["rules"]
    assert isinstance(rules, list)
    rules.append({
        **rules[0], "rule_id": "deep", "action": "rewrite", "scope": "output",
        "category": "exfil", "rewrite_template": "Removed by policy {rule_id}",
        "condition": {
            "kind": "regex", "field": "", "direction": "output", "keywords": [],
            "pattern": pattern, "detector_class": "", "pipeline_stage": "",
        },
    })
    return body


class Lab:
    def __init__(self) -> None:
        self.server = fakeredis.FakeServer()
        self.sync = fakeredis.FakeRedis(server=self.server)
        self.keys = StoreKeys(NAMESPACE)
        self.publisher = ValkeyPublisher(self.sync, SECRET, self.keys)
        self.db = MemoryControlDB()
        self.writer = StateWriter(self.db, self.publisher, SECRET)
        self.rehydrator = Rehydrator(self.db, self.publisher, self.writer, SECRET, stale_grace_s=0.0)
        self.runtime = StateRuntime(
            StateRuntimeConfig("redis://unused", SECRET, NAMESPACE, KNOBS),
            client=fakeredis.FakeAsyncRedis(server=self.server),
        )

    def raw_plan(self, org: str) -> bytes:
        key = self.keys.record_key(StateKind.PLAN, org)
        value = self.sync.get(key)
        assert isinstance(value, bytes)
        return value

    def set_raw_plan(self, org: str, raw: bytes) -> None:
        self.sync.set(self.keys.record_key(StateKind.PLAN, org), raw)


def _hostile_body(org: str) -> Mapping[str, object]:
    return _body(org)


def _tamper_signature(lab: Lab, org: str) -> None:
    raw = lab.raw_plan(org)
    lab.set_raw_plan(org, raw.replace(b'"sig":"', b'"sig":"0', 1))


def _deep_envelope(lab: Lab, org: str) -> None:
    lab.set_raw_plan(org, b'{"x":"' + CANARY.encode() + b'","y":' + b"[" * 100_000 + b"]" * 100_000 + b"}")


SCENARIOS: dict[str, tuple[Callable[[str], Mapping[str, object]], Callable[[Lab, str], None] | None]] = {
    "rewrite_deep_regex": (lambda org: _rewrite_body(org, "(" * 499 + CANARY + ")" * 499), None),
    "rewrite_overflow": (lambda org: _rewrite_body(org, CANARY + "{4294967296}"), None),
    "signature": (_hostile_body, _tamper_signature),
    "deep_envelope": (_hostile_body, _deep_envelope),
}


def _served(lab: Lab, org: str) -> str:
    state = lab.runtime.snapshot.lookup(org)
    if isinstance(state, ExecutionPlan):
        return f"plan seq={state.sequence}"
    return type(state).__name__ + (f"({getattr(state, 'reason', '')})" if hasattr(state, "reason") else "")


def _good_served(lab: Lab) -> int:
    good = [f"good-a{n}" for n in range(GOOD_BEFORE)] + [f"good-b{n}" for n in range(GOOD_AFTER)]
    return sum(1 for org in good if isinstance(lab.runtime.snapshot.lookup(org), ExecutionPlan))


async def _scenario(name: str) -> None:
    make_body, tamper = SCENARIOS[name]
    lab = Lab()
    # A healthy, caught-up gateway first: the hostile tenant HAD a good plan.
    lab.writer.plan_set("victim", _body("victim"))
    lab.writer.key_add("a" * 64, "victim", key_id="id-1", rate_per_s=0.0, burst=0.0)
    lab.writer.budget_set("victim", 1000)
    lab.rehydrator.round_once()
    await lab.runtime.cycle()
    before_ready = lab.runtime.ready()
    # Then one delta: 50 good tenants, the hostile record, 50 more, then KEY/BUDGET/KS changes.
    for n in range(GOOD_BEFORE):
        lab.writer.plan_set(f"good-a{n}", _body(f"good-a{n}"))
    lab.writer.plan_set("victim", make_body("victim"))
    for n in range(GOOD_AFTER):
        lab.writer.plan_set(f"good-b{n}", _body(f"good-b{n}"))
    lab.writer.key_add("b" * 64, "good-a0", key_id="id-2", rate_per_s=0.0, burst=0.0)
    lab.writer.budget_set("good-a0", 500)
    lab.writer.put(StateKind.KS, "org:good-b0", {"on": True}, engaged=True)
    if tamper is not None:
        tamper(lab, "victim")
    lab.rehydrator.round_once()

    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    root = logging.getLogger("amf")
    root.addHandler(handler)
    root.setLevel(logging.DEBUG)
    try:
        try:
            reports = await lab.runtime.cycle()
            outcome = "cycle completed: " + ", ".join(
                f"{r.kind.value}={'ok' if r.ok else 'FAILED'}" for r in reports
            )
        except BaseException as exc:  # noqa: BLE001 - this is what the repro measures
            outcome = f"cycle RAISED {type(exc).__name__}"
        right_after = _good_served(lab)
        victim_right_after = _served(lab, "victim")
        # The `_run` loop's handler, exactly as it runs: one iteration, then stop.
        lab.runtime._config = StateRuntimeConfig(  # noqa: SLF001
            "redis://unused", SECRET, NAMESPACE, StateKnobs(fresh_ms=1_500, rehydrate_period_ms=500, refresh_period_ms=10),
        )
        task = asyncio.create_task(lab.runtime._run())  # noqa: SLF001
        await asyncio.sleep(0.5)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        # Past the freshness bound: a fresh stamp, one more cycle, then the /readyz answer.
        await asyncio.sleep(KNOBS.fresh_ms / 1000 + 0.1)
        lab.rehydrator.round_once()
        try:
            await lab.runtime.cycle()
        except BaseException:  # noqa: BLE001 - measured above; only readiness matters here
            pass
        after_ready = lab.runtime.ready()
    finally:
        root.removeHandler(handler)
    logs = stream.getvalue()
    print(f"== {name}")
    print(f"  before: ready={before_ready[0]}")
    print(f"  {outcome}")
    print(f"  right after the cycle: good tenants served {right_after}/{GOOD_BEFORE + GOOD_AFTER}; "
          f"hostile tenant 'victim': {victim_right_after}")
    print(f"  after the bound: good tenants served {_good_served(lab)}/{GOOD_BEFORE + GOOD_AFTER}; "
          f"hostile tenant 'victim': {_served(lab, 'victim')}")
    print(f"  KEY cursor={lab.runtime.sync.cursor(StateKind.KEY).feed_seq} "
          f"BUDGET good-a0={lab.runtime.budget_snapshot.record_for('good-a0') is not None} "
          f"KS org:good-b0 engaged={'good-b0' in lab.runtime.switches.view().orgs}")
    series = lab.runtime.metrics.snapshot().series()
    known = [k for k in series if "quarantined" in k or "rejected" in k]
    shown = {k: v for k, v in series.items() if k in known and v}
    print(f"  metrics: {shown if known else 'no quarantine/rejection series exist'}")
    print(f"  cycle_failures={lab.runtime.cycle_failures}")
    print(f"  after the {KNOBS.fresh_ms} ms freshness bound: ready={after_ready[0]} ({after_ready[1]})")
    print(f"  log lines: {len(logs.splitlines())}; canary (payload) in logs: {CANARY in logs}; "
          f"'maximum recursion' text in logs: {'maximum recursion' in logs}")
    for line in logs.splitlines()[:4]:
        print(f"    | {line[:160]}")


def main() -> int:
    sys.setrecursionlimit(1000)
    for name in SCENARIOS:
        asyncio.run(_scenario(name))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
