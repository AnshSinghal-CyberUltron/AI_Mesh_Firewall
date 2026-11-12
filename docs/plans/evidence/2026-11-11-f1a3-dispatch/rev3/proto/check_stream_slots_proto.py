"""F1a-3 plan revision 3: tests of the stream-slot prototype against develop's AdmissionController.

Each test pins one rule of plan §3.6 or one reviewer finding (A6 a-d, B-1 a/c, S-1). Run from a neutral
cwd with PYTHONPATH=<worktree>/gateway_v2:<this dir>:
  python -m pytest -q -p no:cacheprovider <this dir>/check_stream_slots_proto.py
"""
from __future__ import annotations

import asyncio
import random

import pytest
from gateway_v2.admit.admission import AdmissionController
from gateway_v2.admit.codel import CoDelParams
from gateway_v2.admit.grant import Admitted, ShedVerdict
from gateway_v2.admit.metrics import AdmissionMetrics
from gateway_v2.admit.quota import AdmissionBounds
from gateway_v2.admit.supervisor import WorkerSupervisor
from slots_core import SlotLimits, SlotPolicy
from stream_slots_proto import HandoffAdmission, InflightLimited, StreamSlots


def _controller(depth: int, box: dict) -> AdmissionController:
    """Reviewer A's helper (review 5871181186, test_rev2_probes.py), unchanged."""
    b = AdmissionBounds(concurrency=depth, request_depth=depth, guard_depth=depth,
                        dispatch_depth=depth, egress_depth=depth, audit_depth=depth)
    return AdmissionController(bounds=b, params=CoDelParams(), rng=random.Random(0),
                               supervisor=WorkerSupervisor(), metrics=AdmissionMetrics(),
                               drain_window_s=60.0, clock=lambda: box["t"])


def _limits(**kw) -> SlotLimits:
    base = {"slots": 4, "byte_budget": 4_000, "wait_total": 14, "wait_per_org": 1}
    base.update(kw)
    return SlotLimits(**base)


# ---------------------------------------------------------------- A6(a): the drain still sees streams
def test_handed_off_stream_stays_visible_to_the_drain() -> None:
    async def run(release_before_drain: bool) -> tuple[float, int, int]:
        box = {"t": 100.0}
        ctl = _controller(15, box)
        slots = StreamSlots(_limits(), HandoffAdmission(ctl))
        assert isinstance(await ctl.admit("org-a", "req-1", 100.0), Admitted)
        slot = await slots.acquire("org-a", "req-1", 100)
        depth_after_handoff = ctl.queue_report().per_queue["request"][0]
        if release_before_drain:
            slot.release()

        async def fake_sleep(s: float) -> None:
            box["t"] += s

        rep = await ctl.drain(accept_grace_s=0.0, drain_window_s=60.0, sleep=fake_sleep)
        return rep.duration_s, len(rep.declared_terminations), depth_after_handoff

    held = asyncio.run(run(False))
    done = asyncio.run(run(True))
    print(f"\ndrain with a handed-off live stream: {held}; after it ended: {done}")
    assert held == (60.0, 1, 0)  # the request-queue slot is free, the drain still waits and declares it
    assert done == (0.0, 0, 0)


def test_finished_records_are_pruned_while_accepting() -> None:
    async def run() -> int:
        ctl = _controller(15, {"t": 100.0})
        slots = StreamSlots(_limits(slots=64), HandoffAdmission(ctl))
        for i in range(1000):
            rid = f"r{i}"
            assert isinstance(await ctl.admit("o", rid, 100.0), Admitted)
            (await slots.acquire("o", rid, 1)).release()
        return len(ctl._in_flight)

    assert asyncio.run(run()) == 0


