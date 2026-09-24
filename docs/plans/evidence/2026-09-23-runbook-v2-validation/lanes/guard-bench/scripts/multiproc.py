#!/usr/bin/env python3
"""Exp A (multi-process contention): N separate processes, each with its OWN ORT/TensorRT session on the same L4
-- what "in-process TensorRT with N uvicorn workers" means in practice -- with and without CUDA MPS.

closed : every process runs back-to-back W-window calls for --secs (synchronised start); aggregate windows/s and
         the merged per-call latency distribution.
open   : every process runs microbatch_bench.py (its own micro-batcher, Poisson arrivals at total_rate/N) on a
         common monotonic start time; raw per-request npz from all processes are merged per step by analyze_ladder.py.
"""
import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import pg2common as C  # noqa: E402

MPS_ENV = {"CUDA_MPS_PIPE_DIRECTORY": "/tmp/rv-mps-pipe", "CUDA_MPS_LOG_DIRECTORY": "/tmp/rv-mps-log"}


def mps(on):
    env = dict(os.environ, **MPS_ENV)
    if on:
        for d in MPS_ENV.values():
            os.makedirs(d, exist_ok=True)
        subprocess.run(["nvidia-cuda-mps-control", "-d"], env=env, check=True)
        time.sleep(2)
    else:
        subprocess.run("echo quit | nvidia-cuda-mps-control", shell=True, env=env)
        time.sleep(2)
    return env if on else dict(os.environ)


CLOSED_CHILD = r'''
import sys, time, json, numpy as np
sys.path.insert(0, "{here}")
import pg2common as C, ortsess
import onnxruntime as ort
try: ort.preload_dlls()
except Exception: pass
tok = C.load_tokenizer("{tok}")
ids = C.benign_filler_ids(tok, "{corpus}", {W} * 510, seed=5)
a, m = C.batch_from_windows([ids[i*510:(i+1)*510] for i in range({W})])
f = {{"input_ids": a, "attention_mask": m}}
sess, info = ortsess.make_session("{onnx}", "{backend}", W={W}, seq=512, opt_batch={ob}, max_batch=64)
for _ in range(50): sess.run(None, f)
while time.monotonic() < {t0}: time.sleep(0.0005)
lat = []
pc = time.perf_counter_ns
while time.monotonic() < {t1}:
    s = pc(); sess.run(None, f); lat.append(pc() - s)
json.dump({{"lat_ns": lat}}, open("{out}", "w"))
'''


def closed(args, env, outdir):
    t0 = time.monotonic() + 60
    t1 = t0 + args.secs
    procs, outs = [], []
    for j in range(args.N):
        o = outdir / f"closed_p{j}.json"
        outs.append(o)
        code = CLOSED_CHILD.format(here=HERE, tok=args.tok_dir, corpus=args.corpus, W=args.W, onnx=args.onnx_dir,
                                   backend=args.backend, ob=args.opt_batch, t0=t0, t1=t1, out=o)
        procs.append(subprocess.Popen([sys.executable, "-c", code], env=env, stderr=subprocess.DEVNULL))
    for p in procs:
        p.wait()
    lat = np.concatenate([np.asarray(json.load(open(o))["lat_ns"]) for o in outs]) / 1e6
    wps = len(lat) * args.W / args.secs
    res = {"N": args.N, "mps": args.mps, "W": args.W, "secs": args.secs, "calls": int(len(lat)),
           "aggregate_windows_per_s": round(wps, 1), "call_latency_ms": C.summarize_ms(lat)}
    print(json.dumps(res), flush=True)
    return res


def open_loop(args, env, outdir):
    rates = [float(x) for x in args.rates_total.split(",")]
    per = ",".join(f"{r / args.N:.3f}" for r in rates)
    start_at = time.monotonic() + 90
    procs = []
    for j in range(args.N):
        cmd = [sys.executable, str(HERE / "microbatch_bench.py"), "--onnx-dir", args.onnx_dir, "--tok-dir", args.tok_dir,
               "--corpus", args.corpus, "--backend", args.backend, "--opt-batch", str(args.opt_batch),
               "--W", str(args.W), "--B", str(args.B), "--wait-us", str(args.wait_us), "--rates", per,
               "--dur", str(args.dur), "--warm", str(args.warm), "--seed", str(100 + j), "--out-dir", str(outdir),
               "--tag", f"p{j}_", "--start-at", f"{start_at:.6f}"]
        procs.append(subprocess.Popen(cmd, env=env, stdout=open(outdir / f"p{j}.log", "w"), stderr=subprocess.STDOUT))
    for p in procs:
        p.wait()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["closed", "open"])
    ap.add_argument("--onnx-dir", required=True)
    ap.add_argument("--tok-dir", required=True)
    ap.add_argument("--corpus", required=True)
    ap.add_argument("--backend", default="trt_fp16_dyn")
    ap.add_argument("--opt-batch", type=int, default=16)
    ap.add_argument("--N", type=int, required=True)
    ap.add_argument("--mps", type=int, default=0)
    ap.add_argument("--W", type=int, default=2)
    ap.add_argument("--B", type=int, default=2)
    ap.add_argument("--wait-us", type=float, default=0)
    ap.add_argument("--rates-total", default="")
    ap.add_argument("--secs", type=float, default=20)
    ap.add_argument("--dur", type=float, default=10)
    ap.add_argument("--warm", type=float, default=3)
    ap.add_argument("--out-dir", required=True)
    args = ap.parse_args()
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    env = mps(True) if args.mps else dict(os.environ)
    try:
        if args.mode == "closed":
            r = closed(args, env, out)
            (out / "closed_summary.json").write_text(json.dumps(r, indent=1))
        else:
            open_loop(args, env, out)
    finally:
        if args.mps:
            mps(False)


if __name__ == "__main__":
    main()
