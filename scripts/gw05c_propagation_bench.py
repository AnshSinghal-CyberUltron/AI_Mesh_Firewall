#!/usr/bin/env python3
"""GW05c — measure the two L05c-1 criteria that do not need a lane.

L05c-1 has five pass criteria. Three of them (C4 p99, infra %, loop lag p99.9) require the full
request path under offered load with a guard, and `gateway_v2/edge/` is still stubs, so they are
doubly blocked: no lane AND no serving path. Two do not:

    Store publish per write          <= 25 ms
    Kill-switch refresh cost         flat with tenant count, within +/-20% of the 1k baseline

Both are properties of the propagation layer alone, so they can be measured on one machine
against a real Postgres and a real Valkey. These are the first DURATIONS anywhere in this card;
everything before was a count.

WHAT THIS IS NOT
----------------
It is not L05c-1 and must not be quoted as it. One machine, the store on loopback, no gateway
process, no guard, no offered load, no 12 workers competing for a loop. That matters in two
opposite directions and the difference is not symmetric:

* Publish latency here is **optimistic**. A loopback round trip is far cheaper than a cross-zone
  Memorystore one, and nothing else is contending for the store. Treat a pass as necessary, not
  sufficient; treat a FAIL as conclusive, because the real thing is slower.
* The flatness ratio is **scale-invariant** and is the claim that actually matters. If a refresh
  round costs the same at 1k and 25k here, it is because the work does not depend on the estate,
  and that reason does not change on a bigger machine.

E2-01 writes every 5 s for 300 s, so each phase does 60 writes. This does 60 per kind per level,
to keep the sample size comparable.

    usage: AMF_PG_DSN=... AMF_VALKEY_URL=... python scripts/gw05c_propagation_bench.py
           scripts/gw05c_propagation_bench.py --levels 1000,10000
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "gateway_v2"))

from gateway_v2.admit.killswitch import (
    KillSwitchSnapshot,
    killswitch_applier,
)
from gateway_v2.domain.category import Category
from gateway_v2.domain.plan import (
    Action,
    FailurePosture,
    Mode,
    RuleScope,
    StreamingMode,
    Surface,
)
from gateway_v2.domain.state import StateKind
from gateway_v2.plan.compiler import RuleDraft
from gateway_v2.plan.delta import plan_applier
from gateway_v2.plan.document import PlanDocument, encode_plan_body
from gateway_v2.plan.snapshot import ReplicaSnapshot
from gateway_v2.plan.store import PlanStore
from gateway_v2.runtime.state_feed import FeedReader
from gateway_v2.runtime.state_task import DeltaBudget, StateSynchroniser
from gateway_v2.runtime.store_keys import StoreKeys
from gateway_v2.runtime.store_valkey import ValkeyStateStore
from state_control.pg import PostgresControlDB
from state_control.valkey import ValkeyPublisher
from state_control.writer import StateWriter

SECRET = b"gw05c-propagation-bench"
WRITES = 60
"""E2-01 writes every 5 s over a 300 s phase."""

PUBLISH_BUDGET_MS = 25.0
FLATNESS_TOLERANCE = 0.20
REFRESH_SAMPLES = 300
OP_TIMEOUT_S = 0.25

SURFACES = frozenset(
    {Surface.CHAT, Surface.MCP, Surface.RAG, Surface.VECTOR, Surface.EMBEDDINGS},
)


def _draft() -> RuleDraft:
    return RuleDraft(
        rule_id="r1",
        category=Category.PROMPT_INJECTION,
        mode=Mode.ENFORCE,
        action=Action.BLOCK,
        threshold=0.8,
        priority=10,
        scope=RuleScope.BOTH,
        on_unavailable=FailurePosture.FAIL_CLOSED,
        surfaces=SURFACES,
    )


def _plan_body(org_id: str) -> dict[str, object]:
    return encode_plan_body(PlanDocument(org_id, StreamingMode.INCREMENTAL, (_draft(),)))


class TimingPublisher:
    """Delegates to the real publisher and records ONLY the store publish duration.

    L05c-1's criterion is "store publish per write", which is what RC2's `pub_ms` measured. The
    surrounding Postgres transaction is a separate cost and is reported separately: this layer
    opens a connection per operation, which is irrelevant at E2-01's write rate (one write every
    5 s) and is on no serving path, but it would otherwise dominate and misattribute the number.
    """

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self.publish_ms: list[float] = []

    def stored_feed_seq(self, kind: StateKind) -> int | None:
        return self._inner.stored_feed_seq(kind)

    def stored_head(self, kind: StateKind) -> Any:
        return self._inner.stored_head(kind)

    def stored_index(self, kind: StateKind) -> Any:
        return self._inner.stored_index(kind)

    def publish_record(self, record: Any, manifest: Any, **kwargs: Any) -> bool:
        started = time.perf_counter_ns()
        try:
            return bool(self._inner.publish_record(record, manifest, **kwargs))
        finally:
            self.publish_ms.append(_ms(started))

    def publish_batch(self, records: Any, manifest: Any, **kwargs: Any) -> bool:
        return bool(self._inner.publish_batch(records, manifest, **kwargs))

    def publish_kind(self, kind: Any, records: Any, manifest: Any, engaged: Any = ()) -> bool:
        return bool(self._inner.publish_kind(kind, records, manifest, engaged))

    def take(self) -> list[float]:
        taken, self.publish_ms = self.publish_ms, []
        return taken


@dataclass(frozen=True, slots=True)
class Sample:
    """Durations in milliseconds. p99 of 60 samples is the 60th-percentile-of-60 reality."""

    count: int
    p50: float
    p99: float
    maximum: float

    @classmethod
    def of(cls, values: list[float]) -> Sample:
        ordered = sorted(values)
        return cls(
            count=len(ordered),
            p50=round(statistics.median(ordered), 3),
            p99=round(ordered[min(int(len(ordered) * 0.99), len(ordered) - 1)], 3),
            maximum=round(ordered[-1], 3),
        )

    def as_dict(self) -> dict[str, float | int]:
        return {"n": self.count, "p50_ms": self.p50, "p99_ms": self.p99, "max_ms": self.maximum}


def _ms(started_ns: int) -> float:
    return (time.perf_counter_ns() - started_ns) / 1e6


class Level:
    """One tenant level: seed the estate, then measure."""

    def __init__(self, dsn: str, valkey_url: str, tenants: int) -> None:
        import psycopg
        import redis

        self.tenants = tenants
        self.schema = f"amf_bench_{uuid.uuid4().hex[:10]}"
        with psycopg.connect(dsn, autocommit=True) as admin, admin.cursor() as cursor:
            cursor.execute(f'CREATE SCHEMA "{self.schema}"')
        self._admin_dsn = dsn
        self.keys = StoreKeys(namespace=f"{{b{self.schema[-8:]}}}")
        self.client = redis.Redis.from_url(valkey_url, socket_timeout=OP_TIMEOUT_S)
        self.db = PostgresControlDB(f"{dsn}?options=-csearch_path%3D{self.schema}")
        self.db.init_schema()
        self.publisher = TimingPublisher(ValkeyPublisher(self.client, SECRET, self.keys))
        self.writer = StateWriter(self.db, self.publisher, SECRET)

    def seed(self) -> float:
        started = time.perf_counter_ns()
        self.writer.put_many(
            StateKind.PLAN,
            [(f"org-{n}", _plan_body(f"org-{n}")) for n in range(self.tenants)],
        )
        self.writer.put_many(
            StateKind.KS,
            [(f"org:org-{n}", {"on": False}) for n in range(self.tenants)],
            engaged={},
        )
        self.writer.put_many(
            StateKind.KEY,
            [
                (
                    f"hash-{n}",
                    {
                        "key_id": f"k{n}",
                        "org_id": f"org-{n % 200}",
                        "rate_per_s": 1.0,
                        "burst": 2.0,
                    },
                )
                for n in range(2_000)
            ],
        )
        return round(_ms(started) / 1000, 2)

    def publish_cost(self) -> dict[str, Any]:
        """One write per sample, with the estate already at `tenants`. E2-01 phases B, C, D."""
        out: dict[str, Any] = {}
        for phase, label, write in (
            ("B", "key_add", self._write_key),
            ("C", "plan_set", self._write_plan),
            ("D", "killswitch", self._write_ks),
        ):
            write(-1)  # warm the connection and the statement cache
            self.publisher.take()
            end_to_end: list[float] = []
            records: set[int] = set()
            for index in range(WRITES):
                started = time.perf_counter_ns()
                published = write(index)
                end_to_end.append(_ms(started))
                records.add(published)
            out[f"phase_{phase}_{label}"] = {
                "store_publish": Sample.of(self.publisher.take()).as_dict(),
                "write_end_to_end": Sample.of(end_to_end).as_dict(),
                "records_per_publish": sorted(records),
            }
        return out

    def _write_plan(self, index: int) -> int:
        return self.writer.plan_set(f"org-{index % 500}", _plan_body(f"org-{index % 500}")).records

    def _write_key(self, index: int) -> int:
        return self.writer.key_add(
            f"hash-{index % 500}", "org-0", key_id="k", rate_per_s=1.0, burst=2.0,
        ).records

    def _write_ks(self, index: int) -> int:
        return self.writer.killswitch(f"org:org-{index % 500}", on=index % 2 == 0).records

    def refresh_cost(self) -> dict[str, Any]:
        """A worker's steady refresh round: the R2-02 headline. Wall AND cpu."""
        import asyncio

        async def measure() -> dict[str, Any]:
            import redis.asyncio

            client = redis.asyncio.Redis.from_url(
                os.environ["AMF_VALKEY_URL"], socket_timeout=OP_TIMEOUT_S,
            )
            try:
                plans = PlanStore()
                snapshot = ReplicaSnapshot(plans, clock=lambda: 1.0)
                switches = KillSwitchSnapshot(stale_ms=5_000, clock=time.monotonic)
                sync = StateSynchroniser(
                    FeedReader(ValkeyStateStore(client, self.keys), SECRET),
                    {
                        StateKind.PLAN: plan_applier(plans, snapshot, clock=lambda: 1.0),
                        StateKind.KS: killswitch_applier(switches),
                    },
                    budget=DeltaBudget(records=5_000),
                )
                # Catch up first: the steady state is what runs every period.
                caught_up = time.perf_counter_ns()
                for kind in (StateKind.PLAN, StateKind.KS):
                    report = await sync.drain(kind)
                    assert report.ok, report.error
                catch_up_s = round(_ms(caught_up) / 1000, 2)

                result: dict[str, Any] = {"cold_catch_up_s": catch_up_s}
                for kind in (StateKind.KS, StateKind.PLAN):
                    await sync.round_once(kind)  # warm
                    wall: list[float] = []
                    cpu: list[float] = []
                    for _ in range(REFRESH_SAMPLES):
                        cpu_started = time.process_time_ns()
                        started = time.perf_counter_ns()
                        report = await sync.round_once(kind)
                        wall.append(_ms(started))
                        cpu.append((time.process_time_ns() - cpu_started) / 1e6)
                        assert report.ok, report.error
                        assert report.applied == 0, "a steady round must apply nothing"
                    result[f"{kind.value}_steady_wall"] = Sample.of(wall).as_dict()
                    result[f"{kind.value}_steady_cpu"] = Sample.of(cpu).as_dict()
                return result
            finally:
                await client.aclose()

        return asyncio.run(measure())

    def drop(self) -> None:
        import psycopg

        found = list(self.client.scan_iter(match=f"{self.keys.namespace}*"))
        if found:
            self.client.delete(*found)
        with psycopg.connect(self._admin_dsn, autocommit=True) as admin, admin.cursor() as cur:
            cur.execute(f'DROP SCHEMA "{self.schema}" CASCADE')