# ---------------------------------------------------------------- A6(b), B-1(a): waiters leave admission
@pytest.mark.parametrize("w", [1, 2])
def test_waiters_do_not_hold_the_shared_admission_queue(w: int) -> None:
    """Reviewer A's door probe on the rev-3 design: org A's 15 extra requests no longer shed org B."""

    async def run():
        ctl = _controller(15, {"t": 100.0})
        slots = StreamSlots(_limits(slots=2, wait_per_org=w), HandoffAdmission(ctl), wait_s=lambda: 5.0)
        held = []
        for i in range(2):
            await ctl.admit("org-a", f"h{i}", 100.0)
            held.append(await slots.acquire("org-a", f"h{i}", 10))
        outcomes, tasks = [], []
        for i in range(15):
            assert isinstance(await ctl.admit("org-a", f"a{i}", 100.0), Admitted)
            tasks.append(asyncio.ensure_future(slots.acquire("org-a", f"a{i}", 10)))
        await asyncio.sleep(0)
        b_admit = await ctl.admit("org-b", "b0", 100.0)
        b_task = asyncio.ensure_future(slots.acquire("org-b", "b0", 10))
        await asyncio.sleep(0)
        held[0].release()  # the freed slot goes to the org with the smallest share: B
        await asyncio.sleep(0)
        b_slot = await b_task
        for t in tasks:
            try:
                outcomes.append(("slot", await asyncio.wait_for(t, 0.01)))
            except InflightLimited as exc:
                outcomes.append(("refused", exc.reason))
            except TimeoutError:
                outcomes.append(("waiting", None))
        return b_admit, b_slot, outcomes, ctl.queue_report().per_queue["request"][0]

    b_admit, b_slot, outcomes, depth = asyncio.run(run())
    refused = sum(1 for o in outcomes if o[0] == "refused")
    print(f"\nw={w}: org-b admit={type(b_admit).__name__}, org-b slot={b_slot.org}; org-a extra: {refused} "
          f"refused (wait_full), {sum(1 for o in outcomes if o[0] == 'waiting')} waiting; request-queue depth={depth}")
    assert isinstance(b_admit, Admitted) and not isinstance(b_admit, ShedVerdict)
    assert b_slot.org == "org-b"
    assert refused == 15 - w and depth == 0


# ---------------------------------------------------------------- A6(d): cancel after grant does not leak
def test_cancel_after_grant_returns_the_slot() -> None:
    async def run() -> tuple[int, str]:
        slots = StreamSlots(_limits(slots=1, wait_per_org=2), wait_s=lambda: 5.0)
        x = await slots.acquire("x", "x0", 1)
        w = asyncio.ensure_future(slots.acquire("w", "w0", 1))
        z = asyncio.ensure_future(slots.acquire("z", "z0", 1))
        await asyncio.sleep(0)
        x.release()  # grants w0 (oldest of equal shares): its future is resolved, the task has not resumed
        w.cancel()  # the client leaves in that window
        with pytest.raises(asyncio.CancelledError):
            await w
        zs = await asyncio.wait_for(z, 1.0)  # the slot went back and on to z, not lost
        slots.policy.invariants()
        return slots.policy.free_slots, zs.org

    free, who = asyncio.run(run())
    assert (free, who) == (0, "z")


# ---------------------------------------------------------------- A6(d), B-S-1: one slot per request
def test_one_slot_held_across_fallback_attempts() -> None:
    """Rev 2 acquired per candidate: attempt 1's failure released the slot to a waiting org and attempt 2
    timed out as upstream_inflight_limited. Rev 3 acquires once; attempts run under the same slot."""

    async def per_candidate() -> str:
        slots = StreamSlots(_limits(slots=1), wait_s=lambda: 0.05)
        s1 = await slots.acquire("a", "a-att1", 1)
        other = asyncio.ensure_future(slots.acquire("b", "b0", 1))
        await asyncio.sleep(0)
        s1.release()  # attempt 1 failed pre-content
        try:
            await slots.acquire("a", "a-att2", 1)  # attempt 2 re-queues
            return "attempt 2 ran"
        except InflightLimited as exc:
            return f"attempt 2 refused: {exc.reason}"
        finally:
            (await other).release()

    async def per_request() -> str:
        slots = StreamSlots(_limits(slots=1), wait_s=lambda: 0.05)
        s = await slots.acquire("a", "a-req", 1)
        other = asyncio.ensure_future(slots.acquire("b", "b0", 1))
        await asyncio.sleep(0)
        attempts = ["att1 failed pre-content", "att2 ran under the same slot"]  # no release in between
        s.release()
        (await other).release()
        return attempts[-1]

    rev2, rev3 = asyncio.run(per_candidate()), asyncio.run(per_request())
    print(f"\nper-candidate (rev 2): {rev2}; per-request (rev 3): {rev3}")
    assert rev2.startswith("attempt 2 refused")
    assert rev3 == "att2 ran under the same slot"


# ---------------------------------------------------------------- Q9 + reserve, max-min, bytes
def test_idle_worker_serves_one_org_up_to_s_minus_reserve() -> None:
    p = SlotPolicy(_limits(slots=10, reserve=2, reserve_bytes=200))
    grants = [p.request("a", f"a{i}", 10) for i in range(12)]
    assert grants.count("grant") == 8 and grants[8] == "wait"  # 8 = S - R; one waiter (cap 1)
    assert p.request("b", "b0", 10) == "grant"  # a newcomer takes the reserve at once
    p.invariants()


