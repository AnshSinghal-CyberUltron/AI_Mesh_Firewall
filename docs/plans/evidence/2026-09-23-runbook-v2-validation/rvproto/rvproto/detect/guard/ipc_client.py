"""Worker side of the owner topologies: submit windows to guard owners over a Unix socket (owner
on this node) or TCP (remote guard nodes, split topology). One connection per owner; each request
goes to the connected owner with the fewest windows outstanding from this worker.

A request is answered by the owner that ran it, or resolved UNAVAILABLE (never a clean pass):
owner unreachable or dead, connection lost, or no answer within twice its budget (a silent peer is
also disconnected). Every result is checked (one probability in [0, 1] per window submitted).
Links reconnect in the background with bounded exponential backoff; each connection starts with
the owner's hello, whose model hash must equal the pinned one. The capacity hint is this worker's
share of the owners' measured rates: sum(owner tokens/s) / workers sharing those owners.
"""

from __future__ import annotations

import asyncio
import itertools
import json
import socket
import time
from collections.abc import Sequence
from dataclasses import dataclass

from rvproto.detect.guard import wire
from rvproto.domain.guard import Budget, GuardResult, Readiness
from rvproto.runtime.config import Settings
from rvproto.runtime.contract import CapacityHint
from rvproto.runtime.metrics import Registry

# owner-side outcomes that come back as not-ok, counted under the in-process batcher's names
_FAIL_COUNTERS = (("deadline", "guard_deadline_expired"), ("execution failed", "guard_exec_errors"))


def _unavail(why: str) -> GuardResult:
    return GuardResult(False, (), 0, 0, 0, why)


@dataclass(slots=True)
class _Pending:
    fut: asyncio.Future[GuardResult]
    t_submit: int
    windows: int
    expires: int


