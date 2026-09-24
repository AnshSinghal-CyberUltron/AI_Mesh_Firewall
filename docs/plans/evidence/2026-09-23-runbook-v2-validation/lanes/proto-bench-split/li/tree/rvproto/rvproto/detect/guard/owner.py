"""Guard owner: ONE process per GPU owns the TensorRT session (guard-bench: N per-worker
sessions on one L4 fall from 466 to 186-270 windows/s). Workers send windows over a Unix socket
(owner on the same node) and/or TCP (a guard-only node serving remote gateway workers), as set by
RV_GUARD_OWNER_LISTEN; the owner runs them FIFO on its single inference thread (MicroBatcher,
B=1). Protocol and frame validation: wire.py.

The owner's queue is THE guard queue of its GPU, so it is bounded here: accepted, unfinished
padded tokens <= pool_size(GUARD) of the owner's own measured rate (work that drains within the
p99 target); beyond it a request is refused at once (SHED -> 503 + Retry-After at the worker)
instead of queueing into deadline expiry, which would turn overload into UNAVAILABLE findings.
A request larger than the whole bound is accepted only into an empty queue.

The TCP listener is unauthenticated: bind it to the VPC-internal address only
(deploy/start_guard_node.sh).
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import socket
import sys
import time
from pathlib import Path

from tokenizers import Tokenizer

from rvproto.detect.guard import wire
from rvproto.detect.guard.batcher import padded_cost
from rvproto.detect.guard.local_onnx import LocalOnnxBackend
from rvproto.domain.guard import Budget, GuardResult
from rvproto.runtime import contract as rc
from rvproto.runtime.config import Settings
from rvproto.runtime.metrics import Registry


def _vocab(s: Settings) -> int | None:
    return Tokenizer.from_file(s.guard_tokenizer).get_vocab_size() if s.guard_tokenizer else None


class GuardOwner:
    def __init__(self, s: Settings, index: int, *, gpu: bool, model_hash: str) -> None:
        self.s = s
        self.index = index
        self.path = wire.socket_path(s.guard_socket_dir, index)
        self.geo = wire.Geometry(s.max_windows, s.window_tokens, _vocab(s))
        if s.guard_owner_listen_tcp is not None and self.geo.vocab is None:
            raise ValueError("a TCP guard owner needs RV_GUARD_TOKENIZER (token ids are range-checked)")
        self.metrics = Registry(index)
        self.backend = LocalOnnxBackend(s, self.metrics, gpu=gpu, device_id=index if gpu else 0,
                                        model_hash=model_hash)
        self.clients = 0
        self.hello: dict[str, object] = {}
        self.cap_tokens = 0
        self.queued_tokens = 0

    async def serve(self) -> None:
        await self.backend.start()
        tokens_per_s = self.backend.tokens_per_s
        assert tokens_per_s is not None
        contract, _ = rc.load()
        self.cap_tokens = rc.derive(rc.with_guard(contract, tokens_per_s), self.s).guard_queue_tokens or 0
        self.hello = {"host": socket.gethostname(), "index": self.index, "pid": os.getpid(),
                      "tokens_per_s": tokens_per_s, "model_hash": self.backend.model_hash,
                      "provider": self.backend.providers[0]}
        servers, endpoints = [], []
        if self.s.guard_owner_listen_unix:  # bound only now: connectable == warm
            Path(self.path).unlink(missing_ok=True)
            servers.append(await asyncio.start_unix_server(self._client, path=self.path))
            os.chmod(self.path, 0o600)
            endpoints.append(f"unix:{self.path}")
        if self.s.guard_owner_listen_tcp is not None:
            host, port = self.s.guard_owner_listen_tcp
            servers.append(await asyncio.start_server(self._client, host, port + self.index))
            endpoints.append(f"{host}:{port + self.index}")
        ready = {**self.hello, "endpoints": endpoints, "queue_cap_tokens": self.cap_tokens}
        Path(self.s.guard_socket_dir).mkdir(mode=0o700, parents=True, exist_ok=True)
        tmp = Path(self.path + ".ready.tmp")
        tmp.write_text(json.dumps(ready))
        tmp.replace(self.path + ".ready")
        sys.stderr.write(json.dumps({"event": "guard_owner_ready", **ready, "detail": self.backend.detail}) + "\n")
        gauges = asyncio.create_task(self._gauges())
        try:
            await asyncio.gather(*(srv.serve_forever() for srv in servers))
        finally:
            gauges.cancel()

    async def _gauges(self) -> None:
        def dump() -> None:
            if self.s.metrics_dir:
                self.metrics.dump(self.s.metrics_dir, stem="owner")

        asyncio.get_running_loop().add_signal_handler(signal.SIGUSR1, dump)  # li experiment
        last = 0.0
        while True:
            m = self.metrics
            m.set("owner_clients", self.clients)
            m.set("owner_queued_tokens", self.queued_tokens)
            m.set("owner_queue_cap_tokens", self.cap_tokens)
            stats = getattr(self.backend, "queue_stats", None)
            if stats is not None:
                items, windows, age = stats()
                m.set("guard_queue_items", items)
                m.set("guard_queue_windows", windows)
                m.set("guard_queue_oldest_age_seconds", age)
            if self.backend.tokens_per_s is not None:
                m.set("guard_tokens_per_s", self.backend.tokens_per_s)
            if self.s.metrics_dump_s > 0 and time.monotonic() - last >= self.s.metrics_dump_s:
                dump()
                last = time.monotonic()
            await asyncio.sleep(1.0)

    async def _client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.clients += 1
        peer = writer.get_extra_info("peername")
        pending: set[asyncio.Task[None]] = set()
        inflight: dict[int, asyncio.Future[GuardResult]] = {}
        writer.write(wire.encode_response(0, GuardResult(True, (), 0, 0, 0, json.dumps(self.hello))))
        try:
            while True:
                (n_bytes,) = wire.U32.unpack(await reader.readexactly(4))
                if n_bytes > self.geo.max_request:
                    raise wire.FrameError(f"request frame of {n_bytes} bytes > {self.geo.max_request}")
                req_id, budget_ns, rows = wire.decode_request(await reader.readexactly(n_bytes), self.geo)
                if not rows:
                    gone = inflight.pop(req_id, None)
                    if gone is not None and gone.cancel():  # the MicroBatcher skips cancelled items
                        self.metrics.inc("owner_cancels")
                    continue
                deadline = time.perf_counter_ns() + budget_ns  # re-anchored on this host's clock
                self.metrics.inc("owner_requests")
                cost = padded_cost(rows, self.s.guard_seq_buckets)
                if self.queued_tokens and self.queued_tokens + cost > self.cap_tokens:
                    self.metrics.inc("owner_shed")
                    wait = self.queued_tokens / self.backend.tokens_per_s  # type: ignore[operator]
                    writer.write(wire.encode_response(req_id, GuardResult(
                        False, (), 0, 0, 0, "guard owner queue full", retry_after_s=wait)))
                    continue
                self.queued_tokens += cost
                self.metrics.inc("owner_windows", len(rows))
                fut = self.backend.submit(rows, Budget(deadline))  # FIFO into the ONE thread
                inflight[req_id] = fut
                fut.add_done_callback(lambda f, r=req_id, c=cost: self._finished(inflight, r, c))
                task = asyncio.ensure_future(self._reply(req_id, fut, writer))
                pending.add(task)
                task.add_done_callback(pending.discard)
        except wire.FrameError as exc:  # unauthenticated listener: drop the connection, never the engine
            self.metrics.inc("owner_bad_frames")
            sys.stderr.write(json.dumps({"event": "guard_owner_bad_frame", "peer": str(peer), "error": str(exc)}) + "\n")
        except (asyncio.IncompleteReadError, ConnectionError):
            pass  # the worker went away: its queued windows are cancelled below, never run
        finally:
            self.clients -= 1
            for t in pending:
                t.cancel()
            writer.close()

    def _finished(self, inflight: dict[int, asyncio.Future[GuardResult]], req_id: int, cost: int) -> None:
        inflight.pop(req_id, None)
        self.queued_tokens -= cost

    async def _reply(self, req_id: int, fut: asyncio.Future[GuardResult], writer: asyncio.StreamWriter) -> None:
        try:
            res = await fut
        except asyncio.CancelledError:
            return  # cancelled by the worker: nothing to report
        writer.write(wire.encode_response(req_id, res))


def run_owner(s: Settings, index: int, gpu: bool, model_hash: str) -> None:
    import uvloop

    uvloop.run(GuardOwner(s, index, gpu=gpu, model_hash=model_hash).serve())
