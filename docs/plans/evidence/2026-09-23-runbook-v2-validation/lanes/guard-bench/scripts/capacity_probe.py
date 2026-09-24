#!/usr/bin/env python3
"""Closed-loop batch sweep: back-to-back sess.run at a fixed batch of b windows x 512 for --secs seconds.

Gives the GPU's service time per batch size and the saturation windows/s = b / service_time
(an upper bound; open-loop capacity at a latency target comes from microbatch_bench.py).
"""
import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pg2common as C  # noqa: E402
import ortsess  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--onnx-dir", required=True)
    ap.add_argument("--tok-dir", required=True)
    ap.add_argument("--corpus", required=True)
    ap.add_argument("--backend", default="trt_fp16_dyn")
    ap.add_argument("--opt-batch", type=int, default=16)
    ap.add_argument("--max-batch", type=int, default=64)
    ap.add_argument("--batches", default="1,2,3,4,7,8,16,24,32,48,64")
    ap.add_argument("--secs", type=float, default=5)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    import onnxruntime as ort
    try:
        ort.preload_dlls()
    except Exception:
        pass
    tok = C.load_tokenizer(args.tok_dir)
    pool = C.benign_filler_ids(tok, args.corpus, 64 * 510, seed=11)
    ids, mask = C.batch_from_windows([pool[i * 510:(i + 1) * 510] for i in range(64)])
    sess, info = ortsess.make_session(args.onnx_dir, args.backend, W=1, seq=512, opt_batch=args.opt_batch,
                                      max_batch=args.max_batch)
    ortsess.assert_backend(sess, args.backend)
    rows = []
    pc = time.perf_counter_ns
    for b in [int(x) for x in args.batches.split(",")]:
        if b > args.max_batch:
            continue
        f = {"input_ids": ids[:b], "attention_mask": mask[:b]}
        for _ in range(50):
            sess.run(None, f)
        xs = []
        t_end = pc() + int(args.secs * 1e9)
        while pc() < t_end:
            t0 = pc()
            sess.run(None, f)
            xs.append(pc() - t0)
        xs = np.asarray(xs)
        s = C.summarize_ms(xs / 1e6)
        wps = b * len(xs) / (xs.sum() / 1e9)
        rows.append({"batch": b, "calls": len(xs), "service_ms": s, "windows_per_s": round(wps, 1),
                     "raw_ns": xs.tolist()})
        print(f"b={b:3d} p50={s['p50']:.3f} ms p99={s['p99']:.3f} ms  -> {wps:8.1f} windows/s", flush=True)
    Path(args.out).write_text(json.dumps({"session": info, "host": C.host_facts(), "rows": rows}))


if __name__ == "__main__":
    main()
