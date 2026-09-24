"""P20: crafted raw records through the REAL analyzers (harness analyze.py; proto-bench-unit pbu_analyze.py).
A  per-request p99 of a max() whose lag term exists only for the 10% sampled SSE streams (sample-mod 10)
B  timeouts/errors inside the 0.1% budget are removed from the latency cohort instead of counted as +inf
C  pbu 'load knee' (p99 of T_addon_total only) vs a mid-stream stall on EVERY SSE stream
Record builders are the harness's own test helpers (tests/test_analyze.py: synth_stream/pair/run)."""
import json, sys, tempfile
from pathlib import Path
H = Path(sys.argv[1]); PBU = Path(sys.argv[2])
sys.path.insert(0, str(H)); sys.path.insert(0, str(H / "tests")); sys.path.insert(0, str(PBU))
import analyze as A
import test_analyze as T
MS = T.MS

def summarize(tag, s, truth):
    print(f"[{tag}] analyzer: qualified={s['qualified']} errors={s['errors']} err_rate={s['error_rate']} "
          f"p99 T_fw_addon={s['T_fw_addon']['p99']} ms  checks={ {k: v for k, v in s['verdict']['checks'].items() if k in ('p99_T_fw_addon_lt_slo','error_rate_le_budget')} } "
          f"VERDICT={'PASS' if s['verdict']['pass'] else 'FAIL'}")
    print(f"[{tag}] ground truth: {truth}")

# ---------------- A: dilution of the lag term (sample-mod 10) ----------------
N = 10000; recs = []; held = held_sampled = 0
for i in range(N):
    stream = (i % 10) < 7                       # 70% SSE
    sampled = stream and (i % 10 == 0)          # 10% of streams carry per-chunk timing (sample-mod 10)
    hold = stream and (i % 25 == 1 or i % 25 == 0) and (i % 50 < 2)  # placeholder, replaced below
    recs.append((i, stream, sampled))
out = []
for i, stream, sampled in recs:
    # 4% of SSE streams suffer a 40 ms mid-stream hold (e.g. a worker event-loop stall / held numeric run)
    h = stream and ((i // 10) % 25 == 0)
    held += h; held_sampled += h and sampled
    out.append(T.pair(i, stream=stream, n=60, hold_at=30 if h else None, hold=40 * MS if h else 0, sampled=sampled))
with tempfile.TemporaryDirectory() as tmp:
    s = T.run(tmp, [c for c, _ in out], [p for _, p in out], "--mode", "sut", "--profile-stages", "proxy")
sse = sum(1 for _, st, _ in recs if st)
summarize("A", s, f"{held}/{sse} SSE streams ({100*held/sse:.1f}%) = {100*held/N:.2f}% of all requests had a 40 ms added delay "
          f"(only {held_sampled} of them sampled) -> true per-request p99 of worst added delay = 40.3 ms")

# ---------------- B: errors removed from the cohort ----------------
out = []
for i in range(N):
    c, p = T.pair(i, stream=False, n=60, sampled=False)
    if i % 1000 == 0:                         # 10 requests (0.1%): client timeout after 120 s, no response
        c.update(status=0, err="timeout", done_seen=False, end_ns=120_000 * MS, first_ns=0)
    elif i % 1000 in range(1, 10) or i in (500, 1500, 2500, 3500, 4500):   # 95 requests (0.95%) at +25 ms
        c["end_ns"] += 25 * MS
    out.append((c, p))
with tempfile.TemporaryDirectory() as tmp:
    s = T.run(tmp, [c for c, _ in out], [p for _, p in out], "--mode", "sut", "--profile-stages", "proxy")
allv = sorted([(c["end_ns"] - p["recv_to_last_ns"]) if not c.get("err") else float("inf") for c, p in out])
tp99 = A.pct_sorted(allv, 0.99)
summarize("B", s, f"over ALL offered requests with timeouts counted as +inf: p99 = {tp99/1e6 if tp99 != float('inf') else 'inf'} ms")

# ---------------- C: pbu 'load knee' vs a stall on every SSE stream ----------------
import pbu_analyze as P
def write_pbu_run(root: Path, pairs):
    lg = root / "lg-vm" / "lg"; pv = root / "prov-vm" / "prov"; lg.mkdir(parents=True); pv.mkdir(parents=True)
    (lg / "requests.jsonl").write_text("".join(json.dumps(c) + "\n" for c, _ in pairs))
    (pv / "records.jsonl").write_text("".join(json.dumps(p) + "\n" for _, p in pairs))
    (lg / "manifest.json").write_text(json.dumps({"config": {"rate": 10, "duration_s": 100, "ramp_s": 0, "warmup_s": 0},
        "counts": {"scheduled": len(pairs), "recorded": len(pairs)}, "epoch_offset_s": 0.0, "interrupted": False}))
out = [T.pair(i, stream=(i % 10) < 7, n=60, hold_at=(30 if (i % 10) < 7 else None), hold=(100 * MS if (i % 10) < 7 else 0),
              sampled=((i % 10) == 0), cli={"stages": "canon:E,det:E,sem:E,resolve:E,dispatch:E,out:E,audit:E"}) for i in range(2000)]
with tempfile.TemporaryDirectory() as tmp:
    write_pbu_run(Path(tmp), out)
    r = P.classify(Path(tmp), "sut", "canon,det,sem,resolve,dispatch,out,audit".split(","), None, "none", 20.0, 0.001, 5.0)
print(f"[C] pbu: strict pass={r['pass']} (p99 T_fw_addon={r['T_fw_addon']['p99']} ms) | load_knee_pass={r['load_knee_pass']} "
      f"(p99 T_fw_addon_nohold={r['T_fw_addon_nohold']['p99']} ms) | T_release_lag_max p50={r['T_release_lag_max'].get('p50')} ms")
print("[C] ground truth: EVERY SSE stream (70% of requests) had one piece delayed 100 ms mid-stream")
