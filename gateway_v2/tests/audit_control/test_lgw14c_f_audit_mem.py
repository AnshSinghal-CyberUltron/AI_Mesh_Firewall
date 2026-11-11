"""L14c-1 — F-AUDIT-MEM. The card's exit test, against a private 64 MB Valkey 8.

    "64 MB store, audit flood. Pass: 5/5 registrations, 0 refused publishes or lease refills."

Round 2's chain, in order, is what this reproduces and then closes:

1. audit fills the store;
2. under an evicting policy the guard-owner registrations go first (the only TTL keys), so
   discovery reads 0/N and new gateways never become ready;
3. every write is then refused — including **the control plane's publishes**;
4. so a kill switch commits to Postgres and never reaches the fleet, which keeps serving the
   previous signed snapshot.

Four arms, exactly as the reference ran them: {RC2-shaped, bounded} x {volatile-lru, noeviction}.
The unbounded arms are the negative control; without them a pass proves only that the flood fit.

**What is honestly PARTIAL here, and why.** The card names three victims. Two are available in
this tree and one is not:

* **Control-plane publishes: REAL.** `state_control.ValkeyPublisher.publish_kind` is the actual
  publish path, and a refused publish here is step 3 of the chain verbatim.
* **Guard-owner registrations: a declared SURROGATE.** `gateway_v2/detect/guard/` has backends
  but no `discovery.py` / `OwnerRegistration` — that is GW08. The surrogate is the same shape
  (`SET ... PX 3000`, re-heartbeated every second) because the shape is the whole mechanism: a
  TTL key is what an evicting policy takes first and what a refused write lets expire. When GW08
  lands, this probe should switch to the real registration and the assertion is unchanged.
* **Lease refills: OPEN until GW19.** `gateway_v2/admit/quota.py` is a stub with no `TokenLease`
  and no lease Lua, so "0 refused lease refills" cannot be measured. It is NOT asserted here,
  and it is recorded as open in the plan rather than quietly dropped.

Private container, loopback only, removed after the run. It FLUSHALLs: never point it at a
shared store.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import time
import uuid
from collections.abc import Awaitable, Iterator
from typing import Any

import pytest

from gateway_v2.audit.budget import StreamBudget
from gateway_v2.audit.record import AuditPhase, AuditRecord
from gateway_v2.audit.sink import AuditSink
from gateway_v2.domain.audit_knobs import AuditMemoryKnobs
from gateway_v2.domain.state import StateKind
from gateway_v2.runtime.store_audit import ValkeyAuditStore
from gateway_v2.runtime.store_keys import StoreKeys
from gateway_v2.runtime.storemem import read_memory, startup_check

pytestmark = pytest.mark.skipif(
    os.environ.get("AMF_LIVE_VALKEY") != "1",
    reason="set AMF_LIVE_VALKEY=1 to run L14c-1 (F-AUDIT-MEM) against a private Valkey 8",
)

IMAGE = os.environ.get("AMF_VALKEY_IMAGE", "valkey/valkey:8-alpine")
PORT = int(os.environ.get("AMF_VALKEY_PORT_F", "26396"))
MAXMEMORY = "64mb"
NAMESPACE = "{f14c}"
SECRET = b"f-audit-mem-hmac-not-a-secret"
OWNERS = 5
OWNER_TTL_MS = 3_000
RECORD_PADDING = 2_600
"""Produces a ~2.9 KB serialized record: the size round 2 measured."""

FLOOD_RECORDS = 70_000
"""About 3.4x maxmemory of audit at this record size, as the reference drove it."""


def _run[T](coro: Awaitable[T]) -> T:
    return asyncio.run(coro)  # type: ignore[arg-type]


def _container(policy: str) -> Iterator[str]:
    name = f"amf-f14c-{uuid.uuid4().hex[:8]}"
    subprocess.run(["docker", "rm", "-f", name], capture_output=True, check=False)
    subprocess.run(
        [
            "docker", "run", "-d", "--rm", "--name", name,
            "-p", f"127.0.0.1:{PORT}:6379", IMAGE,
            "valkey-server",
            "--maxmemory", MAXMEMORY,
            "--maxmemory-policy", policy,
            "--save", "", "--appendonly", "no",
        ],
        check=True, capture_output=True, timeout=120,
    )
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        probe = subprocess.run(
            ["docker", "exec", name, "valkey-cli", "ping"],
            capture_output=True, text=True, check=False,
        )
        if "PONG" in probe.stdout:
            break
        time.sleep(0.2)
    try:
        yield f"redis://127.0.0.1:{PORT}"
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True, check=False)


class Victims:
    """The things an audit flood is supposed not to be able to harm.

    `registrations` is the declared surrogate for GW08's `OwnerRegistration`; `publish` is the
    real control-plane path.
    """

    def __init__(self, url: str, keys: StoreKeys) -> None:
        import redis

        from state_control.valkey import ValkeyPublisher

        self._sync = redis.Redis.from_url(url, socket_timeout=5.0)
        self._keys = keys
        self._publisher = ValkeyPublisher(self._sync, SECRET, keys)
        self.owner_ids = [f"owner-{index}" for index in range(OWNERS)]

    def heartbeat(self) -> int:
        """`SET ... PX 3000` per owner. Returns how many the store accepted."""
        accepted = 0
        for owner in self.owner_ids:
            try:
                if self._sync.set(f"{self._keys.namespace}:guard:{owner}", b"1", px=OWNER_TTL_MS):
                    accepted += 1
            except Exception:  # a refused write IS the finding; count it, do not raise
                pass
        return accepted

    def alive(self) -> int:
        alive = 0
        for owner in self.owner_ids:
            try:
                alive += 1 if self._sync.exists(f"{self._keys.namespace}:guard:{owner}") else 0
            except Exception:
                pass
        return alive

    def publish(self, feed_seq: int) -> bool:
        """A REAL control-plane publish of a kill-switch record. Step 3 is this returning False.

        It publishes an actual signed record rather than an empty kind, because the write has to
        ALLOCATE for the store to refuse it: overwriting an existing key with a same-sized value
        can succeed even at `maxmemory`, and a probe that does that would report "publishes are
        fine" through the exact outage it exists to detect.
        """
        from gateway_v2.domain.state import Version
        from gateway_v2.runtime.state_sig import make_manifest, make_record

        try:
            version = Version(epoch=1, seq=feed_seq)
            record = make_record(
                SECRET,
                StateKind.KS,
                f"org:probe-{feed_seq}",
                {"on": True, "pad": "x" * 512},
                version,
                feed_seq,
            )
            manifest = make_manifest(SECRET, StateKind.KS, version, feed_seq, 1, 1)
            return bool(
                self._publisher.publish_kind(
                    StateKind.KS, (record,), manifest, (f"org:probe-{feed_seq}",),
                ),
            )
        except Exception:
            # A refused write IS the finding. Signature drift would also land here, which is why
            # the bounded arms assert `publish_failures == 0` -- a probe that could never succeed
            # would fail those immediately rather than passing vacuously.
            return False

    def evicted(self) -> int:
        return int(self._sync.info("stats").get("evicted_keys", 0))

    def close(self) -> None:
        self._sync.close()


async def _flood(url: str, *, bounded: bool) -> dict[str, Any]:
    """Drive the audit flood, probing the victims throughout."""
    import redis.asyncio as aioredis

    client = aioredis.Redis.from_url(url, socket_timeout=10.0)
    keys = StoreKeys(NAMESPACE)
    await client.flushall()

    knobs = (
        AuditMemoryKnobs(budget_mb=16) if bounded
        # The RC2 configuration: a per-org record cap far above what fits, and no byte budget.
        else AuditMemoryKnobs(stream_maxlen=2_000_000)
    )
    budget = StreamBudget(knobs, lambda: 2)
    if bounded:
        budget.adopt((await startup_check(client, knobs, worker=0)).budget)
    sink = AuditSink(ValkeyAuditStore(client, keys), budget, queue_depth=25_000)

    victims = Victims(url, keys)
    probes: list[dict[str, Any]] = []
    detail = {"padding": "p" * RECORD_PADDING}
    next_probe = 0.0
    victims.heartbeat()
    await asyncio.sleep(0.1)

    for index in range(FLOOD_RECORDS):
        sink.emit(
            AuditRecord(
                request_id=f"req-{index}",
                org_id="org-a" if index % 2 else "org-b",
                phase=AuditPhase.INPUT,
                outcome="allow",
                recorded_at_ns=index,
                detail=detail,
            ),
        )
        if sink.counters().queued >= 10_000:
            await sink.drain(60.0)
            now = time.monotonic()
            if now >= next_probe:
                accepted = victims.heartbeat()
                probes.append(
                    {
                        "heartbeats_accepted": accepted,
                        "registrations_alive": victims.alive(),
                        "publish_ok": victims.publish(len(probes) + 1),
                        "evicted_keys": victims.evicted(),
                    },
                )
                next_probe = now + 0.5
    await sink.drain(60.0)
    probes.append(
        {
            "heartbeats_accepted": victims.heartbeat(),
            "registrations_alive": victims.alive(),
            "publish_ok": victims.publish(len(probes) + 1),
            "evicted_keys": victims.evicted(),
        },
    )

    memory = await read_memory(client)
    counters = sink.counters()
    result: dict[str, Any] = {
        "bounded": bounded,
        "policy": memory.policy,
        "maxmemory": memory.maxmemory,
        "used_memory_end": memory.used,
        "records_emitted": counters.produced,
        "audit_written": counters.written,
        "audit_write_failed": counters.failed,
        "audit_dropped": counters.dropped,
        "audit_trimmed": counters.trimmed,
        "registrations_alive_min": min(p["registrations_alive"] for p in probes),
        "heartbeats_refused": sum(OWNERS - p["heartbeats_accepted"] for p in probes),
        "publish_failures": sum(1 for p in probes if not p["publish_ok"]),
        "evicted_keys_end": probes[-1]["evicted_keys"],
        "probes": len(probes),
    }
    result["PASS"] = bool(
        result["registrations_alive_min"] == OWNERS
        and result["evicted_keys_end"] == 0
        and result["publish_failures"] == 0
        and result["audit_write_failed"] == 0
        and result["used_memory_end"] < result["maxmemory"],
    )
    victims.close()
    await client.flushall()
    await client.aclose()
    return result


def _arm(policy: str, *, bounded: bool) -> dict[str, Any]:
    gen = _container(policy)
    url = next(gen)
    try:
        return _run(_flood(url, bounded=bounded))
    finally:
        for _ in gen:
            pass


def _write_evidence(name: str, payload: dict[str, Any]) -> None:
    from pathlib import Path

    out = (
        Path(__file__).resolve().parents[3]
        / "docs" / "plans" / "evidence" / "2026-10-08-r2-05"
    )
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{name}.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


# --- the bounded arms: the card's PASS ------------------------------------------------------------


@pytest.mark.parametrize("policy", ["volatile-lru", "noeviction"])
def test_l14c_1_bounded_audit_cannot_harm_the_victims(policy: str) -> None:
    """**L14c-1.** 5/5 registrations, 0 evictions, 0 refused publishes, 0 refused audit writes."""
    result = _arm(policy, bounded=True)
    _write_evidence(f"l14c-1-bounded-{policy}", result)

    assert result["registrations_alive_min"] == OWNERS, (
        f"registrations {result['registrations_alive_min']}/{OWNERS}: an audit flood took the "
        "only TTL keys in the store, so discovery would read 0/N"
    )
    assert result["heartbeats_refused"] == 0
    assert result["evicted_keys_end"] == 0
    assert result["publish_failures"] == 0, (
        "a refused publish is step 3 of the chain: a kill switch commits to Postgres and never "
        "reaches the fleet"
    )
    assert result["audit_write_failed"] == 0
    assert result["used_memory_end"] < result["maxmemory"]
    assert result["audit_trimmed"] > 0, "the bound must be what did the work"
    assert result["PASS"] is True


# --- the unbounded arms: the negative control -----------------------------------------------------


@pytest.mark.parametrize("policy", ["volatile-lru", "noeviction"])
def test_l14c_1_negative_control_reproduces_the_round_two_chain(policy: str) -> None:
    """RC2's configuration, so the pass above is shown to be the bound's doing.

    `volatile-lru` is expected to EVICT the registrations; `noeviction` is expected to REFUSE
    the writes. Either one failing the victims is the finding; both arms passing cleanly would
    mean the flood is too small to prove anything.
    """
    result = _arm(policy, bounded=False)
    _write_evidence(f"l14c-1-unbounded-{policy}", result)

    assert result["audit_trimmed"] == 0, "the control arm must not trim; that is what it controls"
    harmed = (
        result["registrations_alive_min"] < OWNERS
        or result["evicted_keys_end"] > 0
        or result["publish_failures"] > 0
        or result["audit_write_failed"] > 0
    )
    assert harmed, (
        f"the unbounded arm did NOT reproduce the chain ({result}); the flood of "
        f"{FLOOD_RECORDS} records is too small against {MAXMEMORY} to prove the bound is "
        "doing any work"
    )
