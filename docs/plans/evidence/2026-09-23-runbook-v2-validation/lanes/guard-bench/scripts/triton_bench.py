#!/usr/bin/env python3
"""Exp B/C: Triton Inference Server (gRPC) latency per W and open-loop throughput ladder.

latency : closed loop, one request in flight, batch dim = W windows x 512, >= --iters timed calls
ladder  : Poisson arrivals from a generator thread (precise sleep, never waits on results) handed to an
          asyncio gRPC client; latency = t_done - t_sched. Same raw npz layout as microbatch_bench.py so the
          same analyzer recomputes percentiles. Triton's dynamic batcher does the cross-request batching.
The server-side dynamic-batching config is (re)loaded through the model-repository API before a ladder
(--max-batch / --queue-us / --instances) so every step names the exact batcher settings it measured.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import threading
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pg2common as C  # noqa: E402

pc = time.perf_counter_ns


def model_config(name, max_batch, queue_us, instances, preferred=None):
    cfg = {
        "name": name, "backend": "tensorrt", "max_batch_size": max_batch,
        "input": [{"name": "input_ids", "data_type": "TYPE_INT64", "dims": [512]},
                  {"name": "attention_mask", "data_type": "TYPE_INT64", "dims": [512]}],
        "output": [{"name": "logits", "data_type": "TYPE_FP32", "dims": [2]}],
        "instance_group": [{"count": instances, "kind": "KIND_GPU"}],
    }
    if queue_us is not None:
        db = {"max_queue_delay_microseconds": int(queue_us)}
        if preferred:
            db["preferred_batch_size"] = preferred
        cfg["dynamic_batching"] = db
    return cfg


def load_model(url, name, cfg):
    import tritonclient.grpc as g
    cl = g.InferenceServerClient(url)
    try:
        if cl.is_model_ready(name):
            cl.unload_model(name)
            for _ in range(100):
                if not cl.is_model_ready(name):
                    break
                time.sleep(0.1)
    except Exception:
        pass
    cl.load_model(name, config=json.dumps(cfg))
    got = cl.get_model_config(name, as_json=True)
    cl.close()
    return got


def pool_windows(tok_dir, corpus):
    tok = C.load_tokenizer(tok_dir)
    pool = C.benign_filler_ids(tok, corpus, 256 * 510, seed=7)
    return C.batch_from_windows([pool[i * 510:(i + 1) * 510] for i in range(256)])


def make_inputs(ids, mask, W, n=64, seed=0):
    import tritonclient.grpc as g
    rng = np.random.default_rng(seed)
    out = []
    for s in rng.integers(0, len(ids) - W, n):
        a = g.InferInput("input_ids", [W, 512], "INT64")
        a.set_data_from_numpy(ids[s:s + W])
        b = g.InferInput("attention_mask", [W, 512], "INT64")
        b.set_data_from_numpy(mask[s:s + W])
        out.append([a, b])
    return out


def run_latency(args, ids, mask):
    import tritonclient.grpc as g
    cl = g.InferenceServerClient(args.url)
    outreq = [g.InferRequestedOutput("logits")]
    res = {"url": args.url, "model": args.model, "rows": [], "host": C.host_facts()}
    for W in [int(x) for x in args.ws.split(",")]:
        ins = make_inputs(ids, mask, W)
        for i in range(args.warmup):
            cl.infer(args.model, ins[i % len(ins)], outputs=outreq)
        xs = np.empty(args.iters, np.int64)
        for i in range(args.iters):
            t0 = pc()
            r = cl.infer(args.model, ins[i % len(ins)], outputs=outreq)
            r.as_numpy("logits")
            xs[i] = pc() - t0
        s = C.summarize_ms(xs / 1e6)
        res["rows"].append({"W": W, "rtt_ms": s, "raw_ns": xs.tolist()})
        print(f"W={W} p50={s['p50']:.3f} p90={s['p90']:.3f} p99={s['p99']:.3f} max={s['max']:.3f}", flush=True)
    stats = cl.get_inference_statistics(args.model, as_json=True)
    res["server_stats"] = stats
    Path(args.out).write_text(json.dumps(res))


async def ladder_step(loop, client, inputs_pool, model, W, rate_wps, dur, warm, seed):
    import tritonclient.grpc as g
    rps = rate_wps / W
    rng = np.random.default_rng(seed)
    n_total = int((warm + dur) * rps * 1.3) + 64
    offs = np.cumsum(rng.exponential(1e9 / rps, n_total).astype(np.int64))
    offs = offs[offs < int((warm + dur) * 1e9)]
    n = len(offs)
    t_sched = np.zeros(n, np.int64)
    t_submit = np.zeros(n, np.int64)
    t_done = np.zeros(n, np.int64)
    err = np.zeros(n, np.int8)
    outreq = [g.InferRequestedOutput("logits")]
    left = [n]
    done_evt = asyncio.Event()

    async def one(i):
        t_submit[i] = pc()
        try:
            r = await client.infer(model, inputs_pool[i % len(inputs_pool)], outputs=outreq)
            r.as_numpy("logits")
        except Exception:
            err[i] = 1
        t_done[i] = pc()
        left[0] -= 1
        if left[0] == 0:
            done_evt.set()

    t0 = pc() + 20_000_000
    t_sched[:] = t0 + offs

    def gen():
        for i in range(n):
            d = t_sched[i] - pc()
            if d > 0:
                time.sleep(d / 1e9)
            asyncio.run_coroutine_threadsafe(one(i), loop)

    th = threading.Thread(target=gen, daemon=True)
    th.start()
    try:
        await asyncio.wait_for(done_evt.wait(), timeout=warm + dur + 60)
    except asyncio.TimeoutError:
        pass
    th.join(timeout=5)
    i0 = int(np.searchsorted(offs, int(warm * 1e9)))
    return n, i0, t_sched, t_submit, t_done, err


def run_ladder(args, ids, mask):
    import tritonclient.grpc.aio as ga
    cfg = model_config(args.model, args.max_batch, None if args.queue_us < 0 else args.queue_us, args.instances)
    if args.no_load:  # server was (re)started and configured on its own host; record what it runs
        import tritonclient.grpc as g0
        c0 = g0.InferenceServerClient(args.url)
        got = c0.get_model_config(args.model, as_json=True)
        c0.close()
    else:
        got = load_model(args.url, args.model, cfg)
    try:
        import uvloop
        loop = uvloop.new_event_loop()
    except ImportError:
        loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    client = ga.InferenceServerClient(args.url)
    W = args.W
    pool = make_inputs(ids, mask, W, n=64, seed=W)
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    # warm the server at every batch size the batcher can form
    import tritonclient.grpc as g
    sc = g.InferenceServerClient(args.url)
    for b in (1, 2, 4, 8, 16, 32, 64):
        if b <= args.max_batch:
            for _ in range(10):
                sc.infer(args.model, make_inputs(ids, mask, b, n=1)[0])
    steps = []
    for k, rate in enumerate(float(x) for x in args.rates.split(",")):
        # low rates: stretch the step so p99 rests on >= min_reqs measured requests (same rule as microbatch_bench)
        dur = min(args.max_dur, max(args.dur, args.min_reqs * W / rate)) if args.min_reqs else args.dur
        n, i0, ts, tsub, td, err = loop.run_until_complete(
            ladder_step(loop, client, pool, args.model, W, rate, dur, args.warm, args.seed * 1000 + k))
        sl = slice(i0, n)
        ok = (td[sl] > 0) & (err[sl] == 0)
        lat = (td[sl] - ts[sl])[ok] / 1e6
        late = (tsub[sl] - ts[sl]) / 1e6
        comp = int(ok.sum())
        span = (td[sl][ok].max() - ts[sl].min()) / 1e9 if comp else float("nan")
        s = {"step": k, "dur_s": dur, "offered_wps": rate, "offered_rps": round(rate / W, 2), "W": W, "n": int(n - i0),
             "completed": comp, "errors": int(err[sl].sum()),
             "achieved_wps": round(comp * W / span, 1) if comp else 0.0,
             "lat_ms": C.summarize_ms(lat) if comp else None, "lateness_ms": C.summarize_ms(late),
             "drops_gt5ms": int((late > 5.0).sum())}
        steps.append(s)
        np.savez_compressed(out / f"raw_step{k:02d}.npz", t_sched=ts, t_submit=tsub, t_done=td, err=err,
                            i0=np.int64(i0), offered_wps=np.float64(rate), W=np.int64(W))
        L = s["lat_ms"] or {}
        print(f"step {k} offered {rate:.0f} w/s -> {s['achieved_wps']:.0f} w/s  p50 {L.get('p50')} p99 {L.get('p99')} "
              f"max {L.get('max')} late p99 {s['lateness_ms']['p99']} drops {s['drops_gt5ms']} err {s['errors']}", flush=True)
        if not L or L.get("p99", 1e9) > args.stop_p99_ms or s["achieved_wps"] < 0.85 * rate or s["errors"]:
            break
    stats = sc.get_inference_statistics(args.model, as_json=True)
    (out / "summary.json").write_text(json.dumps({"args": vars(args), "model_config": got, "steps": steps,
                                                  "server_stats": stats, "host": C.host_facts()}, indent=1))
    print("DONE", flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["latency", "ladder", "load"])
    ap.add_argument("--url", default="localhost:8001")
    ap.add_argument("--model", required=True)
    ap.add_argument("--tok-dir", required=True)
    ap.add_argument("--corpus", required=True)
    ap.add_argument("--ws", default="1,2,3,4,7")
    ap.add_argument("--iters", type=int, default=3000)
    ap.add_argument("--warmup", type=int, default=300)
    ap.add_argument("--W", type=int, default=2)
    ap.add_argument("--rates", default="")
    ap.add_argument("--dur", type=float, default=10)
    ap.add_argument("--warm", type=float, default=3)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--max-batch", type=int, default=64)
    ap.add_argument("--queue-us", type=float, default=1000)
    ap.add_argument("--instances", type=int, default=1)
    ap.add_argument("--stop-p99-ms", type=float, default=200)
    ap.add_argument("--no-load", action="store_true", help="do not push a model config (server configured separately)")
    ap.add_argument("--min-reqs", type=int, default=2000)
    ap.add_argument("--max-dur", type=float, default=60)
    ap.add_argument("--out", default="")
    ap.add_argument("--out-dir", default="")
    args = ap.parse_args()
    ids, mask = pool_windows(args.tok_dir, args.corpus)
    if args.mode == "load":
        cfg = model_config(args.model, args.max_batch, None if args.queue_us < 0 else args.queue_us, args.instances)
        print(json.dumps(load_model(args.url, args.model, cfg)))
    elif args.mode == "latency":
        run_latency(args, ids, mask)
    else:
        run_ladder(args, ids, mask)


if __name__ == "__main__":
    main()
