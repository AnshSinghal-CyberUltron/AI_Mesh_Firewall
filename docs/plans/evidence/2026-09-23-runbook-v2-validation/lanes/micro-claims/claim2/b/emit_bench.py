#!/usr/bin/env python3
"""Claim 2(b): wall time of ONE RedisLogPublisher emit, measured on the calling thread.

Uses the BASELINE v1 handler verbatim (v1shared/ai_mesh_shared/redis_log_handler.py, sha256-identical to
baseline-2a657fad/shared/...), redis-py 8.0.0 (no hiredis, as in gateway/uv.lock), against a real
redis:7.4-alpine on a separate VM. Replays the exact log payloads captured from the real v1 request path
in claim2/a (logger name, level and message of each record), so payload sizes are the real ones.

Each sample = perf_counter_ns() around handler.handle(record) (lock + emit: format, json.dumps, PUBLISH
round trip) -- i.e. exactly the time the event-loop thread is blocked by one log line.
Also records: Python-level Redis PING RTT (same client, same socket) and a no-network control
(publish replaced by a no-op) that isolates the CPU part of emit().

usage: emit_bench.py --redis redis://HOST:6379/0 --payloads FILE --n N --label L --out OUT.jsonl
"""
import argparse, json, logging, os, sys, time
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "v1shared"))
from ai_mesh_shared.redis_log_handler import RedisLogPublisher  # baseline code, unmodified

ap = argparse.ArgumentParser()
ap.add_argument("--redis", required=True)
ap.add_argument("--payloads", required=True)
ap.add_argument("--n", type=int, default=20000)
ap.add_argument("--warm", type=int, default=500)
ap.add_argument("--label", required=True)
ap.add_argument("--out", required=True)
ap.add_argument("--mode", choices=["publish", "noop"], default="publish")
a = ap.parse_args()

recs = []
for line in open(a.payloads):
    p = json.loads(line)
    entry = json.loads(p["payload"])
    recs.append((p["logger"], getattr(logging, p["levelname"]), entry["message"]))

h = RedisLogPublisher(redis_url=a.redis, service_name="Gateway")
h.setFormatter(logging.Formatter("%(message)s"))
if a.mode == "noop":
    class _Nop:
        def publish(self, *_a, **_k): return 0
    h._client = _Nop()

def one(i):
    name, lvl, msg = recs[i % len(recs)]
    rec = logging.LogRecord(name, lvl, "main.py", 1, msg, None, None)
    t0 = time.perf_counter_ns(); h.handle(rec); return time.perf_counter_ns() - t0

for i in range(a.warm):
    one(i)
# PING RTT through the very same redis-py client/socket (skipped in noop mode)
pings = []
if a.mode == "publish":
    c = h._get_client()
    for _ in range(2000):
        t0 = time.perf_counter_ns(); c.ping(); pings.append(time.perf_counter_ns() - t0)
samples = [one(i) for i in range(a.n)]
with open(a.out, "w") as f:
    f.write(json.dumps({"label": a.label, "mode": a.mode, "redis": a.redis.split("@")[-1], "n": a.n,
                        "payload_file": os.path.basename(a.payloads), "pid": os.getpid(),
                        "affinity": sorted(os.sched_getaffinity(0)),
                        "utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}) + "\n")
    f.write(json.dumps({"ping_ns": pings}) + "\n")
    f.write(json.dumps({"emit_ns": samples}) + "\n")
def pct(xs, q):
    xs = sorted(xs); return xs[min(len(xs) - 1, int(q * len(xs)))] / 1e3
print(f"{a.label:28s} mode={a.mode:7s} n={a.n} emit_us p50={pct(samples,.5):8.1f} p90={pct(samples,.9):8.1f} "
      f"p99={pct(samples,.99):8.1f} max={max(samples)/1e3:8.1f} mean={sum(samples)/len(samples)/1e3:8.1f}"
      + (f" | ping_us p50={pct(pings,.5):7.1f} p99={pct(pings,.99):7.1f}" if pings else ""))
