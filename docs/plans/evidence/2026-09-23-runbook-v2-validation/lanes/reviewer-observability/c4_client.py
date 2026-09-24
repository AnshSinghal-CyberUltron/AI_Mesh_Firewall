#!/usr/bin/env python3
"""reviewer-observability: client-side view of the v2.1 C4 streaming SLO (T_input + T_release_processing +
T_finalize inside the SLO; T_holdback_wait published separately), computed per sampled SSE stream from raw
olg arr_ns/arr_cum and synthprov emit_ns/emit_cum only (no gateway data, no cross-host clocks).

For client content event k (arrival a[k] on the client clock, cumulative bytes C[k]) the upstream piece whose
arrival TRIGGERED the release is m(k) = first provider piece with P[m] >= C[k] (the gateway releases everything
up to the leading non-word char of the newest piece and holds the rest). d_k = a[k] - e[m(k)] is then the
addon of that release WITHOUT the holdback wait (the wait for piece m to arrive is excluded because we measure
from m's own emission).  d_0 is the first released byte's addon (= T_addon_first by first byte).
Per request (sampled SSE):  C4 = max(T_addon_total, max_k d_k)      (JSON: T_addon_total)
  lower bound: if several upstream pieces were coalesced into one read, measuring from the newest piece hides
  part of the older piece's wait (that part is processing/loop delay, i.e. C4 counts it) -> C4 is a LOWER bound.
Also reported: proc_extra = max_k d_k - d_0 (mid-stream processing beyond the first byte's) and
hold = per-stream max over pieces j of (first covering arrival - e[j]) - max_k d_k-ish decomposition.
Views mirror recompute.py (qualified; infra = +inf; policy blocks excluded; uniform fnv1a64(rid) % 10 sample).
"""
from __future__ import annotations

import bisect
import json
import math
import sys
from collections import Counter
from pathlib import Path

import orjson

sys.path.insert(0, str(Path(__file__).resolve().parent))
import recompute as R  # noqa: E402

INF = float("inf")


def main():
    root = Path(sys.argv[1])
    pos = [x for x in sys.argv[1:] if not x.startswith("--")]
    lgg = pos[1] if len(pos) > 1 else "*/lg"
    pg = pos[2] if len(pos) > 2 else "*/prov"
    stages = R.STAGES_DEFAULT.split(",")
    direct = "--direct" in sys.argv
    by_rid, calls, n2r, _ = R.load_provider(sorted(p for p in root.glob(pg) if p.is_dir()))
    c4, c4_sse, c4_json, extra, dmax, d0s, lagmax, nohold = [], [], [], [], [], [], [], []
    cnt = Counter()
    for d in sorted(p for p in root.glob(lgg) if p.is_dir()):
        f = R.find(d, "requests.jsonl")
        with R.open_any(f) as fh:
            for line in fh:
                c = orjson.loads(line)
                if c.get("ph") != 2:
                    continue
                rid = c["rid"]
                if R.fnv1a64(rid) % 10 != 0:
                    continue
                p = by_rid.get(rid)
                ncalls = calls.get(rid, 0)
                st = R.stages_of(c.get("stages"))
                status = c.get("status", 0)
                disp = (c.get("disp") or "").upper()
                if status in R.BLOCK_STATUSES and disp == "BLOCK" and p is None and not any(v == "U" for v in st.values()):
                    cnt["policy_block"] += 1
                    continue
                ok = (status == 200 and c.get("done_seen") and not c.get("err") and p is not None and ncalls == 1
                      and p.get("status") == 200 and c.get("content_sha256") == p.get("content_sha256")
                      and (direct or (disp in R.QUAL_DISP and all(st.get(s) == "E" for s in stages))))
                if not ok:
                    cnt["infra_or_unqualified"] += 1
                    c4.append(INF)
                    (c4_sse if c.get("stream") else c4_json).append(INF)
                    nohold.append(INF)
                    continue
                total = c["end_ns"] - p["recv_to_last_ns"]
                nohold.append(total)
                if not c.get("stream"):
                    c4.append(total)
                    c4_json.append(total)
                    cnt["json"] += 1
                    continue
                e, P, a, C = p.get("emit_ns"), p.get("emit_cum"), c.get("arr_ns"), c.get("arr_cum")
                if not (e and P and a and C and C[-1] == P[-1]):
                    cnt["sse_unmappable"] += 1
                    continue
                ds = []
                for k in range(len(a)):
                    m = bisect.bisect_left(P, C[k])
                    m = min(m, len(P) - 1)
                    ds.append(a[k] - e[m])
                # per-piece covering lag (harness definition) for reference
                k, n, lm = 0, len(a), 0
                for j in range(len(e)):
                    while k < n and C[k] < P[j]:
                        k += 1
                    lm = max(lm, a[k] - e[j])
                dm = max(ds)
                v = max(total, dm)
                c4.append(v)
                c4_sse.append(v)
                d0s.append(ds[0])
                dmax.append(dm)
                extra.append(dm - ds[0])
                lagmax.append(lm)
                cnt["sse"] += 1
    out = {"run": str(root), "counts": dict(cnt),
           "C4_T_fw_addon_ABC": R.dist(c4), "C4_sse": R.dist(c4_sse), "C4_json": R.dist(c4_json),
           "nohold_sample_c": R.dist(nohold),
           "d0_first_byte_addon": R.dist(d0s), "dmax_per_stream": R.dist(dmax),
           "proc_extra_per_stream": R.dist(extra), "harness_lag_max_per_stream": R.dist(lagmax)}
    print(json.dumps(out))


if __name__ == "__main__":
    main()
