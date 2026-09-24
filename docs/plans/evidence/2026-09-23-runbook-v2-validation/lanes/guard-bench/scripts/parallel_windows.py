#!/usr/bin/env python3
"""How should the W windows of ONE request be executed on the GPU(s)? Closed loop, one request in flight.

  batch    : one sess.run of [W,512] on the exact-shape WxN static engine
  serial   : W back-to-back batch-1 calls on the 1x512 static engine (one thread)
  streams  : W threads, each with its own 1x512 session (own CUDA stream) on the SAME GPU, run concurrently
  gpus     : like streams but window i runs on GPU i % n_gpus (only if >1 GPU is visible)
Latency per request = start of first call -> all W windows' logits on host. >= --iters timed requests. Raw ns saved.
"""
import argparse
import json
import sys
import threading
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pg2common as C  # noqa: E402
import ortsess  # noqa: E402

pc = time.perf_counter_ns


def static_session(onnx_dir, W):
    """Exact-shape Wx512 engine (same cache as bench_latency_gpu.py)."""
    sess, _ = ortsess.make_session(onnx_dir, "trt_fp16_static", W=W, seq=512)
    ortsess.assert_backend(sess, "trt_fp16_static")
    return sess


def gpu_session(onnx_dir, device):
    """1x512 static TensorRT session pinned to a GPU (device_id), engine cache per device."""
    import onnxruntime as ort
    from pathlib import Path as P
    so = ort.SessionOptions()
    so.intra_op_num_threads = 1
    so.add_session_config_entry("session.intra_op.allow_spinning", "0")
    cdir = P.home() / "gb" / "trtcache" / P(onnx_dir).name / f"static_1x512_fp16_L3_dev{device}"
    cdir.mkdir(parents=True, exist_ok=True)
    shp = "input_ids:1x512,attention_mask:1x512"
    trt = {"device_id": device, "trt_fp16_enable": True, "trt_engine_cache_enable": True, "trt_engine_cache_path": str(cdir),
           "trt_timing_cache_enable": True, "trt_timing_cache_path": str(cdir), "trt_profile_min_shapes": shp,
           "trt_profile_opt_shapes": shp, "trt_profile_max_shapes": shp}
    s = ort.InferenceSession(str(P(onnx_dir) / "model.fp32.onnx"), so,
                             providers=[("TensorrtExecutionProvider", trt), ("CUDAExecutionProvider", {"device_id": device})])
    s.disable_fallback()
    assert s.get_providers()[0] == "TensorrtExecutionProvider"
    return s


class Pool:
    """W worker threads, each owning one session; run() fans one window to each and waits for all."""

    def __init__(self, sessions):
        self.sessions = sessions
        self.jobs = [None] * len(sessions)
        self.go = [threading.Event() for _ in sessions]
        self.done = [threading.Event() for _ in sessions]
        for i in range(len(sessions)):
            threading.Thread(target=self._loop, args=(i,), daemon=True).start()

    def _loop(self, i):
        while True:
            self.go[i].wait()
            self.go[i].clear()
            f = self.jobs[i]
            self.sessions[i].run(None, f)
            self.done[i].set()

    def run(self, feeds_list):
        for i, f in enumerate(feeds_list):
            self.jobs[i] = f
            self.done[i].clear()
            self.go[i].set()
        for i in range(len(feeds_list)):
            self.done[i].wait()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--onnx-dir", required=True)
    ap.add_argument("--tok-dir", required=True)
    ap.add_argument("--corpus", required=True)
    ap.add_argument("--Ws", default="2,3")
    ap.add_argument("--modes", default="batch,serial,streams")
    ap.add_argument("--iters", type=int, default=2000)
    ap.add_argument("--warmup", type=int, default=200)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    sys.setswitchinterval(0.0002)
    import onnxruntime as ort
    try:
        ort.preload_dlls()
    except Exception:
        pass
    import subprocess
    ngpu = len(subprocess.check_output(["nvidia-smi", "-L"], text=True).strip().splitlines())
    tok = C.load_tokenizer(args.tok_dir)
    ids = C.benign_filler_ids(tok, args.corpus, 8 * 510, seed=3)
    a, m = C.batch_from_windows([ids[i * 510:(i + 1) * 510] for i in range(8)])
    res = {"host": C.host_facts(), "n_gpus": ngpu, "rows": []}
    one = gpu_session(args.onnx_dir, 0)
    for W in [int(x) for x in args.Ws.split(",")]:
        wf = [{"input_ids": a[i:i + 1], "attention_mask": m[i:i + 1]} for i in range(W)]
        for mode in args.modes.split(","):
            if mode == "gpus" and ngpu < 2:
                continue
            if mode == "batch":
                s = static_session(args.onnx_dir, W)
                f = {"input_ids": a[:W], "attention_mask": m[:W]}
                call = lambda: s.run(None, f)  # noqa: E731
            elif mode == "serial":
                call = lambda: [one.run(None, x) for x in wf]  # noqa: E731
            elif mode == "streams":
                pool = Pool([one] + [gpu_session(args.onnx_dir, 0) for _ in range(W - 1)])
                call = lambda: pool.run(wf)  # noqa: E731
            else:
                pool = Pool([gpu_session(args.onnx_dir, i % ngpu) for i in range(W)])
                call = lambda: pool.run(wf)  # noqa: E731
            for _ in range(args.warmup):
                call()
            xs = np.empty(args.iters, np.int64)
            for i in range(args.iters):
                t0 = pc()
                call()
                xs[i] = pc() - t0
            s_ = C.summarize_ms(xs / 1e6)
            thr = W * args.iters / (xs.sum() / 1e9)
            res["rows"].append({"W": W, "mode": mode, "latency_ms": s_, "closed_loop_windows_per_s": round(thr, 1),
                                "raw_ns": xs.tolist()})
            print(f"W={W} {mode:8s} p50={s_['p50']:.3f} p90={s_['p90']:.3f} p99={s_['p99']:.3f} max={s_['max']:.3f}  "
                  f"-> {thr:.1f} windows/s (1 request in flight)", flush=True)
    Path(args.out).write_text(json.dumps(res))


if __name__ == "__main__":
    main()
