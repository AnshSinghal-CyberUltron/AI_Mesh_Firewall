#!/usr/bin/env python3
"""claim2a_postprocess.py -- reads records_pub_<cell>.jsonl written by claim2a_emit_count.py and writes
thread_breakdown.json: per cell x scenario (measured requests only), RedisLogPublisher.emit() calls per
request split by the thread that executed them. emit() (and its synchronous PUBLISH) runs in the calling
thread, so only emits on the event-loop thread (MainThread here: asyncio + ASGITransport run on it) can
stall the event loop; emits from executor threads stall that worker thread instead.

    cd <this dir> && python3 claim2a_postprocess.py
"""
import glob
import json
import os
import statistics
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))


def _st(xs):
    return {"min": min(xs), "median": statistics.median(xs), "max": max(xs)} if xs else {}


def main():
    out = {}
    for fp in sorted(glob.glob(os.path.join(HERE, "records_pub_*.jsonl"))):
        cell = os.path.basename(fp)[len("records_pub_"):-len(".jsonl")]
        per_req = defaultdict(lambda: Counter())
        threads = Counter()
        keys = set()
        run = json.load(open(os.path.join(HERE, f"run_{cell}.json")))
        for o in run["outcomes"]:
            if o["req_phase"] == "measured":
                keys.add((o["scenario"], o["req_idx"]))
        for line in open(fp):
            r = json.loads(line)
            if r["attr"] == "unattributed" or r["req_phase"] != "measured":
                continue
            loop = r["thread"] == "MainThread"
            per_req[(r["scenario"], r["req_idx"])]["loop" if loop else "worker"] += 1
            if not loop:
                threads[r["thread"].rsplit("_", 1)[0]] += 1
        cell_out = {}
        for sc in sorted({k[0] for k in keys}):
            idx = sorted(k[1] for k in keys if k[0] == sc)
            cell_out[sc] = {"event_loop_thread_emits_per_request": _st([per_req[(sc, i)]["loop"] for i in idx]),
                            "worker_thread_emits_per_request": _st([per_req[(sc, i)]["worker"] for i in idx])}
        cell_out["worker_thread_name_prefixes"] = dict(threads)
        out[cell] = cell_out
    with open(os.path.join(HERE, "thread_breakdown.json"), "w") as f:
        json.dump(out, f, indent=1)
    for cell, d in out.items():
        for sc, v in d.items():
            if sc == "worker_thread_name_prefixes":
                continue
            print(f"{cell:16} {sc:22} loop={v['event_loop_thread_emits_per_request']} "
                  f"worker={v['worker_thread_emits_per_request']}")
        print(f"{cell:16} worker thread prefixes: {d['worker_thread_name_prefixes']}")


if __name__ == "__main__":
    main()
