#!/usr/bin/env python3
"""Exp A (throughput): open-loop arrivals -> in-process cross-request micro-batcher -> one inference thread.

Model of a gateway worker process:
  generator thread : Poisson arrivals from a seeded schedule (open loop; lateness recorded, never waits on results)
  batcher thread   : takes WHOLE requests (W windows each) until B windows are queued or `wait` has elapsed since
                     the oldest queued request was enqueued (greedy when behind), concatenates them, runs ONE
                     sess.run (host arrays: H2D + compute + D2H), then completes the batch on the asyncio loop
  asyncio loop     : resumes each waiting request (t_done), as a uvicorn worker would
Latency = t_done - t_sched (scheduled arrival), so generator lateness is charged to the system, never hidden.

usage: microbatch_bench.py --onnx-dir D --tok-dir T --corpus C --backend trt_fp16_dyn --W 2 --B 16 --wait-us 1000
         --rates 200,400,... (windows/s, this process) --dur 10 --warm 3 --out-dir OUT [--seed 1] [--start-at NS]
"""
from __future__ import annotations

import argparse
import asyncio
import collections
import json
import os
import sys
import threading
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pg2common as C  # noqa: E402
import ortsess  # noqa: E402

pc = time.perf_counter_ns


class Req:
    __slots__ = ("i", "W", "ids", "mask", "fut")

    def __init__(self, i, W, ids, mask):
        self.i, self.W, self.ids, self.mask, self.fut = i, W, ids, mask, None


class Recorder:
    def __init__(self, n):
        self.t_sched = np.zeros(n, np.int64)
        self.t_submit = np.zeros(n, np.int64)
        self.t_bs = np.zeros(n, np.int64)
        self.t_be = np.zeros(n, np.int64)
        self.t_done = np.zeros(n, np.int64)
        self.bw = np.zeros(n, np.int32)
        self.br = np.zeros(n, np.int32)
        self.W = np.zeros(n, np.int16)


class MicroBatcher:
    def __init__(self, sess, B, wait_ns, loop, rec):
        self.sess, self.B, self.wait_ns, self.loop, self.rec = sess, B, wait_ns, loop, rec
        self.q = collections.deque()
        self.cv = threading.Condition(threading.Lock())
        self.stop = False
        self.batches = 0
        self.th = threading.Thread(target=self._run, name="gpu-infer", daemon=True)
        self.th.start()

    def submit(self, r):
        with self.cv:
            self.q.append(r)
            self.cv.notify()

    def _take(self):
        with self.cv:
            while not self.q and not self.stop:
                self.cv.wait()
            if not self.q:
                return None, 0
            deadline = self.rec.t_submit[self.q[0].i] + self.wait_ns
            batch, n = [], 0
            while True:
                while self.q and n + self.q[0].W <= self.B:
                    r = self.q.popleft()
                    batch.append(r)
                    n += r.W
                if not batch:  # a single request larger than B still runs alone
                    r = self.q.popleft()
                    batch.append(r)
                    n += r.W
                if n >= self.B or (self.q and n + self.q[0].W > self.B):
                    break
                rem = deadline - pc()
                if rem <= 0:
                    break
                self.cv.wait(rem / 1e9)
            return batch, n

    def _run(self):
        rec = self.rec
        while True:
            batch, n = self._take()
            if batch is None:
                return
            if len(batch) == 1:
                ids, mask = batch[0].ids, batch[0].mask
            else:
                ids = np.concatenate([r.ids for r in batch])
                mask = np.concatenate([r.mask for r in batch])
            t0 = pc()
            self.sess.run(None, {"input_ids": ids, "attention_mask": mask})
            t1 = pc()
            for r in batch:
                rec.t_bs[r.i], rec.t_be[r.i], rec.bw[r.i], rec.br[r.i] = t0, t1, n, len(batch)
            self.batches += 1
            self.loop.call_soon_threadsafe(_complete, batch)


def _complete(batch):
    for r in batch:
        r.fut.set_result(None)


def draw_w(rng, n, W, wmix):
    """Per-request window counts. wmix='headline': input tokens ~ U[100,1024] (HARNESS_SPEC §4), W = ceil(tok/510)."""
    if wmix == "headline":
        tok = rng.integers(100, 1025, n)
        return np.ceil(tok / 510).astype(np.int64)
    return np.full(n, W, np.int64)


def mean_w(W, wmix):
    if wmix == "headline":
        t = np.arange(100, 1025)
        return float(np.ceil(t / 510).mean())
    return float(W)


