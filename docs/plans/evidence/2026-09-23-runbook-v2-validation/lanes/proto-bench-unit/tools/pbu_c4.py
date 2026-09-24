#!/usr/bin/env python3
"""Corrected SSE metrics for runs with per-chunk data on 100% of requests (olg + synthprov -sample-mod 1).

Controller rules (a)-(d):
  (a) every stream has per-chunk arrivals/emissions (runs must use -sample-mod 1; others are refused)
  (b) "first" = the client event that completes provider token 1 (not the first byte, which is a lone space)
  (c) p99 over ALL offered requests minus policy blocks, infra errors counted as +inf; qualified-only also shown
  (d) T_fw_addon three ways:
      STRICT : SSE max(T_addon_total, T_first_tok1, T_release_lag_max)   JSON T_addon_total
      C4     : SSE max(T_addon_total, max_k d_k), d_k = a[k] - e[m(k)], m(k) = first provider piece with
               cum >= client cum at event k (the upstream piece whose arrival released event k): T_input +
               T_release_processing, holdback wait excluded (logic of reviewer-observability/c4_client.py,
               without its %10 filter)                                     JSON T_addon_total
      LOAD   : T_addon_total (the earlier "load knee" metric)
Holdback wait (per provider piece j): released by event k(j) (first client event covering P[j]); the release
was triggered by piece m(k(j)); wait = e[m] - e[j] in ms and m - j in upstream tokens.
Classification is pbu_analyze.categorize() (policy blocks excluded; infra / degraded = +inf).
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pbu_analyze as P  # noqa: E402

A = P.A
INF = float("inf")


def dist(vals: list) -> dict:
    xs = sorted(vals)
    if not xs:
        return {"n": 0}
    n = len(xs)
    fin = [v for v in xs if v != INF]

    def q(p: float):
        v = xs[max(0, min(n - 1, math.ceil(p * n) - 1))]  # nearest rank
        return "inf" if v == INF else round(v / 1e6, 3)
    return {"n": n, "n_inf": n - len(fin), "p50": q(.5), "p90": q(.9), "p99": q(.99), "p999": q(.999),
            "max_finite": round(fin[-1] / 1e6, 3) if fin else None}


def tokdist(vals: list) -> dict:
    xs = sorted(vals)
    if not xs:
        return {"n": 0}
    n = len(xs)
    return {"n": n, "p50": xs[n // 2], "p90": xs[int(n * .9)], "p99": xs[min(n - 1, int(n * .99))],
            "max": xs[-1], "share_ge1": round(sum(1 for x in xs if x >= 1) / n, 4)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True)
    ap.add_argument("--profile-stages", default="canon,det,sem,resolve,dispatch,out,audit")
    ap.add_argument("--corpus", default=None)
    ap.add_argument("--mode", default="sut", choices=["sut", "direct"])
    a = ap.parse_args()
    run = Path(a.run)
    stages = [s for s in a.profile_stages.split(",") if s] if a.mode == "sut" else []
    prov = A.Provider()
    prov.load([str(p) for p in sorted(run.glob("*/prov"))])
    exp = A.load_expectations(a.corpus) if a.corpus else {}
    args = SimpleNamespace(policy="none", mode=a.mode)
    files = A.expand([str(p) for p in sorted(run.glob("*/lg"))], "requests.jsonl")
    for m in sorted(run.glob("*/lg/manifest.json")):
        if json.loads(m.read_text())["config"].get("sample_mod") != 1:
            raise SystemExit(f"{m}: olg sample_mod != 1 (rule a needs per-chunk data on every request)")
    nonce = Counter()
    for f in files:
        with A.open_any(f) as fh:
            for line in fh:
                if line.strip():
                    nonce[A.loads(line).get("nonce")] += 1
    views = {k: [] for k in ("strict_all", "strict_q", "c4_all", "c4_q", "load_all", "load_q",
                             "strict_sse_q", "c4_sse_q", "json_q", "first_tok1_q", "first_byte_q", "lag_q",
                             "proc_extra_q")}
    hold_ms, hold_tok, hold_stream_ms, hold_stream_tok = [], [], [], []
    cnt = Counter()
    for f in files:
        with A.open_any(f) as fh:
            for line in fh:
                if not line.strip():
                    continue
                c = A.loads(line)
                if c.get("ph", 2) != 2:
                    continue
                rid = c["rid"]
                p = prov.by_rid.get(rid)
                calls = prov.calls.get(rid, 0)
                k, m, reasons, saf = P.categorize(c, p, calls, args, exp, stages, nonce[c.get("nonce")] > 1,
                                                  a.mode, "none")
                cnt[k] += 1
                if k.startswith("policy_block"):
                    continue
                if k != "qualified":
                    for v in ("strict_all", "c4_all", "load_all"):
                        views[v].append(INF)
                    continue
                total = m["addon_total"]
                views["load_q"].append(total)
                views["load_all"].append(total)
                if not c.get("stream"):
                    for v in ("strict_all", "strict_q", "c4_all", "c4_q", "json_q"):
                        views[v].append(total)
                    continue
                e, Pc = p.get("emit_ns"), p.get("emit_cum")
                arr, C = c.get("arr_ns"), c.get("arr_cum")
                if not (e and Pc and arr and C and C[-1] == Pc[-1]):
                    cnt["sse_unmappable"] += 1
                    for v in ("strict_all", "c4_all"):
                        views[v].append(INF)
                    continue
                n_e, n_a = len(e), len(arr)
                # C4: event k released by piece m(k) = first piece with Pc[m] >= C[k]
                ds = []
                mk = []
                j = 0
                for kk in range(n_a):
                    while j < n_e - 1 and Pc[j] < C[kk]:
                        j += 1
                    mk.append(j)
                    ds.append(arr[kk] - e[j])
                # per piece j: covering event k(j); lag_j = arr[k] - e[j]; hold = e[m(k)] - e[j], m(k) - j
                kk = 0
                lag = None
                first_tok1 = None
                smax_ms, smax_tok = 0, 0
                for jj in range(n_e):
                    while kk < n_a - 1 and C[kk] < Pc[jj]:
                        kk += 1
                    lj = arr[kk] - e[jj]
                    if jj == 0:
                        first_tok1 = lj
                    lag = lj if lag is None or lj > lag else lag
                    hm, ht = e[mk[kk]] - e[jj], mk[kk] - jj
                    hold_ms.append(hm)
                    hold_tok.append(ht)
                    smax_ms, smax_tok = max(smax_ms, hm), max(smax_tok, ht)
                hold_stream_ms.append(smax_ms)
                hold_stream_tok.append(smax_tok)
                dmax = max(ds)
                strict = max(total, first_tok1, lag)
                c4 = max(total, dmax)
                for v, x in (("strict_all", strict), ("strict_q", strict), ("strict_sse_q", strict),
                             ("c4_all", c4), ("c4_q", c4), ("c4_sse_q", c4), ("first_tok1_q", first_tok1),
                             ("lag_q", lag), ("proc_extra_q", dmax - ds[0])):
                    views[v].append(x)
                if m.get("addon_first") is not None:
                    views["first_byte_q"].append(m["addon_first"])
    out = {"run": run.name, "counts": dict(cnt), "rules": "a: sample-mod 1; b: first=token-1 event; c: +inf infra",
           **{k: dist(v) for k, v in views.items()},
           "holdback_wait_per_piece_ms": dist(hold_ms), "holdback_wait_per_piece_tokens": tokdist(hold_tok),
           "holdback_wait_per_stream_max_ms": dist(hold_stream_ms),
           "holdback_wait_per_stream_max_tokens": tokdist(hold_stream_tok)}
    (run / "pbu_c4.json").write_text(json.dumps(out, indent=1) + "\n")
    L = [f"# {run.name} corrected metrics (rules a-d)", f"counts {out['counts']}"]
    for k in ("strict_all", "strict_q", "c4_all", "c4_q", "load_all", "load_q", "strict_sse_q", "c4_sse_q", "json_q",
              "first_tok1_q", "first_byte_q", "lag_q", "proc_extra_q", "holdback_wait_per_piece_ms",
              "holdback_wait_per_stream_max_ms"):
        L.append(f"{k}: {out[k]}")
    L.append(f"holdback_wait_per_piece_tokens: {out['holdback_wait_per_piece_tokens']}")
    L.append(f"holdback_wait_per_stream_max_tokens: {out['holdback_wait_per_stream_max_tokens']}")
    (run / "pbu_c4.md").write_text("\n".join(L) + "\n")
    print("\n".join(L))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
