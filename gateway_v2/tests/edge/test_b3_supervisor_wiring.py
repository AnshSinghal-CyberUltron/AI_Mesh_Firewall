# Feature: serving-entrypoint-gw-b3
# Validates: Requirements 2.4, 2.5, 2.6, 2.7
"""Worker-supervision wiring: isolate / respawn / peers-keep-admitting (task 4.2).

Example/unit tests for :func:`gateway_v2.edge.serve.build_worker_supervisor`, the real OS
worker-supervision ``respawn`` adapter wired into the shipped
:class:`~gateway_v2.admit.supervisor.WorkerSupervisor`. These exercise the B3 LOCAL,
accounting-only half (``respawn_os_worker=None`` — no real multiprocess) plus the real-callback /
fail-closed branches, driving a FAKE crash by calling ``supervisor.on_worker_exit(worker_id)``
directly (a worker whose OS process would have exited abnormally).

House idiom: plain ``assert`` example tests (NOT a property task); the serving bounds come from a
real :class:`~gateway_v2.runtime.resources.ResourceContract` built via ``from_signals`` (the same
fixture shape the other edge tests use), so no capacity literal lives in the test. No
``hypothesis``; no uvicorn (``build_worker_supervisor`` uses none).
"""

from __future__ import annotations

from gateway_v2.admit.supervisor import Worker, WorkerSupervisor
from gateway_v2.edge.serve import build_worker_supervisor
from gateway_v2.runtime.kinds import HardwareSignals
from gateway_v2.runtime.resources import ResourceContract, from_signals


def _contract() -> ResourceContract:
    """A serviceable contract (same ``from_signals`` fixture shape as the other edge tests)."""
    signals = HardwareSignals(
        cpu_quota=4.0,
        memory_limit=400 * 1024 * 1024 * 8,
        fd_limit=4096,
        cpu_source="test",
        mem_source="test",
        fd_source="test",
    )
    return from_signals(
        signals,
        target_p99_ms=20.0,
        utilization_cap=0.75,
        per_worker_rss=400 * 1024 * 1024,
    )


def _per_worker_slots(contract: ResourceContract) -> int:
    """The contract-derived per-worker in-flight depth used as the adapter's fallback slot count."""
    return contract.queue_depth(contract.offered_service_rate())


# --------------------------------------------------------------------------- #
# R2.4 — a crashed worker is isolated: active_slots drops by exactly its slots.
# --------------------------------------------------------------------------- #


def test_crashed_worker_isolated_drops_only_its_slots() -> None:
    """on_worker_exit drops the dead worker's slots from active_slots by exactly its contribution.

    The crash is faked (no real multiprocess); the accounting-only adapter
    (``respawn_os_worker=None``) reconciles the shared accounting in-process (R2.4).
    """
    contract = _contract()
    dead = Worker(worker_id="w-dead", slots=7)
    peer = Worker(worker_id="w-peer", slots=5)
    supervisor = build_worker_supervisor(contract=contract, workers=(dead, peer))

    before = supervisor.active_slots
    assert before == 7 + 5

    # Measure the isolation half directly: isolate() returns the slots removed and they leave
    # active_slots before any replacement is minted (the drop equals the dead worker's slots).
    isolate_only = WorkerSupervisor((Worker(worker_id="w-dead", slots=7),))
    removed = isolate_only.isolate("w-dead")
    assert removed == 7
    assert isolate_only.active_slots == 0

    # Through the real wiring: after on_worker_exit the net change over the WHOLE event is the
    # dead worker's slots leaving then (local half) being restored, so active_slots is unchanged
    # and the drop that occurred mid-event equals exactly the dead worker's 7 slots.
    supervisor.on_worker_exit("w-dead")
    assert supervisor.active_slots == before  # 7 dropped, 7 restored == drop was exactly 7


# --------------------------------------------------------------------------- #
# R2.7 — replacement re-enters with the crashed slot contribution + generation+1.
# --------------------------------------------------------------------------- #


def test_replacement_restores_slots_and_bumps_generation() -> None:
    """The local-half respawn mints a replacement with the crashed slots and generation+1 (R2.7)."""
    contract = _contract()
    dead = Worker(worker_id="w0", slots=9, generation=3)
    supervisor = build_worker_supervisor(contract=contract, workers=(dead,))

    before = supervisor.active_slots
    outcome = supervisor.on_worker_exit("w0")

    # active_slots restored to its pre-crash total (the replacement carries the crashed slots).
    assert supervisor.active_slots == before == 9
    # generation incremented by exactly one.
    assert outcome.replacement_generation == 4
    assert outcome.isolated == "w0"
    assert outcome.respawned is True
    assert supervisor.live_worker_ids == ("w0",)


# --------------------------------------------------------------------------- #
# R2.6 — peers keep admitting: only the dead worker id is affected.
# --------------------------------------------------------------------------- #


