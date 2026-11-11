"""Off-loop semantic stage prototype: event-loop isolation, deadline, cancellation, overload, memory.

  flock /home/claude/work/.heavy.lock nice -n 10 .f2-scratch/venv/bin/python -I offloop.py

The stage shape the plan proposes (NOT product code):
  * ONE InferenceSession per process (intra_op 1, no spinning), shared by a ThreadPoolExecutor of
    ``k`` workers (ORT releases the GIL inside ``run``; the session is thread-safe for ``run``);
  * an admission bound of ``q`` windows in flight: a request that does not fit is answered
    UNAVAILABLE("overloaded") at once, never queued without bound;
  * a per-request deadline (the remaining guard budget): on expiry the request is answered
    UNAVAILABLE("timeout") and the in-flight ORT run is cancelled with ``RunOptions.terminate``
    so the worker is freed, not left computing a result nobody reads.
Measured: loop lag (inline vs offloaded), terminate latency, overload behaviour, process-pool
alternative (start-up, RSS, per-call overhead), RSS per session.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import json
import os
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np
import onnxruntime as ort
import psutil
from f2common import EVIDENCE_DIR, ONNX_PATH, make_session

FULL = np.ones((1, 512), dtype=np.int64)


def rss_mb(pid: int | None = None) -> float:
    return psutil.Process(pid or os.getpid()).memory_info().rss / 2**20


def pct(xs: list[float], q: float) -> float:
    s = sorted(xs)
    return s[min(len(s) - 1, round(q * (len(s) - 1)))] if s else float("nan")


class Unavailable(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class GuardStage:
    def __init__(self, sess, workers: int, max_windows_in_flight: int) -> None:
        self.sess = sess
        self.pool = concurrent.futures.ThreadPoolExecutor(max_workers=max(1, workers),
                                                          thread_name_prefix="amf-guard")
        self.capacity = max_windows_in_flight
        self.in_flight = 0
        self.terminated = 0

    def _run(self, rows: np.ndarray, opts: ort.RunOptions) -> np.ndarray:
        return self.sess.run(["logits"], {"input_ids": rows, "attention_mask": rows * 0 + 1},
                             run_options=opts)[0]

    async def classify(self, rows: np.ndarray, budget_s: float) -> np.ndarray:
        n = rows.shape[0]
        if self.in_flight + n > self.capacity:
            raise Unavailable("overloaded")
        self.in_flight += n
        opts = ort.RunOptions()
        loop = asyncio.get_running_loop()
        fut = loop.run_in_executor(self.pool, self._run, rows, opts)
        try:
            return await asyncio.wait_for(asyncio.shield(fut), timeout=budget_s)
        except TimeoutError:
            opts.terminate = True  # cancel the ORT run (queued or running) so the worker is freed
            self.terminated += 1

            def _release(_f: concurrent.futures.Future) -> None:
                self.in_flight -= n
            fut.add_done_callback(lambda f: (f.exception(), _release(f)))
            raise Unavailable("timeout") from None
        finally:
            if fut.done() and not opts.terminate:
                self.in_flight -= n


async def heartbeat(stop: asyncio.Event, lags: list[float]) -> None:
    while not stop.is_set():
        t0 = time.perf_counter()
        await asyncio.sleep(0.001)
        lags.append((time.perf_counter() - t0 - 0.001) * 1000)


async def loop_lag(sess, mode: str, workers: int, requests: int) -> dict[str, object]:
    stage = GuardStage(sess, workers, max_windows_in_flight=10**6)
    lags: list[float] = []
    stop = asyncio.Event()
    hb = asyncio.create_task(heartbeat(stop, lags))
    await asyncio.sleep(0.05)
    lat: list[float] = []
    c0 = time.process_time()
    w0 = time.perf_counter()

    async def one() -> None:
        t0 = time.perf_counter()
        rows = np.ones((2, 512), dtype=np.int64)
        if mode == "inline":
            sess.run(["logits"], {"input_ids": rows, "attention_mask": rows})  # blocks the loop
        else:
            await stage.classify(rows, budget_s=60.0)
        lat.append((time.perf_counter() - t0) * 1000)

    await asyncio.gather(*(one() for _ in range(requests)))
    wall = time.perf_counter() - w0
    stop.set()
    await hb
    stage.pool.shutdown()
    return {"mode": mode, "workers": workers, "requests": requests, "windows_per_request": 2,
            "loop_lag_ms_p50": round(pct(lags, 0.5), 2), "loop_lag_ms_p99": round(pct(lags, 0.99), 2),
            "loop_lag_ms_max": round(max(lags), 2), "heartbeats": len(lags),
            "request_ms_p50": round(pct(lat, 0.5), 1), "request_ms_p99": round(pct(lat, 0.99), 1),
            "windows_per_s": round(2 * requests / wall, 2),
            "cpu_ms_per_window": round((time.process_time() - c0) * 1000 / (2 * requests), 2)}


async def tokenizer_gil() -> dict[str, object]:
    """Does tokenizing a long prompt on a worker thread stall the event loop (GIL held)?"""
    from f2common import load_tokenizer

    tok = load_tokenizer()
    rows = [json.loads(x)["text"] for x in (EVIDENCE_DIR.parents[3] / "tests/detection_corpus/"
                                            "benign.jsonl").read_text().splitlines() if x.strip()]
    text = (" ".join(rows) * 20)[:65536]
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    loop = asyncio.get_running_loop()
    out = {}
    for mode in ("idle", "executor", "batch_executor"):
        lags: list[float] = []
        stop = asyncio.Event()
        hb = asyncio.create_task(heartbeat(stop, lags))
        await asyncio.sleep(0.05)
        t0 = time.perf_counter()
        for _ in range(10):
            if mode == "executor":
                await loop.run_in_executor(pool, lambda: tok.encode(text, add_special_tokens=False))
            elif mode == "batch_executor":
                await loop.run_in_executor(
                    pool, lambda: tok.encode_batch([text], add_special_tokens=False))
            else:
                await asyncio.sleep(0.02)
        wall = time.perf_counter() - t0
        stop.set()
        await hb
        out[mode] = {"loop_lag_ms_p50": round(pct(lags, 0.5), 2),
                     "loop_lag_ms_p99": round(pct(lags, 0.99), 2),
                     "loop_lag_ms_max": round(max(lags), 2), "wall_s": round(wall, 3)}
    pool.shutdown()
    return {"text_bytes": len(text.encode()), "encodes": 10, **out}


def terminate_latency(sess) -> dict[str, object]:
    rows = np.ones((8, 512), dtype=np.int64)
    out = []
    for delay in (0.005, 0.02, 0.05):
        opts = ort.RunOptions()
        result: dict[str, object] = {}

        def work(o=opts, res=result) -> None:
            t0 = time.perf_counter()
            try:
                sess.run(["logits"], {"input_ids": rows, "attention_mask": rows}, run_options=o)
                res["outcome"] = "completed"
            except Exception as exc:  # noqa: BLE001
                res["outcome"] = type(exc).__name__ + ": " + str(exc)[:80]
            res["ran_ms"] = (time.perf_counter() - t0) * 1000

        th = threading.Thread(target=work)
        th.start()
        time.sleep(delay)
        t_set = time.perf_counter()
        opts.terminate = True
        th.join()
        out.append({"terminate_after_ms": delay * 1000, "outcome": result["outcome"],
                    "run_ms_total": round(result["ran_ms"], 1),
                    "stopped_ms_after_terminate": round((time.perf_counter() - t_set) * 1000, 1)})
    # the session is still usable after a terminated run
    sess.run(["logits"], {"input_ids": FULL, "attention_mask": FULL})
    return {"batch": "8x512", "cases": out, "session_usable_after": True}


async def overload(sess) -> dict[str, object]:
    stage = GuardStage(sess, workers=2, max_windows_in_flight=8)
    results: list[str] = []
    lat: list[float] = []

    async def one(i: int) -> None:
        await asyncio.sleep(0.002 * i)
        t0 = time.perf_counter()
        try:
            await stage.classify(np.ones((2, 512), dtype=np.int64), budget_s=0.25)
            results.append("ok")
            lat.append((time.perf_counter() - t0) * 1000)
        except Unavailable as exc:
            results.append(exc.reason)
            lat.append((time.perf_counter() - t0) * 1000)

    await asyncio.gather(*(one(i) for i in range(40)))
    await asyncio.sleep(1.0)
    stage.pool.shutdown(wait=True)
    counts = {k: results.count(k) for k in sorted(set(results))}
    return {"requests": 40, "windows_per_request": 2, "workers": 2, "max_windows_in_flight": 8,
            "budget_s": 0.25, "outcomes": counts, "terminated_runs": stage.terminated,
            "answer_ms_p50": round(pct(lat, 0.5), 1), "answer_ms_max": round(max(lat), 1),
            "in_flight_after_drain": stage.in_flight}


def _child_init() -> None:
    global CHILD_SESS
    CHILD_SESS = make_session(threads=1)


def _child_run(rows: list[list[int]]) -> tuple[list[list[float]], float, int]:
    ids = np.asarray(rows, dtype=np.int64)
    out = CHILD_SESS.run(["logits"], {"input_ids": ids, "attention_mask": np.ones_like(ids)})[0]
    return out.tolist(), rss_mb(), os.getpid()


def process_pool(sess) -> dict[str, object]:
    t0 = time.perf_counter()
    with concurrent.futures.ProcessPoolExecutor(max_workers=1, initializer=_child_init) as pool:
        rows = [[1] + [5] * 126 + [2]]
        _, child_rss, _pid = pool.submit(_child_run, rows).result()
        start_s = time.perf_counter() - t0
        lat_pp = []
        for _ in range(10):
            w0 = time.perf_counter()
            pool.submit(_child_run, rows).result()
            lat_pp.append((time.perf_counter() - w0) * 1000)
    lat_th = []
    row128 = np.ones((1, 128), dtype=np.int64)
    for _ in range(10):
        w0 = time.perf_counter()
        sess.run(["logits"], {"input_ids": row128, "attention_mask": row128})
        lat_th.append((time.perf_counter() - w0) * 1000)
    return {"start_incl_session_s": round(start_s, 2), "child_rss_mb": round(child_rss, 1),
            "process_pool_128tok_ms_p50": round(pct(lat_pp, 0.5), 1),
            "in_process_128tok_ms_p50": round(pct(lat_th, 0.5), 1),
            "ipc_overhead_ms_p50_estimate": round(pct(lat_pp, 0.5) - pct(lat_th, 0.5), 1)}


def memory() -> dict[str, float]:
    r0 = rss_mb()
    s1 = make_session(threads=1)
    s1.run(["logits"], {"input_ids": FULL, "attention_mask": FULL})
    r1 = rss_mb()
    s2 = make_session(threads=1)
    s2.run(["logits"], {"input_ids": FULL, "attention_mask": FULL})
    r2 = rss_mb()
    batch = np.ones((8, 512), dtype=np.int64)
    s1.run(["logits"], {"input_ids": batch, "attention_mask": batch})
    r3 = rss_mb()
    del s2
    return {"rss_mb_python_ort_imported": round(r0, 1),
            "rss_mb_first_session_warm_1x512": round(r1, 1),
            "rss_mb_second_session_warm_1x512": round(r2, 1),
            "rss_mb_after_8x512_batch": round(r3, 1),
            "mb_per_session": round(r2 - r1, 1)}


def main() -> int:
    report: dict[str, object] = {"loadavg_at_start": os.getloadavg(),
                                 "onnx": str(ONNX_PATH.name)}
    report["memory"] = memory()
    sess = make_session(threads=1)
    sess.run(["logits"], {"input_ids": FULL, "attention_mask": FULL})
    report["loop_lag"] = [
        asyncio.run(loop_lag(sess, "inline", 0, 6)),
        asyncio.run(loop_lag(sess, "executor", 1, 6)),
        asyncio.run(loop_lag(sess, "executor", 2, 6)),
    ]
    report["tokenizer_gil"] = asyncio.run(tokenizer_gil())
    report["terminate"] = terminate_latency(sess)
    report["overload"] = asyncio.run(overload(sess))
    report["process_pool"] = process_pool(sess)
    report["loadavg_at_end"] = os.getloadavg()
    (EVIDENCE_DIR / "results" / "offloop.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