async def run_step(loop, batcher, rec, pool_ids, pool_mask, W, rate_wps, dur_s, warm_s, seed, start_at_ns=None,
                   wmix=""):
    """Offer Poisson arrivals at rate_wps/mean(W) requests/s for warm_s + dur_s. Returns index of first measured req."""
    rps = rate_wps / mean_w(W, wmix)
    rng = np.random.default_rng(seed)
    n_total = int((warm_s + dur_s) * rps * 1.3) + 64
    gaps = rng.exponential(1e9 / rps, n_total).astype(np.int64)
    offs = np.cumsum(gaps)
    offs = offs[offs < int((warm_s + dur_s) * 1e9)]
    n = len(offs)
    ws = draw_w(rng, n, W, wmix)
    starts = rng.integers(0, len(pool_ids) - 8, n)
    done_evt = asyncio.Event()
    pending = [n]

    async def waiter(r):
        await r.fut
        rec.t_done[r.i] = pc()
        pending[0] -= 1
        if pending[0] == 0:
            done_evt.set()

    reqs = []
    for i in range(n):
        s, w = starts[i], int(ws[i])
        r = Req(i, w, pool_ids[s:s + w], pool_mask[s:s + w])
        r.fut = loop.create_future()
        reqs.append(r)
        rec.W[i] = w
    for r in reqs:
        loop.create_task(waiter(r))
    await asyncio.sleep(0)

    t0 = start_at_ns if start_at_ns else pc() + 20_000_000
    rec.t_sched[:n] = t0 + offs

    def gen():
        sl = time.sleep
        for r in reqs:
            tgt = rec.t_sched[r.i]
            d = tgt - pc()
            if d > 0:
                sl(d / 1e9)
            rec.t_submit[r.i] = pc()
            batcher.submit(r)

    th = threading.Thread(target=gen, name="gen", daemon=True)
    th.start()
    try:
        await asyncio.wait_for(done_evt.wait(), timeout=warm_s + dur_s + 60)
    except asyncio.TimeoutError:
        pass
    th.join(timeout=5)
    first_measured = int(np.searchsorted(offs, int(warm_s * 1e9)))
    return n, first_measured


