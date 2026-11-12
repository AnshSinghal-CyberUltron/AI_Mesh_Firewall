"""F1a-3 plan revision 3, §3.6: prototype of the per-worker stream-slot POLICY (pure, clock-free).

Evidence for the plan, not product code. The same class drives the discrete-event fairness simulation
(`sim/stream_slots_rev3_sim.py`) and the asyncio wrapper (`stream_slots_proto.py`), so the rules the
plan states are the rules that were simulated and unit-tested.

Rules (plan §3.6.3):
  * A grant is a reservation of one slot and `charge` bytes. Capacity: `slots` (S) and `byte_budget` (B).
  * Reserve (owner question Q13): the last `reserve` slots and the last `reserve_bytes` bytes go only to an
    org that holds fewer than `reserve` slots ("small holder"). reserve = 0 disables it.
  * request(): grant at once if the org is eligible and no waiter is eligible (waiters first); otherwise
    wait, unless the org already has `wait_per_org` waiters or the list holds `wait_total` -> refuse.
    While draining every request is refused.
  * release(): return the reservation, then repeatedly grant the eligible waiter whose org has the
    smallest dominant share max(slots_held / S, bytes_held / B) (ties: oldest waiter). This is
    contention-gated: with no waiter nothing is shared, and an idle worker serves one org up to S - reserve.
  * cancel(): a waiter leaves (timeout or client gone).
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class SlotLimits:
    slots: int
    byte_budget: int
    wait_total: int
    wait_per_org: int
    reserve: int = 0
    reserve_bytes: int = 0

    def __post_init__(self) -> None:
        if self.slots < 1 or self.byte_budget < 1 or self.wait_total < 0 or self.wait_per_org < 0:
            raise ValueError("stream slot limits below minimum to serve")
        if not 0 <= self.reserve < self.slots or not 0 <= self.reserve_bytes < self.byte_budget:
            raise ValueError("reserve must leave capacity for a large holder")


@dataclass(slots=True)
class _Waiter:
    org: str
    req: str
    charge: int
    seq: int


@dataclass(slots=True)
class SlotPolicy:
    limits: SlotLimits
    free_slots: int = field(init=False)
    free_bytes: int = field(init=False)
    held_slots: dict[str, int] = field(default_factory=dict)
    held_bytes: dict[str, int] = field(default_factory=dict)
    waiters: dict[str, deque[_Waiter]] = field(default_factory=dict)
    n_waiting: int = 0
    draining: bool = False
    _seq: int = 0

    def __post_init__(self) -> None:
        self.free_slots = self.limits.slots
        self.free_bytes = self.limits.byte_budget

    # -------------------------------------------------------------------------------- eligibility
    def _fits(self, org: str, charge: int) -> bool:
        lim = self.limits
        if self.free_slots < 1 or self.free_bytes < charge:
            return False
        if self.held_slots.get(org, 0) < lim.reserve:  # small holder: may use the reserve
            return True
        return self.free_slots - 1 >= lim.reserve and self.free_bytes - charge >= lim.reserve_bytes

    def share(self, org: str) -> float:
        lim = self.limits
        return max(self.held_slots.get(org, 0) / lim.slots, self.held_bytes.get(org, 0) / lim.byte_budget)

    def _eligible_heads(self) -> list[_Waiter]:
        return [q[0] for q in self.waiters.values() if q and self._fits(q[0].org, q[0].charge)]

    # -------------------------------------------------------------------------------- transitions
    def _take(self, org: str, charge: int) -> None:
        self.free_slots -= 1
        self.free_bytes -= charge
        self.held_slots[org] = self.held_slots.get(org, 0) + 1
        self.held_bytes[org] = self.held_bytes.get(org, 0) + charge

    def request(self, org: str, req: str, charge: int) -> str:
        """Return "grant", "wait", "refuse_draining", "refuse_wait_full" or "refuse_too_large"."""
        if self.draining:
            return "refuse_draining"
        if charge > self.limits.byte_budget:  # never grantable; the boot check makes this unreachable
            return "refuse_too_large"
        if self._fits(org, charge) and not self._eligible_heads():
            self._take(org, charge)
            return "grant"
        q = self.waiters.get(org)
        if (q is not None and len(q) >= self.limits.wait_per_org) or self.n_waiting >= self.limits.wait_total:
            return "refuse_wait_full"
        if q is None:
            q = self.waiters[org] = deque()
        self._seq += 1
        q.append(_Waiter(org, req, charge, self._seq))
        self.n_waiting += 1
        return "wait"

    def release(self, org: str, charge: int) -> list[tuple[str, str, int]]:
        """Return a reservation; grant waiters. Returns [(org, req, charge)] granted, in order."""
        self.free_slots += 1
        self.free_bytes += charge
        left = self.held_slots[org] - 1
        if left:
            self.held_slots[org] = left
            self.held_bytes[org] -= charge
        else:
            del self.held_slots[org]
            del self.held_bytes[org]
        return self._grant_waiters()

    def _grant_waiters(self) -> list[tuple[str, str, int]]:
        granted = []
        while True:
            heads = self._eligible_heads()
            if not heads:
                return granted
            w = min(heads, key=lambda h: (self.share(h.org), h.seq))
            self._pop(w)
            self._take(w.org, w.charge)
            granted.append((w.org, w.req, w.charge))

    def _pop(self, w: _Waiter) -> None:
        q = self.waiters[w.org]
        q.popleft()
        if not q:
            del self.waiters[w.org]
        self.n_waiting -= 1

    def cancel(self, org: str, req: str) -> list[tuple[str, str, int]] | None:
        """Remove a waiter (timeout or disconnect). None if it is not waiting (already granted).

        Removing an org's head can expose a smaller next waiter of the same org that fits now (found by
        the 10k-step property test), so a cancel also runs the grant loop and returns its grants."""
        q = self.waiters.get(org)
        if not q:
            return None
        for i, w in enumerate(q):
            if w.req == req:
                del q[i]
                if not q:
                    del self.waiters[org]
                self.n_waiting -= 1
                return self._grant_waiters()
        return None

    def flush_waiters(self) -> list[tuple[str, str]]:
        """Drain start: refuse every waiter (they retry elsewhere). Returns [(org, req)]."""
        out = [(w.org, w.req) for q in self.waiters.values() for w in q]
        self.waiters.clear()
        self.n_waiting = 0
        self.draining = True
        return out

    def invariants(self) -> None:
        lim = self.limits
        assert 0 <= self.free_slots <= lim.slots, self.free_slots
        assert 0 <= self.free_bytes <= lim.byte_budget, self.free_bytes
        assert sum(self.held_slots.values()) == lim.slots - self.free_slots
        assert sum(self.held_bytes.values()) == lim.byte_budget - self.free_bytes
        assert self.n_waiting == sum(len(q) for q in self.waiters.values()) <= lim.wait_total
        assert all(len(q) <= lim.wait_per_org for q in self.waiters.values())
        # no waiter is eligible while capacity it could use sits free (work conservation)
        assert not self._eligible_heads(), "an eligible waiter was left waiting"
