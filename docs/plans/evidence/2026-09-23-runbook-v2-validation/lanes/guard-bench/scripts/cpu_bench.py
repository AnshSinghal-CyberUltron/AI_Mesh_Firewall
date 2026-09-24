#!/usr/bin/env python3
"""Exp D: CPU-only ONNX Runtime (the runbook's local_onnx backend) on a C4 host.

latency    : one session, intra-op threads T, sequential calls of W windows x 512 (batch = W);
             up to --max-iters timed calls or --max-secs per config (min --min-iters); raw ns saved.
throughput : P processes x T threads (P*T = vCPUs), each running back-to-back W-window calls for --secs;
             aggregate windows/s and windows/s per vCPU.
Backends: cpu_fp32 / cpu_int8 (our export + ORT dynamic quantization) and, for 22M, cpu_thirdparty_{fp32,int8}
(the gravitee-io export the 159.2 ms figure was measured on).
"""
from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import os
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pg2common as C  # noqa: E402
import ortsess  # noqa: E402

pc = time.perf_counter_ns


def feeds(tok_dir, corpus, W, seq=512):
    tok = C.load_tokenizer(tok_dir)
    ids = C.benign_filler_ids(tok, corpus, W * (seq - 2), seed=W)
    a, m = C.batch_from_windows([ids[i * (seq - 2):(i + 1) * (seq - 2)] for i in range(W)], seq=seq)
    return {"input_ids": a, "attention_mask": m}


def latency(args):
    rows = []
    for backend in args.backends.split(","):
        for T in [int(x) for x in args.threads.split(",")]:
            sess, info = ortsess.make_session(args.onnx_dir, backend, threads=T)
            for W in [int(x) for x in args.ws.split(",")]:
                f = feeds(args.tok_dir, args.corpus, W)
                for _ in range(args.warmup):
                    sess.run(None, f)
                xs = []
                t_end = pc() + int(args.max_secs * 1e9)
                while len(xs) < args.max_iters and (pc() < t_end or len(xs) < args.min_iters):
                    t0 = pc()
                    sess.run(None, f)
                    xs.append(pc() - t0)
                xs = np.asarray(xs)
                s = C.summarize_ms(xs / 1e6)
                rows.append({"backend": backend, "threads": T, "W": W, "seq": 512, "latency_ms": s,
                             "windows_per_s_single_stream": round(W * len(xs) / (xs.sum() / 1e9), 2),
                             "model_path": info["model_path"], "raw_ns": xs.tolist()})
                print(f"{backend:22s} T={T:2d} W={W}  n={s['n']:4d} p50={s['p50']:9.2f} p99={s['p99']:9.2f} ms", flush=True)
            del sess
    Path(args.out).write_text(json.dumps({"host": C.host_facts(), "rows": rows}))


def _worker(args, backend, T, W, t_start, t_stop, q):
    sess, _ = ortsess.make_session(args.onnx_dir, backend, threads=T)
    f = feeds(args.tok_dir, args.corpus, W)
    for _ in range(3):
        sess.run(None, f)
    while time.monotonic() < t_start:
        time.sleep(0.001)
    n, lat = 0, []
    while time.monotonic() < t_stop:
        t0 = pc()
        sess.run(None, f)
        lat.append(pc() - t0)
        n += 1
    q.put({"calls": n, "lat_ns": lat})


def throughput(args):
    ncpu = os.cpu_count()
    rows = []
    for backend in args.backends.split(","):
        for W in [int(x) for x in args.ws.split(",")]:
            for T in [int(x) for x in args.threads.split(",")]:
                P = ncpu // T
                q = mp.Queue()
                t_start = time.monotonic() + 20
                t_stop = t_start + args.secs
                ps = [mp.Process(target=_worker, args=(args, backend, T, W, t_start, t_stop, q)) for _ in range(P)]
                for p in ps:
                    p.start()
                res = [q.get() for _ in ps]
                for p in ps:
                    p.join()
                calls = sum(r["calls"] for r in res)
                lat = np.concatenate([np.asarray(r["lat_ns"]) for r in res]) / 1e6
                wps = calls * W / args.secs
                rows.append({"backend": backend, "W": W, "procs": P, "threads": T, "secs": args.secs, "calls": calls,
                             "windows_per_s": round(wps, 2), "windows_per_s_per_vcpu": round(wps / ncpu, 3),
                             "call_latency_ms": C.summarize_ms(lat), "raw_ms": lat.round(3).tolist()})
                print(f"{backend:22s} W={W} {P}x{T}: {wps:8.2f} windows/s = {wps / ncpu:6.3f} /vCPU  "
                      f"call p50 {rows[-1]['call_latency_ms']['p50']:.1f} p99 {rows[-1]['call_latency_ms']['p99']:.1f} ms",
                      flush=True)
    Path(args.out).write_text(json.dumps({"host": C.host_facts(), "rows": rows}))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["latency", "throughput"])
    ap.add_argument("--onnx-dir", required=True)
    ap.add_argument("--tok-dir", required=True)
    ap.add_argument("--corpus", required=True)
    ap.add_argument("--backends", default="cpu_fp32,cpu_int8")
    ap.add_argument("--threads", default="1,2,4,8")
    ap.add_argument("--ws", default="1,2,3")
    ap.add_argument("--warmup", type=int, default=5)
    ap.add_argument("--max-iters", type=int, default=300)
    ap.add_argument("--min-iters", type=int, default=30)
    ap.add_argument("--max-secs", type=float, default=40)
    ap.add_argument("--secs", type=float, default=40)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    (latency if args.mode == "latency" else throughput)(args)


if __name__ == "__main__":
    main()
