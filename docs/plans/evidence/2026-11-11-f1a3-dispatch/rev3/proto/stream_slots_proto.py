"""F1a-3 plan revision 3, §3.6: asyncio prototype of `StreamSlots` around the pure policy, plus the
admission hand-off that keeps a stream visible to the SIGTERM drain. Evidence for the plan, not product
code; `test_stream_slots_proto.py` drives it against develop's real `AdmissionController`.

Request lifecycle (plan §3.6.1):
  admit (F1a-1) -> pipeline -> StreamSlots.acquire(org, request_id, charge)       <- once per request
     acquire: admission.handoff(request_id)   releases the request-queue slot, KEEPS the drain record
              policy.request(...)              grant | wait (per-org and total wait bounds) | refuse
  -> every routed attempt runs under the one slot (GW28 fallback never re-queues)
  -> response ends -> Slot.release(): policy.release(...) then admission.finish_handed_off(request_id)
"""
from __future__ import annotations

import asyncio
from collections.abc import Callable

from slots_core import SlotLimits, SlotPolicy


class InflightLimited(Exception):
    """-> `upstream_inflight_limited` (429, retry-after: 1), pre-send, refunded."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class HandoffAdmission:
    """What plan §3.6.1 asks F1a-1's AdmissionController to add (amendment A6), built here from develop's
    public primitives: `complete` releases the request-queue slot (and pops `_in_flight[request_id]`),
    `register_stream` puts a drain record back under the same id, `finish_stream` marks it finished.
    Develop's `finish_stream` never prunes the record; the hand-off prunes it while ACCEPTING so the map
    stays bounded by live streams (while DRAINING the finished flag is what the drain reads)."""

    def __init__(self, controller) -> None:
        self.c = controller

    def handoff(self, request_id: str) -> None:
        self.c.complete(request_id)
        self.c.register_stream(request_id)

    def finish_handed_off(self, request_id: str) -> None:
        from gateway_v2.admit.admission import DrainState

        self.c.finish_stream(request_id)
        if self.c.state is DrainState.ACCEPTING:
            self.c._in_flight.pop(request_id, None)


class Slot:
    __slots__ = ("_owner", "_released", "charge", "org", "req")

    def __init__(self, owner: StreamSlots, org: str, req: str, charge: int) -> None:
        self._owner, self.org, self.req, self.charge, self._released = owner, org, req, charge, False

    def release(self) -> None:
        """Exactly once (RequestHolds.release calls it in its finally); later calls are no-ops."""
        if self._released:
            return
        self._released = True
        self._owner._release(self)


class StreamSlots:
    def __init__(self, limits: SlotLimits, admission: HandoffAdmission | None = None,
                 wait_s: Callable[[], float] = lambda: 2.0) -> None:
        self.policy = SlotPolicy(limits)
        self.admission = admission
        self.wait_s = wait_s
        self._futs: dict[str, asyncio.Future[None]] = {}

    async def acquire(self, org: str, req: str, charge: int) -> Slot:
        if self.admission is not None:
            self.admission.handoff(req)  # the request leaves the shared admission queue here
        try:
            verdict = self.policy.request(org, req, charge)
            if verdict == "grant":
                return Slot(self, org, req, charge)
            if verdict != "wait":
                raise InflightLimited(verdict)
            fut: asyncio.Future[None] = asyncio.get_running_loop().create_future()
            self._futs[req] = fut
            try:
                await asyncio.wait_for(asyncio.shield(fut), self.wait_s())
            except BaseException as exc:
                # timeout, client disconnect or kill. If the grant raced in (the future is resolved but
                # this task did not resume before it was cancelled), the slot is ours: give it back, or it
                # leaks -- the classic asyncio semaphore cancel-after-grant bug.
                self._futs.pop(req, None)
                if fut.done() and not fut.cancelled() and fut.exception() is None:
                    self._release(Slot(self, org, req, charge))
                else:
                    self._wake(self.policy.cancel(org, req) or [])
                    if not fut.done():
                        fut.cancel()
                if isinstance(exc, TimeoutError):  # upstream_slot_wait_s() expired: 429, pre-send
                    raise InflightLimited("wait_expired") from None
                raise
            self._futs.pop(req, None)
            if fut.exception() is not None:
                raise fut.exception()  # drain flush
            return Slot(self, org, req, charge)
        except BaseException:
            if self.admission is not None:
                self.admission.finish_handed_off(req)
            raise

    def _wake(self, granted: list[tuple[str, str, int]]) -> None:
        for _org, req, _charge in granted:
            fut = self._futs.get(req)
            if fut is not None and not fut.done():
                fut.set_result(None)

    def _release(self, slot: Slot) -> None:
        self._wake(self.policy.release(slot.org, slot.charge))
        if self.admission is not None:
            self.admission.finish_handed_off(slot.req)

    def start_drain(self) -> int:
        """SIGTERM: refuse new requests and every waiter (they retry on another worker); running streams
        keep their slots and stay in the drain's record until they end."""
        flushed = self.policy.flush_waiters()
        for _org, req in flushed:
            fut = self._futs.get(req)
            if fut is not None and not fut.done():
                fut.set_exception(InflightLimited("refuse_draining"))
        return len(flushed)
