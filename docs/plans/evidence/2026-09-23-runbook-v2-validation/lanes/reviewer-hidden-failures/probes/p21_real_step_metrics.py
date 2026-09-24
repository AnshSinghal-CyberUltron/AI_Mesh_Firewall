"""P21: re-analyse a REAL proto-bench-unit step (raw olg + synthprov files) for two instrument effects:
(1) T_addon_first's 'first content' event: how many bytes it carried (a lone separator released by the holdback?)
(2) how much of T_fw_addon's tail comes from the 10% sampled lag term, and what p99 the lag term has on its own.
Uses the harness analyzer's own loader/joins (analyze.py)."""
import collections, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1]); import analyze as A
run = Path(sys.argv[2])
prov = A.Provider(); prov.load([str(p) for p in run.glob("*/prov")])
first_bytes = collections.Counter(); gap = []; lag_sse = []; total_sse = []; first_sse = []; n_sse = n_sampled = 0
for f in A.expand([str(p) for p in run.glob("*/lg")], "requests.jsonl"):
    for c in A.read_jsonl(f):
        if c.get("ph") != 2 or not c.get("stream") or c.get("status") != 200:
            continue
        p = prov.by_rid.get(c["rid"])
        if not p: continue
        n_sse += 1
        if c.get("end_ns") and p.get("recv_to_last_ns") is not None: total_sse.append(c["end_ns"] - p["recv_to_last_ns"])
        if c.get("first_ns") and p.get("recv_to_first_ns") is not None: first_sse.append(c["first_ns"] - p["recv_to_first_ns"])
        if c.get("sampled") and c.get("arr_cum") and "emit_ns" in p:
            n_sampled += 1
            first_bytes[c["arr_cum"][0]] += 1
            need = p["emit_cum"][0]
            k = next((i for i, x in enumerate(c["arr_cum"]) if x >= need), None)
            if k is not None: gap.append(c["arr_ns"][k] - c["arr_ns"][0])
            if c["arr_cum"][-1] == p["emit_cum"][-1]:
                lag_sse.append(A.release_lag_per_piece(p["emit_ns"], p["emit_cum"], c["arr_ns"], c["arr_cum"]))
d = lambda xs: A.dist_ms([x for x in xs if x is not None])
print(f"run {run.name}: SSE 200s joined={n_sse}, per-chunk sampled={n_sampled} ({100*n_sampled/max(1,n_sse):.1f}%)")
print(f"(1) bytes carried by the first content event (sampled streams): {dict(first_bytes.most_common(4))}")
print(f"    time from that event to the event completing provider token 1: {d(gap)}")
print(f"(2) T_addon_first (SSE, all): {d(first_sse)}")
print(f"    T_addon_total (SSE, all): {d(total_sse)}")
print(f"    T_release_lag_max (sampled only): {d(lag_sse)}")