def summarize(rec, n, i0, rate_wps, W, dur_s):
    sl = slice(i0, n)
    ok = rec.t_done[sl] > 0
    lat = (rec.t_done[sl] - rec.t_sched[sl])[ok] / 1e6
    late = (rec.t_submit[sl] - rec.t_sched[sl]) / 1e6
    gpu = (rec.t_be[sl] - rec.t_bs[sl])[ok] / 1e6
    qw = (rec.t_bs[sl] - rec.t_submit[sl])[ok] / 1e6
    comp = int(ok.sum())
    span = (rec.t_done[sl][ok].max() - rec.t_sched[sl].min()) / 1e9 if comp else float("nan")
    wdone = int(rec.W[sl][ok].sum())
    return {
        "offered_wps": rate_wps, "offered_rps": round(comp and (n - i0) / max(1e-9, (rec.t_sched[sl].max() - rec.t_sched[sl].min()) / 1e9), 2),
        "W": W, "n": int(n - i0), "completed": comp,
        "achieved_wps": round(wdone / span, 1) if comp else 0.0,
        "lat_ms": C.summarize_ms(lat) if comp else None,
        "lateness_ms": C.summarize_ms(late),
        "drops_gt5ms": int((late > 5.0).sum()),
        "gpu_exec_ms": C.summarize_ms(gpu) if comp else None,
        "queue_wait_ms": C.summarize_ms(qw) if comp else None,
        "mean_batch_windows": round(float(rec.bw[sl][ok].mean()), 2) if comp else None,
        "mean_batch_reqs": round(float(rec.br[sl][ok].mean()), 2) if comp else None,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--onnx-dir", required=True)
    ap.add_argument("--tok-dir", required=True)
    ap.add_argument("--corpus", required=True)
    ap.add_argument("--backend", default="trt_fp16_dyn")
    ap.add_argument("--opt-batch", type=int, default=16)
    ap.add_argument("--max-batch", type=int, default=64)
    ap.add_argument("--W", type=int, default=2)
    ap.add_argument("--wmix", default="", help="'headline' = per-request W from input tokens ~U[100,1024]")
    ap.add_argument("--B", type=int, default=16)
    ap.add_argument("--wait-us", type=float, default=1000)
    ap.add_argument("--rates", required=True, help="comma list of offered windows/s for THIS process")
    ap.add_argument("--dur", type=float, default=10)
    ap.add_argument("--warm", type=float, default=3)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--tag", default="")
    ap.add_argument("--start-at", type=float, default=0, help="CLOCK_MONOTONIC seconds for step-0 start (multi-proc sync)")
    ap.add_argument("--stop-p99-ms", type=float, default=200)
    ap.add_argument("--min-reqs", type=int, default=2000, help="stretch low-rate steps to >= this many measured requests")
    ap.add_argument("--max-dur", type=float, default=60)
    ap.add_argument("--no-stop", action="store_true", help="run every listed step (knee confirmation)")
    args = ap.parse_args()
    sys.setswitchinterval(0.0002)

    import onnxruntime as ort
    try:
        ort.preload_dlls()
    except Exception:
        pass
    try:
        import uvloop
        loop = uvloop.new_event_loop()
        loop_kind = "uvloop"
    except ImportError:
        loop = asyncio.new_event_loop()
        loop_kind = "asyncio"
    asyncio.set_event_loop(loop)

    tok = C.load_tokenizer(args.tok_dir)
    pool = C.benign_filler_ids(tok, args.corpus, 256 * 510, seed=7)
    wins = [pool[i * 510:(i + 1) * 510] for i in range(256)]
    pool_ids, pool_mask = C.batch_from_windows(wins)

    sess, info = ortsess.make_session(args.onnx_dir, args.backend, W=args.B, seq=512,
                                      opt_batch=args.opt_batch, max_batch=args.max_batch)
    ortsess.assert_backend(sess, args.backend)
    for b in (1, 2, 3, 4, 7, 8, 16, 32, 64):
        if b <= args.max_batch:
            for _ in range(20):
                sess.run(None, {"input_ids": pool_ids[:b], "attention_mask": pool_mask[:b]})

    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    rates = [float(x) for x in args.rates.split(",")]
    meta = {"args": vars(args), "session": info, "loop": loop_kind, "host": C.host_facts(), "pid": os.getpid(),
            "clock": "time.perf_counter_ns (CLOCK_MONOTONIC)"}
    steps = []
    for k, rate in enumerate(rates):
        dur = args.dur
        if args.min_reqs and not args.start_at:  # low rates: stretch the step so p99 rests on >= min_reqs samples
            dur = min(args.max_dur, max(args.dur, args.min_reqs * mean_w(args.W, args.wmix) / rate))
        max_n = int((args.warm + dur) * rate / mean_w(args.W, args.wmix) * 1.3) + 64
        rec = Recorder(max_n)
        b = MicroBatcher(sess, args.B, int(args.wait_us * 1000), loop, rec)
        start_ns = None
        if args.start_at:
            start_ns = int((args.start_at + k * (args.warm + args.dur + 8)) * 1e9)
        n, i0 = loop.run_until_complete(run_step(loop, b, rec, pool_ids, pool_mask, args.W, rate, dur,
                                                 args.warm, args.seed * 1000 + k, start_ns, wmix=args.wmix))
        with b.cv:
            b.stop = True
            b.cv.notify_all()
        b.th.join(timeout=10)
        s = summarize(rec, n, i0, rate, args.W, dur)
        s["dur_s"] = dur
        s["batches"] = b.batches
        s["step"] = k
        steps.append(s)
        np.savez_compressed(out / f"raw_{args.tag}step{k:02d}.npz", t_sched=rec.t_sched[:n], t_submit=rec.t_submit[:n],
                            t_bs=rec.t_bs[:n], t_be=rec.t_be[:n], t_done=rec.t_done[:n], bw=rec.bw[:n], br=rec.br[:n],
                            W=rec.W[:n], i0=np.int64(i0), offered_wps=np.float64(rate))
        lat = s["lat_ms"] or {}
        print(f"[{args.tag}] step {k} offered {rate:.0f} w/s -> achieved {s['achieved_wps']:.0f} w/s  "
              f"p50 {lat.get('p50')} p99 {lat.get('p99')} max {lat.get('max')}  batch {s['mean_batch_windows']}  "
              f"late p99 {s['lateness_ms']['p99']} drops {s['drops_gt5ms']}", flush=True)
        if not args.start_at and not args.no_stop and (not lat or lat.get("p99", 1e9) > args.stop_p99_ms
                                  or s["achieved_wps"] < 0.85 * rate):
            break
    (out / f"summary_{args.tag}.json").write_text(json.dumps({"meta": meta, "steps": steps}, indent=1))
    print("DONE", flush=True)


if __name__ == "__main__":
    main()
