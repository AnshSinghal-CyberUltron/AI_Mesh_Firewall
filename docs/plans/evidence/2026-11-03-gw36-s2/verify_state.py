"""GW36 S2 live verifier: read the REAL store the way a gateway worker does.

Runs inside a state-control container (it has `gateway_v2` and `redis`):

    docker compose exec -T state-projector-a python - < verify_state.py [args...]

It drains every kind with the gateway's own `ValkeyStateStore` + `FeedReader` + appliers and
observes the stamp with `StampView`, then prints one JSON object: what a worker would serve.
Nothing here writes to the store.

Args (optional): key hashes whose principal to resolve, e.g. `key=<sha256hex>`.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time

import redis.asyncio as aioredis

from gateway_v2.admit.identity import IdentityCache, identity_applier, principal_of
from gateway_v2.admit.killswitch import KillSwitchSnapshot, killswitch_applier
from gateway_v2.domain.plan import ExecutionPlan
from gateway_v2.domain.state import StateKind
from gateway_v2.plan.delta import plan_applier
from gateway_v2.plan.snapshot import ReplicaSnapshot
from gateway_v2.plan.store import PlanStore
from gateway_v2.runtime.state_feed import FeedReader
from gateway_v2.runtime.state_sig import decode_record
from gateway_v2.runtime.state_stamp import StampView
from gateway_v2.runtime.state_task import StateSynchroniser
from gateway_v2.runtime.store_keys import KEYS
from gateway_v2.runtime.store_valkey import ValkeyStateStore


async def main(argv: list[str]) -> dict[str, object]:
    secret = os.environ["AMF_STATE_HMAC_KEY"].encode("utf-8")
    client = aioredis.Redis.from_url(os.environ["AMF_STATE_VALKEY_URL"], socket_timeout=0.2)
    store = ValkeyStateStore(client)
    plans = PlanStore()
    snapshot = ReplicaSnapshot(plans)
    switches = KillSwitchSnapshot(stale_ms=5_000)
    keys = IdentityCache()
    sync = StateSynchroniser(
        FeedReader(store, secret),
        {
            StateKind.PLAN: plan_applier(plans, snapshot),
            StateKind.KS: killswitch_applier(switches),
            StateKind.KEY: identity_applier(keys),
        },
    )
    out: dict[str, object] = {"at": time.time()}
    for kind in (StateKind.PLAN, StateKind.KEY, StateKind.KS, StateKind.BUDGET):
        if kind is StateKind.BUDGET:
            head = await store.head(kind)
            out["budget_manifest"] = head.manifest_raw is not None
            continue
        report = await sync.drain(kind)
        out[kind.value] = {"ok": report.ok, "error": report.error, "cursor": str(report.cursor)}
    view = StampView(secret, started_at=0.0)
    accepted = view.observe(await store.stamp())
    out["stamp"] = {
        "accepted": accepted,
        "fresh": view.fresh(),
        "age_s": None if not accepted else round(view.age_seconds(), 3),
    }
    orgs = [a.split("=", 1)[1] for a in argv if a.startswith("org=")]
    out["plans"] = {
        org: (
            f"plan v{plan.epoch}.{plan.sequence} rules={len(plan.rules)}"
            if isinstance(plan := plans.read(org), ExecutionPlan)
            else type(plan).__name__
        )
        for org in orgs
    }
    out["killed"] = {org: switches.org_killed(org) for org in orgs}
    principals: dict[str, object] = {}
    for key_hash in [a.split("=", 1)[1] for a in argv if a.startswith("key=")]:
        (raw,) = await store.records(StateKind.KEY, (key_hash,))
        if raw is None:
            principals[key_hash[:12]] = "absent"
            continue
        principal = principal_of(decode_record(secret, StateKind.KEY, raw))
        principals[key_hash[:12]] = (
            "revoked"
            if principal is None
            else {"org_id": principal.org_id, "key_id": principal.key_id}
        )
    out["principals"] = principals
    out["keys_in_index"] = await client.zcard(KEYS.index(StateKind.KEY))
    await client.aclose()
    return out


if __name__ == "__main__":
    print(json.dumps(asyncio.run(main(sys.argv[1:])), indent=1, sort_keys=True))
