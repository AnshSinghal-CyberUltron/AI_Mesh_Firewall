#!/usr/bin/env python3
"""Exp A (per-call): PG2 latency on one L4 through ONNX Runtime, batch = W windows.

Timed region = sess.run() with host numpy inputs and host outputs, i.e. H2D + compute + D2H.
A second timed series ("devio") binds inputs/outputs on the GPU to show the transfer share.
Every raw sample (ns) is saved; summaries are recomputed from raw by analyze_latency.py.

usage: bench_latency_gpu.py --onnx-dir D --tok-dir T --corpus C --backend B --out OUT.json
       [--iters 3000 --warmup 300 --opt-batch 16 --cold] [--restart-probe W seq]
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pg2common as C  # noqa: E402
import ortsess  # noqa: E402

CONFIGS = [(1, 256), (1, 512), (2, 512), (3, 512), (4, 512), (7, 512)]


def gpu_state():
    try:
        return subprocess.check_output(
            ["nvidia-smi", "--query-gpu=clocks.sm,clocks.mem,temperature.gpu,power.draw,utilization.gpu,clocks_throttle_reasons.active",
             "--format=csv,noheader"], text=True, timeout=10).strip()
    except Exception as e:
        return repr(e)


def feeds_for(tok, corpus, W, seq):
    ids = C.benign_filler_ids(tok, corpus, W * (seq - 2), seed=W * 1000 + seq)
    wins = [ids[i * (seq - 2):(i + 1) * (seq - 2)] for i in range(W)]
    a, m = C.batch_from_windows(wins, seq=seq)
    return {"input_ids": a, "attention_mask": m}


def time_host(sess, feeds, warmup, iters):
    for _ in range(warmup):
        sess.run(None, feeds)
    out = np.empty(iters, dtype=np.int64)
    pc = time.perf_counter_ns
    for i in range(iters):
        t0 = pc()
        sess.run(None, feeds)
        out[i] = pc() - t0
    return out


def time_devio(sess, feeds, warmup, iters):
    import onnxruntime as ort
    io = sess.io_binding()
    for k, v in feeds.items():
        io.bind_ortvalue_input(k, ort.OrtValue.ortvalue_from_numpy(v, "cuda", 0))
    io.bind_output("logits", "cuda")
    for _ in range(warmup):
        sess.run_with_iobinding(io)
        io.synchronize_outputs()
    out = np.empty(iters, dtype=np.int64)
    pc = time.perf_counter_ns
    for i in range(iters):
        t0 = pc()
        sess.run_with_iobinding(io)
        io.synchronize_outputs()
        out[i] = pc() - t0
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--onnx-dir", required=True)
    ap.add_argument("--tok-dir", required=True)
    ap.add_argument("--corpus", required=True)
    ap.add_argument("--backend", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--iters", type=int, default=3000)
    ap.add_argument("--warmup", type=int, default=300)
    ap.add_argument("--opt-batch", type=int, default=16)
    ap.add_argument("--max-batch", type=int, default=64)
    ap.add_argument("--cold", action="store_true", help="wipe this backend's engine cache first")
    ap.add_argument("--restart-probe", nargs=2, type=int, metavar=("W", "SEQ"))
    ap.add_argument("--configs", default="")
    args = ap.parse_args()

    import onnxruntime as ort
    try:
        ort.preload_dlls()
    except Exception:
        pass
    tok = C.load_tokenizer(args.tok_dir)
    kw = {"opt_batch": args.opt_batch, "max_batch": args.max_batch}
    configs = CONFIGS if not args.configs else [tuple(int(x) for x in c.split("x")) for c in args.configs.split(",")]
    if args.backend == "trt_fp16_multi":  # its profiles pin seq to 512
        configs = [c for c in configs if c[1] == 512]

    if args.restart_probe:  # fresh process, engine cache already populated
        W, seq = args.restart_probe
        feeds = feeds_for(tok, args.corpus, W, seq)
        t0 = time.perf_counter()
        sess, info = ortsess.make_session(args.onnx_dir, args.backend, W=W, seq=seq, **kw)
        t1 = time.perf_counter()
        sess.run(None, feeds)
        t2 = time.perf_counter()
        res = {"backend": args.backend, "W": W, "seq": seq, "create_s": round(t1 - t0, 4),
               "first_run_s": round(t2 - t1, 4), "ready_s": round(t2 - t0, 4), "info": info}
        Path(args.out).write_text(json.dumps(res, indent=1))
        print(json.dumps({k: res[k] for k in ("backend", "W", "seq", "create_s", "first_run_s", "ready_s")}))
        return

    if args.cold and args.backend.startswith("trt"):
        root = Path.home() / "gb" / "trtcache" / Path(args.onnx_dir).name
        pat = {"trt_fp16_static": "static_*", "trt_fp16_multi": f"multi_max{args.max_batch}_*"}.get(
            args.backend, f"dyn_opt{args.opt_batch}_max{args.max_batch}_*")
        for d in root.glob(pat):
            shutil.rmtree(d)

    report = {"meta": {"backend": args.backend, "onnx_dir": args.onnx_dir, "iters": args.iters, "warmup": args.warmup,
                       "host": C.host_facts(), "ort": ort.__version__, "opt_batch": args.opt_batch,
                       "max_batch": args.max_batch, "timed_region": "sess.run(numpy in -> numpy out): H2D+compute+D2H"},
              "configs": []}
    sess = None
    for W, seq in configs:
        feeds = feeds_for(tok, args.corpus, W, seq)
        row = {"W": W, "seq": seq, "gpu_before": gpu_state()}
        if sess is None or args.backend == "trt_fp16_static":
            sess = None  # release the previous engine/context before building the next
            t0 = time.perf_counter()
            sess, info = ortsess.make_session(args.onnx_dir, args.backend, W=W, seq=seq, **kw)
            ortsess.assert_backend(sess, args.backend)
            t1 = time.perf_counter()
            sess.run(None, feeds)
            t2 = time.perf_counter()
            row.update({"session_create_s": round(t1 - t0, 4), "first_run_s": round(t2 - t1, 4),
                        "cold_ready_s": round(t2 - t0, 4), "session_info": info})
        host = time_host(sess, feeds, args.warmup, args.iters)
        row["host_ns"] = host.tolist()
        row["host_ms"] = C.summarize_ms(host / 1e6)
        try:
            dev = time_devio(sess, feeds, args.warmup // 2, min(args.iters, 2000))
            row["devio_ns"] = dev.tolist()
            row["devio_ms"] = C.summarize_ms(dev / 1e6)
        except Exception as e:
            row["devio_error"] = repr(e)[:500]
        row["gpu_after"] = gpu_state()
        ortsess.assert_backend(sess, args.backend)  # still on the requested EP after timing (no fallback)
        row["providers_after"] = sess.get_providers()
        out = sess.run(None, feeds)[0]
        row["finite"] = bool(np.all(np.isfinite(out)))
        report["configs"].append(row)
        print(f"{args.backend:16s} W={W} seq={seq}  host p50={row['host_ms']['p50']:.3f} p90={row['host_ms']['p90']:.3f} "
              f"p99={row['host_ms']['p99']:.3f} max={row['host_ms']['max']:.3f}  devio p50={row.get('devio_ms', {}).get('p50')}  "
              f"ready={row.get('cold_ready_s')}", flush=True)
    # graph placement: which EP ran which nodes (TRT subgraph appears as a fused TRTKernel node)
    try:
        W, seq = configs[1] if len(configs) > 1 else configs[0]
        report["placement"] = ortsess.placement(args.onnx_dir, args.backend, feeds_for(tok, args.corpus, W, seq), W=W, seq=seq, **kw)
    except Exception as e:
        report["placement_error"] = repr(e)[:800]
    Path(args.out).write_text(json.dumps(report))
    print("DONE", args.out)


if __name__ == "__main__":
    main()
