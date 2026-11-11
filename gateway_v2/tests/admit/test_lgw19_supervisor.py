"""Worker-crash isolation + respawn tests (R2-07 / GW19), task 11.2.

Covers :class:`gateway_v2.admit.supervisor.WorkerSupervisor`:

* a crashed worker is **isolated** — its slots leave the shared accounting (Req 9.1);
* a replacement is **respawned** via the injected seam (Req 9.2);
* **peers keep admitting** throughout a crash/respawn — their slots are never touched (Req 9.3).

Drives a fake worker that "crashes" by raising, asserting the isolation/respawn logic in-process
(real OS supervision is the serving-entrypoint card's job). Test files are not under the
import-linter layer contract.
"""

from __future__ import annotations

from gateway_v2.admit.supervisor import Worker, WorkerSupervisor


def _supervisor() -> WorkerSupervisor:
    return WorkerSupervisor(
        (
            Worker(worker_id="w1", slots=4),
            Worker(worker_id="w2", slots=4),
            Worker(worker_id="w3", slots=4),
        ),
    )


def test_isolate_removes_only_the_crashed_workers_slots() -> None:
    """Isolating a crashed worker drops its slots; peers' slots are untouched (Req 9.1 / 9.3)."""
    sup = _supervisor()
    assert sup.active_slots == 12
    removed = sup.isolate("w2")
    assert removed == 4
    assert sup.active_slots == 8  # only w2's 4 slots left the accounting
    assert sup.live_worker_ids == ("w1", "w3")  # peers keep admitting


def test_isolate_is_idempotent_for_unknown_or_dead_workers() -> None:
    """Isolating an unknown or already-dead worker removes nothing."""
    sup = _supervisor()
    assert sup.isolate("ghost") == 0
    sup.isolate("w1")
    assert sup.isolate("w1") == 0  # already dead
    assert sup.active_slots == 8


def test_crash_then_respawn_restores_capacity_and_mints_a_replacement() -> None:
    """A crashing worker is isolated and a replacement respawned (Req 9.1 / 9.2)."""
    respawned: list[str] = []

    def respawn(worker_id: str, slots: int) -> Worker:
        respawned.append(worker_id)
        return Worker(worker_id=worker_id, slots=slots)

    sup = WorkerSupervisor(
        (Worker(worker_id="w1", slots=4), Worker(worker_id="w2", slots=4)),
        respawn=respawn,
    )

    # A fake worker crashes by raising; the supervisor isolates + respawns it.
    def crashing_worker() -> None:
        raise RuntimeError("worker w2 crashed")

    try:
        crashing_worker()
    except RuntimeError:
        outcome = sup.on_worker_exit("w2")

    assert outcome.isolated == "w2"
    assert outcome.respawned is True
    assert outcome.replacement_generation == 1  # a fresh generation was minted
    assert respawned == ["w2"]  # the injected respawn seam was called
    # Capacity is restored and all workers are live again.
    assert sup.active_slots == 8
    assert sup.live_worker_ids == ("w1", "w2")


def test_peers_keep_admitting_during_a_crash_and_respawn() -> None:
    """While one worker crashes/respawns, peers' slots remain available the whole time (Req 9.3)."""
    sup = _supervisor()
    # The instant w2 is isolated (crashed, not yet respawned) peers w1 + w3 still contribute.
    sup.isolate("w2")
    assert sup.active_slots == 8
    assert set(sup.live_worker_ids) == {"w1", "w3"}
    # Respawn w2; peers were never affected and now full capacity returns.
    outcome = sup.on_worker_exit("w2")
    assert outcome.respawned is True
    assert sup.active_slots == 12
    assert set(sup.live_worker_ids) == {"w1", "w2", "w3"}


def test_default_respawn_stub_records_intent() -> None:
    """With no injected respawn, the default stub mints a live replacement (records intent)."""
    sup = WorkerSupervisor((Worker(worker_id="w1", slots=2),))
    sup.isolate("w1")
    assert sup.active_slots == 0
    outcome = sup.on_worker_exit("w1")
    assert outcome.respawned is True
    assert sup.active_slots == 2  # replacement carries the same slot contribution