class _Link:
    """One connection to one owner."""

    def __init__(self, owner: OwnerClientBackend, ep: wire.Endpoint) -> None:
        self.o = owner
        self.ep = ep
        self.writer: asyncio.StreamWriter | None = None
        self.info: dict[str, object] | None = None  # the last hello
        self.pending: dict[int, _Pending] = {}
        self.windows = 0
        self.last_rx = 0
        self.error = "not connected yet"

    async def _open(self) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        if self.ep.kind == "unix":
            return await asyncio.open_unix_connection(self.ep.address)
        reader, writer = await asyncio.open_connection(self.ep.address, self.ep.port)
        sock = writer.get_extra_info("socket")
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        # an IDLE peer that vanished (VM gone, partition) is found by keepalive within the
        # staleness period: first probe after half of it, then two unanswered 1 s probes
        half = max(1, int(self.o.s.ks_stale_ms / 2000))
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPIDLE, half)
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, 1)
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPCNT, 2)
        return reader, writer

    async def _frame(self, reader: asyncio.StreamReader) -> tuple[int, GuardResult]:
        (n,) = wire.U32.unpack(await reader.readexactly(4))
        if n > self.o.geo.max_response:
            raise wire.FrameError(f"response frame of {n} bytes")
        return wire.decode_response(await reader.readexactly(n), self.o.geo)

    async def _hello(self, reader: asyncio.StreamReader) -> dict[str, object]:
        req_id, hello = await self._frame(reader)
        info = json.loads(hello.detail) if req_id == 0 else {}
        if info.get("model_hash") != self.o.model_hash:  # never serve another model as this one
            self.o.metrics.inc("guard_owner_model_mismatch")
            raise ConnectionRefusedError(f"owner model {info.get('model_hash')} != pinned {self.o.model_hash}")
        float(info["tokens_per_s"])  # a hello without a measured rate is not a warm owner
        return info

    async def run(self) -> None:
        s = self.o.s
        period = s.ks_refresh_ms / 1000.0
        delay = floor = period / 10
        while True:
            writer: asyncio.StreamWriter | None = None
            try:
                reader, writer = await asyncio.wait_for(self._open(), period)
                self.info = await asyncio.wait_for(self._hello(reader), period)
                self.writer, self.last_rx, delay = writer, time.perf_counter_ns(), floor
                self.o.metrics.inc("guard_owner_connects")
                self.o.refresh_detail()
                while True:
                    self._deliver(*await self._frame(reader))
            except (OSError, asyncio.IncompleteReadError, ValueError, KeyError, TypeError) as exc:
                if self.writer is not None:
                    self.o.metrics.inc("guard_owner_disconnects")
                self.writer = None
                self.error = f"{type(exc).__name__}: {exc}"[:200]
                self.fail_all(f"guard owner {self.ep} unreachable: {type(exc).__name__}")
                self.o.refresh_detail()
            if writer is not None:
                writer.close()
            await asyncio.sleep(delay)
            delay = min(delay * 2, period)  # bounded backoff: never slower than the refresh period

    def submit(self, req_id: int, fut: asyncio.Future[GuardResult], rows: Sequence[Sequence[int]],
               budget: Budget) -> None:
        assert self.writer is not None
        now = time.perf_counter_ns()
        remaining = budget.deadline_ns - now
        grace = max(remaining, int(self.o.s.ks_refresh_ms * 1e6 / 5))
        self.pending[req_id] = _Pending(fut, now, len(rows), budget.deadline_ns + grace)
        self.windows += len(rows)
        self.writer.write(wire.encode_request(req_id, remaining, [list(r) for r in rows]))

    def cancel(self, req_id: int) -> None:
        p = self.pending.pop(req_id, None)
        if p is not None:
            self.windows -= p.windows
            self.o.metrics.inc("guard_cancelled_skipped")
            if self.writer is not None:  # tell the owner to skip the queued windows
                self.writer.write(wire.encode_request(req_id, 0, []))

    def _deliver(self, req_id: int, res: GuardResult) -> None:
        self.last_rx = time.perf_counter_ns()
        p = self.pending.pop(req_id, None)
        if p is None:
            return  # cancelled or timed out here before the owner answered
        self.windows -= p.windows
        m = self.o.metrics
        m.observe("guard_owner_rtt_ns", self.last_rx - p.t_submit)
        if res.ok and (len(res.p_malicious) != p.windows or not all(0.0 <= x <= 1.0 for x in res.p_malicious)):
            m.inc("guard_owner_bad_results")
            res = _unavail(f"guard owner {self.ep} returned an invalid result")
        elif res.ok:
            m.observe("guard_queue_ns", res.queue_ns)
            m.observe("guard_exec_ns", res.exec_ns)
            m.inc("guard_windows", p.windows)
        elif res.retry_after_s is not None:
            m.inc("guard_owner_sheds")
        else:
            m.inc(next((c for key, c in _FAIL_COUNTERS if key in res.detail), "guard_unavailable_batches"))
        if not p.fut.done():
            p.fut.set_result(res)

    def sweep(self, now: int) -> None:
        """Answer overdue requests UNAVAILABLE. If the owner sent nothing at all since an overdue
        request was written, the connection is dead (half-open): drop it and reconnect."""
        silent = False
        for req_id in [r for r, p in self.pending.items() if p.expires <= now]:
            p = self.pending.pop(req_id)
            self.windows -= p.windows
            self.o.metrics.inc("guard_owner_timeouts")
            silent = silent or self.last_rx < p.t_submit
            if not p.fut.done():
                p.fut.set_result(_unavail(f"guard owner {self.ep}: no answer within twice the budget"))
        if silent and self.writer is not None:
            self.writer.close()  # the read loop sees EOF, fails the rest and reconnects

    def fail_all(self, why: str) -> None:
        pending, self.pending, self.windows = self.pending, {}, 0
        if pending:
            self.o.metrics.inc("guard_unavailable_batches", len(pending))
        for p in pending.values():
            if not p.fut.done():
                p.fut.set_result(_unavail(why))

    def describe(self) -> str:
        if self.writer is None or self.info is None:
            return f"{self.ep} DOWN ({self.error})"
        i = self.info
        return f"{self.ep} pid={i['pid']} host={i['host']} tokens_per_s={float(i['tokens_per_s']):.0f}"  # type: ignore[arg-type]