def _verdict(levels: dict[str, Any], tenant_levels: list[int]) -> dict[str, Any]:
    baseline = str(tenant_levels[0])
    checks: dict[str, Any] = {}

    worst_publish = 0.0
    worst_write = 0.0
    for name, data in levels.items():
        for phase, sample in data["publish"].items():
            worst_publish = max(worst_publish, float(sample["store_publish"]["p99_ms"]))
            worst_write = max(worst_write, float(sample["write_end_to_end"]["p99_ms"]))
            if sample["records_per_publish"] != [1]:
                checks.setdefault("publish_is_per_record", []).append(f"{name}/{phase}")
    checks["write_end_to_end_p99_ms"] = round(worst_write, 3)
    checks["publish_per_write_p99_ms"] = round(worst_publish, 3)
    checks["publish_within_25ms"] = worst_publish <= PUBLISH_BUDGET_MS
    checks["publish_is_per_record"] = "publish_is_per_record" not in checks

    base_cpu = float(levels[baseline]["refresh"]["ks_steady_cpu"]["p50_ms"])
    base_wall = float(levels[baseline]["refresh"]["ks_steady_wall"]["p50_ms"])
    ratios: dict[str, dict[str, float]] = {}
    flat = True
    for name, data in levels.items():
        cpu = float(data["refresh"]["ks_steady_cpu"]["p50_ms"])
        wall = float(data["refresh"]["ks_steady_wall"]["p50_ms"])
        ratios[name] = {
            "cpu_vs_baseline": round(cpu / base_cpu, 3) if base_cpu else 0.0,
            "wall_vs_baseline": round(wall / base_wall, 3) if base_wall else 0.0,
        }
        if base_cpu and cpu / base_cpu > 1 + FLATNESS_TOLERANCE:
            flat = False
    checks["killswitch_refresh_ratios"] = ratios
    checks["killswitch_refresh_flat_within_20pct"] = flat
    return checks


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--levels", default="1000,10000,25000")
    parser.add_argument("--out", default="")
    args = parser.parse_args(argv)

    dsn = os.environ.get("AMF_PG_DSN", "")
    valkey_url = os.environ.get("AMF_VALKEY_URL", "")
    if not (dsn and valkey_url):
        print("AMF_PG_DSN and AMF_VALKEY_URL must be set", file=sys.stderr)
        return 2

    tenant_levels = [int(part) for part in args.levels.split(",") if part.strip()]
    levels: dict[str, Any] = {}
    for tenants in tenant_levels:
        print(f"==> {tenants} tenants: seeding", flush=True)
        level = Level(dsn, valkey_url, tenants)
        try:
            seed_s = level.seed()
            print(f"    seeded in {seed_s}s; measuring publish", flush=True)
            publish = level.publish_cost()
            print("    measuring refresh", flush=True)
            refresh = level.refresh_cost()
            levels[str(tenants)] = {
                "tenants": tenants,
                "seed_s": seed_s,
                "publish": publish,
                "refresh": refresh,
            }
        finally:
            level.drop()

    report = {
        "card": "GW05c",
        "measurement": "propagation cost only -- NOT L05c-1",
        "what_this_covers": [
            "store publish per write <= 25 ms",
            "kill-switch refresh cost flat with tenant count within +/-20% of 1k",
        ],
        "what_needs_a_lane": [
            "C4 p99 < 20 ms",
            "infra <= 0.1%",
            "loop lag p99.9 <= 5 ms",
        ],
        "why": (
            "one machine, store on loopback, no gateway process, no guard, no offered load. "
            "publish numbers are OPTIMISTIC versus a cross-zone store, so a pass is necessary "
            "and not sufficient; a failure would be conclusive. the flatness ratio is "
            "scale-invariant and is the claim that matters."
        ),
        "rc2_baseline": {
            "killswitch_refresh_ms": {"1k": 8.5, "10k": 75.0, "25k": 213.0},
            "publish_p50_ms": {"1k": "30-76", "10k": "114-623", "25k": "255-1724"},
        },
        "writes_per_phase": WRITES,
        "refresh_samples": REFRESH_SAMPLES,
        "levels": levels,
        "checks": _verdict(levels, tenant_levels),
    }
    text = json.dumps(report, indent=2)
    print(text)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    checks = report["checks"]
    ok = bool(
        checks["publish_within_25ms"]
        and checks["publish_is_per_record"]
        and checks["killswitch_refresh_flat_within_20pct"],
    )
    print(f"\nPASS={ok}", file=sys.stderr)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