def test_peers_untouched_across_crash_event() -> None:
    """live_worker_ids/slots for the OTHER workers are unchanged across the crash (R2.6).

    Register >=2 workers, crash one, assert the peer ids and their slot contribution are untouched.
    """
    contract = _contract()
    dead = Worker(worker_id="w-dead", slots=7)
    peer_a = Worker(worker_id="w-a", slots=4)
    peer_b = Worker(worker_id="w-b", slots=6)
    supervisor = build_worker_supervisor(
        contract=contract, workers=(dead, peer_a, peer_b)
    )

    peers_before = supervisor.live_worker_ids
    assert peers_before == ("w-a", "w-b", "w-dead")

    supervisor.on_worker_exit("w-dead")

    # The peer ids are untouched (the dead id is respawned under the same id for the local half).
    assert supervisor.live_worker_ids == ("w-a", "w-b", "w-dead")
    # Peers' own slot contribution is unchanged — only the dead worker's slots ever moved.
    assert peer_a.slots == 4
    assert peer_b.slots == 6
    assert peer_a.alive is True
    assert peer_b.alive is True


# --------------------------------------------------------------------------- #
# R2.5 — the respawn callable is invoked with the crashed (id, slots).
# --------------------------------------------------------------------------- #


def test_respawn_os_worker_invoked_with_crashed_id() -> None:
    """A counting ``respawn_os_worker`` confirms it is called with the crashed worker id (R2.5).

    The adapter closes over the crashed worker's own slots and passes them through; wrapping a
    counting OS-respawn hook confirms the real respawn is requested for the right id, and the
    replacement carries the crashed slot contribution (R2.5/R2.7).
    """
    contract = _contract()
    dead = Worker(worker_id="w-crash", slots=8)
    calls: list[str] = []

    def counting_respawn(worker_id: str) -> None:
        calls.append(worker_id)

    supervisor = build_worker_supervisor(
        contract=contract, workers=(dead,), respawn_os_worker=counting_respawn
    )

    before = supervisor.active_slots
    supervisor.on_worker_exit("w-crash")

    # The OS-respawn hook was invoked exactly once, with the crashed worker's id.
    assert calls == ["w-crash"]
    # The replacement carries the crashed slots (active_slots restored), proving slots threaded in.
    assert supervisor.active_slots == before == 8


# --------------------------------------------------------------------------- #
# R2.4 fail-closed — a respawn that itself raises leaves the dead worker isolated.
# --------------------------------------------------------------------------- #


def test_respawn_raises_mints_dead_replacement_slots_stay_out() -> None:
    """When ``respawn_os_worker`` RAISES, the adapter mints a replacement NOT alive (fail-closed).

    The B3 adapter is the component that owns the fail-closed decision: on an OS-respawn failure it
    returns a replacement :class:`~gateway_v2.admit.supervisor.Worker` with ``alive=False`` so its
    slots stay OUT of ``active_slots`` rather than double-counting capacity a worker that was never
    actually respawned cannot serve. Drive the injected adapter directly with the crashed
    ``(id, slots)`` — the same call the supervisor makes inside ``on_worker_exit``.
    """
    contract = _contract()
    dead = Worker(worker_id="w-x", slots=11)
    peer = Worker(worker_id="w-ok", slots=3)

    def raising_respawn(worker_id: str) -> None:
        raise RuntimeError(f"OS respawn of {worker_id} failed")

    supervisor = build_worker_supervisor(
        contract=contract, workers=(dead, peer), respawn_os_worker=raising_respawn
    )

    # The injected adapter is the fail-closed authority: invoked with the crashed (id, slots) it
    # mints a replacement that is NOT alive, so the dead worker's slots stay isolated.
    replacement = supervisor._respawn("w-x", 11)
    assert replacement.alive is False
    assert replacement.slots == 11

    # A supervisor whose shared accounting holds that dead replacement keeps the slots OUT of
    # active_slots (only a live peer contributes), so there is no double-counting (R2.4).
    isolated = WorkerSupervisor((replacement, Worker(worker_id="w-ok", slots=3)))
    assert isolated.active_slots == 3
    assert isolated.live_worker_ids == ("w-ok",)


# --------------------------------------------------------------------------- #
# R6.6 — contract-derived slot fallback when the crashed slots are unknown (0).
# --------------------------------------------------------------------------- #


def test_unknown_crashed_slots_fall_back_to_contract_depth() -> None:
    """When the crashed slots are unknown (0), the replacement uses the contract-derived depth.

    No literal: the fallback is ``contract.queue_depth(contract.offered_service_rate())``. Construct
    a Worker with ``slots=0`` to exercise this path.
    """
    contract = _contract()
    expected_depth = _per_worker_slots(contract)
    assert expected_depth > 0  # the fixture derives a real positive per-worker depth

    dead = Worker(worker_id="w-unknown", slots=0)
    supervisor = build_worker_supervisor(contract=contract, workers=(dead,))

    assert supervisor.active_slots == 0  # a slots=0 worker contributes nothing before the crash
    supervisor.on_worker_exit("w-unknown")

    # The replacement re-enters with the contract-derived per-worker depth, not a bare literal.
    assert supervisor.active_slots == expected_depth