class OwnerClientBackend:
    def __init__(self, s: Settings, metrics: Registry, *, endpoints: Sequence[wire.Endpoint], sharing: int,
                 model_hash: str, name: str) -> None:
        self.name = name
        self.s = s
        self.metrics = metrics
        self.sharing = sharing
        self.model_hash = model_hash
        self.geo = wire.Geometry(s.max_windows, s.window_tokens)
        self.links = [_Link(self, ep) for ep in endpoints]
        self.tokens_per_s: float | None = None
        self.detail = "waiting for guard owners"
        self._ids = itertools.count(1)
        self._rr = itertools.count()
        self._killed = False
        self._tasks: list[asyncio.Task[None]] = []

    async def start(self) -> None:
        """Ready once every owner has said hello once (warm, pinned model) and one is connected."""
        self._tasks = [asyncio.create_task(link.run()) for link in self.links]
        self._tasks.append(asyncio.create_task(self._sweeper()))
        while any(link.info is None for link in self.links) or not self._live():
            await asyncio.sleep(self.s.ks_refresh_ms / 1000.0)
        self.tokens_per_s = sum(float(link.info["tokens_per_s"]) for link in self.links) / self.sharing  # type: ignore[arg-type,index]
        self.refresh_detail()

    async def _sweeper(self) -> None:
        period = self.s.ks_refresh_ms / 5000.0
        while True:
            await asyncio.sleep(period)
            now = time.perf_counter_ns()
            for link in self.links:
                link.sweep(now)

    def _live(self) -> list[_Link]:
        return [link for link in self.links if link.writer is not None]

    def refresh_detail(self) -> None:
        if not self._killed:
            share = f"share 1/{self.sharing}" if self.tokens_per_s is not None else "starting"
            self.detail = (f"owners {len(self._live())}/{len(self.links)} connected, {share}: "
                           + "; ".join(link.describe() for link in self.links))[:1000]

    def submit(self, rows: Sequence[Sequence[int]], budget: Budget) -> asyncio.Future[GuardResult]:
        fut: asyncio.Future[GuardResult] = asyncio.get_running_loop().create_future()
        live = self._live()
        if self._killed or not live:
            fut.set_result(_unavail(f"guard backend not ready: {self.detail}"[:300]))
            return fut
        k = next(self._rr) % len(live)  # ties rotate; otherwise the least outstanding windows
        link = min(live[k:] + live[:k], key=lambda x: x.windows)
        req_id = next(self._ids)
        link.submit(req_id, fut, rows, budget)
        fut.add_done_callback(lambda f, r=req_id, lk=link: lk.cancel(r) if f.cancelled() else None)
        return fut

    async def classify(self, rows: Sequence[Sequence[int]], budget: Budget) -> GuardResult:
        return await self.submit(rows, budget)

    def queue_stats(self) -> tuple[int, int, float]:
        """This worker's outstanding owner requests: (items, windows, oldest age seconds)."""
        pend = [p for link in self.links for p in link.pending.values()]
        if not pend:
            return 0, 0, 0.0
        oldest = min(p.t_submit for p in pend)
        return len(pend), sum(p.windows for p in pend), (time.perf_counter_ns() - oldest) / 1e9

    async def readiness(self) -> Readiness:
        ok = not self._killed and self.tokens_per_s is not None and bool(self._live())
        return Readiness(ok, self.name, self.model_hash, self.detail)

    def capacity_hint(self) -> CapacityHint | None:
        return None if self.tokens_per_s is None else CapacityHint(tokens_per_second=self.tokens_per_s)

    def kill(self, why: str) -> None:
        self._killed = True
        self.detail = f"killed: {why}"
        for link in self.links:
            link.fail_all(f"guard backend down: {why}")

    def revive(self) -> None:
        self._killed = False
        self.refresh_detail()

    async def close(self) -> None:
        for t in self._tasks:
            t.cancel()
        for link in self.links:
            if link.writer is not None:
                link.writer.close()