def test_no_reserve_idle_worker_serves_one_org_fully() -> None:
    p = SlotPolicy(_limits(slots=10))
    assert [p.request("a", f"a{i}", 10) for i in range(10)].count("grant") == 10
    assert p.request("b", "b0", 10) == "wait"


def test_release_goes_to_smallest_dominant_share() -> None:
    p = SlotPolicy(_limits(slots=6, byte_budget=1_000, wait_per_org=2))
    for i in range(3):
        assert p.request("a", f"a{i}", 10) == "grant"
    for i in range(2):
        assert p.request("b", f"b{i}", 300) == "grant"  # b: 2/6 slots but 600/1000 bytes -> share 0.6
    assert p.request("c", "c0", 10) == "grant"  # 6 of 6 slots used
    assert p.request("b", "b9", 10) == "wait"  # b waits first (older)
    assert p.request("a", "a9", 10) == "wait"
    granted = p.release("c", 10)
    assert granted == [("a", "a9", 10)]  # a: share 0.5 < b: 0.6, although b's waiter is older
    p.invariants()


def test_byte_budget_blocks_until_bytes_free() -> None:
    p = SlotPolicy(_limits(slots=10, byte_budget=100))
    assert p.request("a", "a0", 60) == "grant"
    assert p.request("b", "b0", 50) == "wait"  # 1 slot free is not enough: 40 bytes free
    assert p.release("a", 60) == [("b", "b0", 50)]
    p.invariants()


def test_wait_bounds_per_org_and_total() -> None:
    p = SlotPolicy(_limits(slots=1, wait_total=3, wait_per_org=1))
    assert p.request("a", "a0", 1) == "grant"
    assert p.request("a", "a1", 1) == "wait"
    assert p.request("a", "a2", 1) == "refuse_wait_full"  # per-org cap
    assert p.request("b", "b0", 1) == "wait"
    assert p.request("c", "c0", 1) == "wait"
    assert p.request("d", "d0", 1) == "refuse_wait_full"  # total cap


def test_drain_refuses_waiters_and_new_requests() -> None:
    async def run():
        slots = StreamSlots(_limits(slots=1), wait_s=lambda: 5.0)
        s = await slots.acquire("a", "a0", 1)
        w = asyncio.ensure_future(slots.acquire("b", "b0", 1))
        await asyncio.sleep(0)
        n = slots.start_drain()
        with pytest.raises(InflightLimited) as e1:
            await w
        with pytest.raises(InflightLimited) as e2:
            await slots.acquire("c", "c0", 1)
        s.release()
        slots.policy.invariants()
        return n, e1.value.reason, e2.value.reason

    assert asyncio.run(run()) == (1, "refuse_draining", "refuse_draining")


def test_policy_invariants_random_10k() -> None:
    rng = random.Random(1213)
    for trial in range(10):
        lim = SlotLimits(slots=rng.randint(1, 12), byte_budget=rng.randint(50, 400),
                         wait_total=rng.randint(0, 8), wait_per_org=rng.randint(0, 3))
        if rng.random() < 0.5 and lim.slots > 1:
            lim = SlotLimits(lim.slots, lim.byte_budget, lim.wait_total, lim.wait_per_org,
                             reserve=rng.randint(0, lim.slots - 1), reserve_bytes=rng.randint(0, lim.byte_budget // 3))
        p = SlotPolicy(lim)
        held: list[tuple[str, str, int]] = []
        waiting: list[tuple[str, str]] = []
        for i in range(1000):  # 10 trials x 1,000 steps
            op = rng.random()
            org = f"o{rng.randint(0, 5)}"
            if op < 0.5:
                ch = rng.randint(1, lim.byte_budget // 4 + 1)
                v = p.request(org, f"t{trial}r{i}", ch)
                if v == "grant":
                    held.append((org, f"t{trial}r{i}", ch))
                elif v == "wait":
                    waiting.append((org, f"t{trial}r{i}"))
            elif op < 0.85 and held:
                o, r, ch = held.pop(rng.randrange(len(held)))
                for g in p.release(o, ch):
                    held.append(g)
                    waiting.remove((g[0], g[1]))
            elif waiting:
                o, r = waiting.pop(rng.randrange(len(waiting)))
                granted = p.cancel(o, r)
                assert granted is not None
                for g in granted:
                    held.append(g)
                    waiting.remove((g[0], g[1]))
            p.invariants()
