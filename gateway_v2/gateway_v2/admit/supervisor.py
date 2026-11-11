"""Worker-crash isolation + respawn seam (R2-07 / GW19, task 11.1, Req 3.4 / 9).

In the prototype one worker crashing took the whole serving unit down. This module defines the
*logic* that isolates a crashed worker and respawns a replacement while peers keep admitting — the
part that is locally verifiable — and leaves the real OS ``fork``/respawn to the serving-entrypoint
card (a declared dependency, Req 3.4 / 9). The seam is injectable: the real respawn is a
``Callable[[], Worker]`` and defaults to a stub that merely records intent, so a test can drive a
fake worker that "crashes" by raising and assert isolation + respawn-called + peers-keep-admitting
without any real processes.

Isolation means a crashed worker's slots are removed from the shared accounting **without touching
peers** (Req 9.1 / 9.3): the controller consults ``active_slots`` to know how much concurrency is
really available, and a crash drops only the dead worker's contribution. Nothing here holds a
capacity literal — a worker's slot count is supplied by the caller (ultimately a
:class:`ResourceContract` call) — and there is no module-level mutable state.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

__all__ = (
    "Worker",
    "WorkerSupervisor",
)


@dataclass(slots=True)
class Worker:
    """A serving worker tracked by the supervisor.

    ``slots`` is the worker's contribution to the shared concurrency accounting (supplied by the
    caller, never a literal here). ``alive`` flips to ``False`` on isolation. ``generation`` counts
    how many times this worker id has been respawned, so a test can see a replacement was minted.
    """

    worker_id: str
    slots: int
    alive: bool = True
    generation: int = 0


@dataclass(frozen=True, slots=True)
class ExitOutcome:
    """What ``on_worker_exit`` did: the isolated worker id and whether a respawn was issued."""

    isolated: str
    respawned: bool
    replacement_generation: int


class WorkerSupervisor:
    """Isolate a crashed worker and respawn a replacement; peers keep admitting.

    The real process ``fork``/respawn is injected as ``respawn`` (``Callable[[], Worker]``); the
    default stub records intent only (real supervision is the serving-entrypoint card's job). The
    supervisor owns the registry of workers and the shared ``active_slots`` total; isolating a
    crashed worker removes only its slots (Req 9.1), respawn mints a replacement (Req 9.2), and the
    surviving workers' slots are untouched so peers keep admitting throughout (Req 9.3).
    """

    __slots__ = ("_respawn", "_workers")

    def __init__(
        self,
        workers: tuple[Worker, ...] = (),
        *,
        respawn: Callable[[str, int], Worker] | None = None,
    ) -> None:
        # Instance-level registry only -- no module-level mutable state.
        self._workers: dict[str, Worker] = {w.worker_id: w for w in workers}
        self._respawn = respawn if respawn is not None else self._default_respawn

    @staticmethod
    def _default_respawn(worker_id: str, slots: int) -> Worker:
        """Stub respawn: mint a fresh live worker recording the respawn intent.

        Real OS supervision is the serving-entrypoint card's responsibility; locally this records
        that a replacement was requested so the isolation/respawn logic is testable in-process.
        """
        return Worker(worker_id=worker_id, slots=slots, alive=True, generation=0)

    def register(self, worker: Worker) -> None:
        """Add a worker to the shared accounting."""
        self._workers[worker.worker_id] = worker

    @property
    def active_slots(self) -> int:
        """Total slots across *live* workers. A crashed-and-isolated worker contributes 0."""
        return sum(w.slots for w in self._workers.values() if w.alive)

    @property
    def live_worker_ids(self) -> tuple[str, ...]:
        """Ids of the workers still admitting (peers), sorted for a stable read."""
        return tuple(sorted(wid for wid, w in self._workers.items() if w.alive))

    def isolate(self, worker_id: str) -> int:
        """Isolate a crashed worker: drop its slots from the shared accounting (Req 9.1 / 9.3).

        Returns the slots removed. Peers are untouched — only the named worker is marked dead and
        its contribution to ``active_slots`` falls away. Isolating an unknown or already-dead
        worker removes nothing (idempotent).
        """
        worker = self._workers.get(worker_id)
        if worker is None or not worker.alive:
            return 0
        worker.alive = False
        return worker.slots

    def on_worker_exit(self, worker_id: str) -> ExitOutcome:
        """Isolate the crashed worker, then respawn a replacement (Req 9.1 / 9.2).

        The crashed worker is isolated first (its slots leave the shared accounting), then the
        injected respawn mints a replacement that re-enters the accounting with the same slot
        contribution and an incremented generation. Peers keep admitting the entire time because
        only the dead worker's slots were ever removed (Req 9.3).
        """
        prior = self._workers.get(worker_id)
        prior_slots = prior.slots if prior is not None else 0
        prior_generation = prior.generation if prior is not None else -1
        self.isolate(worker_id)
        replacement = self._respawn(worker_id, prior_slots)
        replacement.alive = True
        replacement.generation = prior_generation + 1
        self._workers[worker_id] = replacement
        return ExitOutcome(
            isolated=worker_id,
            respawned=True,
            replacement_generation=replacement.generation,
        )
