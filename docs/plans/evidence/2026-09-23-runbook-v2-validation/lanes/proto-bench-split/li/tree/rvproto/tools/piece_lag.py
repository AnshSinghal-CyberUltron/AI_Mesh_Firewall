"""Per-piece release lag by piece position, recomputed from raw olg + synthprov records with the
analyzer's own definition (first client arrival covering provider piece j) - (emission of j).
Shows WHERE in the stream an injected hold lands, which a per-stream max can dilute.
  python tools/piece_lag.py <run_dir> [<run_dir> ...]   (run_dir has olg/requests.jsonl, provider.jsonl)"""
import importlib.util
import json
import statistics
import sys

SP = "/tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/1b4d4994-cf08-4d34-a6cb-54fd36f36877/scratchpad"
spec = importlib.util.spec_from_file_location("analyze", f"{SP}/harness/analyze.py")
an = importlib.util.module_from_spec(spec)
spec.loader.exec_module(an)  # type: ignore[union-attr]


def lags(e, P, a, C):
    out, k = [], 0
    for j in range(len(e)):
        while k < len(a) and C[k] < P[j]:
            k += 1
        if k == len(a):
            return None
        out.append(a[k] - e[j])
    return out


for run in sys.argv[1:]:
    prov = {r["rid"]: r for r in an.read_jsonl(f"{run}/provider.jsonl") if r.get("emit_ns")}
    by_pos: dict[int, list[float]] = {}
    n = 0
    for c in an.read_jsonl(f"{run}/olg/requests.jsonl"):
        p = prov.get(c.get("rid"))
        if not p or not c.get("arr_ns") or not c.get("arr_cum"):
            continue
        L = lags(p["emit_ns"], p["emit_cum"], c["arr_ns"], c["arr_cum"])
        if not L:
            continue
        n += 1
        for j, v in enumerate(L):
            by_pos.setdefault(j, []).append(v / 1e6)
    row = {j: round(statistics.median(v), 1) for j, v in sorted(by_pos.items()) if 40 <= j <= 56}
    print(json.dumps({"run": run.rsplit("/", 1)[-1], "streams": n, "median_lag_ms_by_piece": row}))
