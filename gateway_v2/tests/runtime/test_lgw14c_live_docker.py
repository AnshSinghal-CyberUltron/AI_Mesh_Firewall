"""GW14c against a REAL Valkey 8. Skipped unless AMF_LIVE_VALKEY=1.

Three things only a real store can answer, and each one is a claim the card rests on:

* **The byte model is above what Valkey actually charges.** The budget divides bytes, so if the
  model under-states the per-entry cost the store fills while every gauge reports the bound
  being honoured. Measured here against `MEMORY USAGE` and against the `used_memory` delta, at
  payload sizes from 315 B to 6 KB — the range the reference measured.
* **`XTRIM` reports exactly what it removed.** The whole `audit_trimmed_records` counter, and
  therefore the M3 fix, is that one return value. A fake cannot prove it.
* **The bound actually holds under a flood.** Writing several times the budget leaves
  `used_memory` at the budget rather than at `maxmemory`. This is the mechanism half of L14c-1;
  the full gate, with the control-plane publishes and the TTL-key victims, is
  `test_lgw14c_f_audit_mem.py`.

Every run uses a PRIVATE container on loopback, removed afterwards. Nothing here may be pointed
at a shared store: it writes freely and the fixture flushes.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import time
import uuid
from collections.abc import Awaitable, Iterator

import pytest

from gateway_v2.audit.budget import ENTRY_BYTES, ENTRY_FACTOR, StreamBudget
from gateway_v2.audit.record import AuditPhase, AuditRecord
from gateway_v2.audit.sink import AuditSink
from gateway_v2.domain.audit_knobs import MIB, AuditMemoryKnobs
from gateway_v2.runtime.store_audit import ValkeyAuditStore
from gateway_v2.runtime.store_keys import StoreKeys
from gateway_v2.runtime.storemem import audit_budget, read_memory, startup_check

pytestmark = pytest.mark.skipif(
    os.environ.get("AMF_LIVE_VALKEY") != "1",
    reason="set AMF_LIVE_VALKEY=1 to run the audit budget proofs against a private Valkey 8",
)

IMAGE = os.environ.get("AMF_VALKEY_IMAGE", "valkey/valkey:8-alpine")
PORT = int(os.environ.get("AMF_VALKEY_PORT", "26398"))
MAXMEMORY_MB = 64
NAMESPACE = "{gw14c}"


def _run[T](coro: Awaitable[T]) -> T:
    return asyncio.run(coro)  # type: ignore[arg-type]


@pytest.fixture(scope="module")
def valkey_url() -> Iterator[str]:
    """A private 64 MB Valkey on loopback, with `noeviction` so a refused write is visible."""
    name = f"amf-gw14c-{uuid.uuid4().hex[:8]}"
    subprocess.run(["docker", "rm", "-f", name], capture_output=True, check=False)
    subprocess.run(
        [
            "docker", "run", "-d", "--rm", "--name", name,
            "-p", f"127.0.0.1:{PORT}:6379", IMAGE,
            "valkey-server",
            "--maxmemory", f"{MAXMEMORY_MB}mb",
            "--maxmemory-policy", "noeviction",
            "--save", "",
            "--appendonly", "no",
        ],
        check=True,
        capture_output=True,
        timeout=120,
    )
    url = f"redis://127.0.0.1:{PORT}"
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        probe = subprocess.run(
            ["docker", "exec", name, "valkey-cli", "ping"], capture_output=True, text=True,
            check=False,
        )
        if "PONG" in probe.stdout:
            break
        time.sleep(0.2)
    else:  # pragma: no cover - only on a broken docker host
        subprocess.run(["docker", "rm", "-f", name], capture_output=True, check=False)
        pytest.fail("the private Valkey never answered PING")
    try:
        yield url
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True, check=False)


def _client(url: str) -> object:
    import redis.asyncio as aioredis

    return aioredis.Redis.from_url(url, socket_timeout=5.0)


# --- the byte model against real measurements -----------------------------------------------------


@pytest.mark.parametrize(
    "payload_bytes",
    # 2 KB, 4 KB and 8 KB are here deliberately: each lands just above an allocator size class,
    # so the real cost jumps to ~1.25x the payload. The reference's 1.10 factor passed only
    # because it sampled 2.9 KB and 6 KB, which are two of the cheap points.
    [315, 512, 1_024, 2_048, 2_900, 4_096, 6_000, 8_192],
)
def test_the_byte_model_is_above_what_valkey_charges(valkey_url: str, payload_bytes: int) -> None:
    """Over-estimate, at every size. The direction of this error is the difference between
    "the budget holds with headroom" and "the store fills while the gauge says it is fine"."""

    async def measure() -> tuple[float, float, float]:
        client = _client(valkey_url)
        keys = StoreKeys(NAMESPACE)
        store = ValkeyAuditStore(client, keys)
        org = f"model-{payload_bytes}"
        key = keys.audit_stream(org)
        await client.delete(key)  # type: ignore[attr-defined]

        entries = 2_000
        payload = b"x" * payload_bytes
        before = (await read_memory(client)).used
        for start in range(0, entries, 500):
            del start
            await store.append([(org, payload)] * 500, {})
        after = (await read_memory(client)).used
        reported = await client.memory_usage(key)  # type: ignore[attr-defined]

        await client.delete(key)  # type: ignore[attr-defined]
        await client.aclose()  # type: ignore[attr-defined]
        return (
            (after - before) / entries,
            float(reported or 0) / entries,
            payload_bytes * ENTRY_FACTOR + ENTRY_BYTES,
        )

    used_delta, memory_usage, model = _run(measure())

    assert model >= used_delta, (
        f"the model charges {model:.0f} B per entry but used_memory grew {used_delta:.0f} B: "
        "under-estimating means the store fills while the budget reports it is being honoured"
    )
    assert model >= memory_usage, (
        f"the model charges {model:.0f} B but MEMORY USAGE reports {memory_usage:.0f} B"
    )


# --- XTRIM's return value -------------------------------------------------------------------------


def test_xtrim_reports_exactly_what_it_removed(valkey_url: str) -> None:
    """`audit_trimmed_records` is this number. If it were inferred, M3 would be back."""

    async def drive() -> tuple[int, int, int]:
        client = _client(valkey_url)
        keys = StoreKeys(NAMESPACE)
        store = ValkeyAuditStore(client, keys)
        org = "trim-exact"
        await client.delete(keys.audit_stream(org))  # type: ignore[attr-defined]

        # Fill well past the cap, then write one more batch WITH a cap and read back the answer.
        await store.append([(org, b"y" * 200)] * 3_000, {})
        before = await store.length(org)
        result = await store.append([(org, b"y" * 200)] * 10, {org: 500})
        after = await store.length(org)

        await client.delete(keys.audit_stream(org))  # type: ignore[attr-defined]
        await client.aclose()  # type: ignore[attr-defined]
        return before + 10, result.trimmed, after

    expected_len, trimmed, actual_len = _run(drive())

    assert trimmed > 0, "a stream far above its cap must report a trim"
    assert expected_len - trimmed == actual_len, (
        "the reported trim count must equal the observed length change exactly"
    )


def test_approximate_trim_converges_rather_than_stalling_one_batch(valkey_url: str) -> None:
    """Approximate XTRIM removes at most about 10,000 entries per call. A stream far above a new
    cap therefore converges over successive batches, which is why a freshly-bounded deployment
    does not drop to its budget on the first write -- stated in the adapter, proven here."""

    async def drive() -> list[int]:
        client = _client(valkey_url)
        keys = StoreKeys(NAMESPACE)
        store = ValkeyAuditStore(client, keys)
        org = "converge"
        await client.delete(keys.audit_stream(org))  # type: ignore[attr-defined]
        await store.append([(org, b"z" * 100)] * 25_000, {})

        lengths = []
        for _ in range(8):
            await store.append([(org, b"z" * 100)], {org: 100})
            lengths.append(await store.length(org))

        await client.delete(keys.audit_stream(org))  # type: ignore[attr-defined]
        await client.aclose()  # type: ignore[attr-defined]
        return lengths

    lengths = _run(drive())

    # It descends while it is far from the cap (each call removing up to ~10k entries) and then
    # SETTLES just above it, oscillating by the one record each batch adds. Asserting monotonic
    # descent would be asserting the wrong thing: once converged, a stream that keeps shrinking
    # is a stream being over-trimmed.
    assert lengths[0] > lengths[1] > lengths[2], "a far-above stream converges, batch by batch"
    settled = lengths[3:]
    assert settled, "the sample must reach the settled regime"
    assert all(100 <= length <= 100 + 200 for length in settled), (
        f"settled lengths {settled} must sit just above the cap of 100, not drift away from it"
    )
    assert max(settled) - min(settled) <= 5, (
        f"settled lengths {settled} must oscillate by about the one record each batch adds"
    )


# --- the bound holds under a flood ----------------------------------------------------------------


def test_the_budget_holds_under_a_flood_that_would_otherwise_fill_the_store(
    valkey_url: str,
) -> None:
    """The mechanism half of L14c-1: write several times the budget and the store does not fill.

    Unbounded, this same traffic is what took a 10.4 GiB instance from 1.48 to 6.42 GiB in 51
    minutes. Here the budget is 16 MB of a 64 MB store and the flood is ~3x maxmemory.
    """

    async def drive() -> tuple[int, int, int, int, int]:
        client = _client(valkey_url)
        keys = StoreKeys(NAMESPACE)
        await client.flushall()  # type: ignore[attr-defined]

        knobs = AuditMemoryKnobs(budget_mb=16)
        check = await startup_check(client, knobs, worker=0)
        budget = StreamBudget(knobs, lambda: 2)
        budget.adopt(check.budget)
        sink = AuditSink(ValkeyAuditStore(client, keys), budget, queue_depth=50_000)

        detail = {"padding": "p" * 2_600}
        for index in range(70_000):
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
            if sink.counters().queued >= 20_000:
                await sink.drain(30.0)
        await sink.drain(30.0)

        memory = await read_memory(client)
        counters = sink.counters()
        await client.flushall()  # type: ignore[attr-defined]
        await client.aclose()  # type: ignore[attr-defined]
        return (
            memory.used,
            memory.maxmemory,
            counters.written,
            counters.failed,
            counters.trimmed,
        )

    used, maxmemory, written, failed, trimmed = _run(drive())

    assert failed == 0, "under noeviction a refused write means the bound did not hold"
    assert written > 0
    assert trimmed > 0, "a flood past the budget must trim, and the trim must be counted"
    assert used < maxmemory, f"the store filled: used={used} maxmemory={maxmemory}"
    # The budget is 16 MB of 64 MB. Allow generous headroom for the store's own overhead and for
    # approximate trimming, and still assert it is nowhere near full.
    assert used < maxmemory * 0.75, (
        f"used={used} is {used / maxmemory:.0%} of maxmemory with a 25% budget"
    )


def test_an_unbounded_writer_is_the_negative_control(valkey_url: str) -> None:
    """The same traffic with NO budget resolved fills the store and the writes start failing.

    Without this arm the test above proves only that 70,000 records fit. With it, the bound is
    shown to be the thing doing the work -- the `noeviction` refusals ARE the R2-05 failure
    chain's second step.
    """

    async def drive() -> tuple[int, int, int]:
        client = _client(valkey_url)
        keys = StoreKeys(NAMESPACE)
        await client.flushall()  # type: ignore[attr-defined]

        # No budget, and the per-org upper bound far above what fits: RC2's configuration.
        knobs = AuditMemoryKnobs(stream_maxlen=2_000_000)
        budget = StreamBudget(knobs, lambda: 2)
        sink = AuditSink(ValkeyAuditStore(client, keys), budget, queue_depth=50_000)

        detail = {"padding": "p" * 2_600}
        for index in range(70_000):
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
            if sink.counters().queued >= 20_000:
                await sink.drain(30.0)
        await sink.drain(30.0)

        memory = await read_memory(client)
        counters = sink.counters()
        await client.flushall()  # type: ignore[attr-defined]
        await client.aclose()  # type: ignore[attr-defined]
        return memory.used, counters.failed, counters.trimmed

    used, failed, trimmed = _run(drive())

    assert trimmed == 0, "the unbounded arm must not trim; that is what makes it the control"
    assert failed > 0, (
        "the unbounded arm is expected to fill a 64 MB store and have writes REFUSED under "
        f"noeviction (used={used}); if it did not, the flood is too small to prove anything"
    )


# --- the budget follows the store -----------------------------------------------------------------


def test_the_budget_follows_a_live_resize(valkey_url: str) -> None:
    async def drive() -> tuple[int | None, int | None]:
        client = _client(valkey_url)
        knobs = AuditMemoryKnobs(fraction=0.5)
        await client.config_set("maxmemory", str(64 * MIB))  # type: ignore[attr-defined]
        first = audit_budget(knobs, await read_memory(client)).budget_bytes
        await client.config_set("maxmemory", str(128 * MIB))  # type: ignore[attr-defined]
        second = audit_budget(knobs, await read_memory(client)).budget_bytes
        await client.config_set("maxmemory", f"{MAXMEMORY_MB}mb")  # type: ignore[attr-defined]
        await client.aclose()  # type: ignore[attr-defined]
        return first, second

    first, second = _run(drive())

    assert first == 32 * MIB
    assert second == 64 * MIB
