#!/usr/bin/env python3
"""split lane: one step under the controller's corrected rules, from raw olg + synthprov records.

Rules (controller, from reviewer-hidden-failures C2/C3). The helpers are reused unchanged from
reviewer-observability's recompute.py (tools/recompute_ref.py): record loading, joins, the per-piece
lag and first-token math, and nearest-rank percentiles. That tool imports neither analyze.py nor
pbu_analyze.py.
  Classification of each measured request (olg ph == 2), same as recompute_ref:
    policy block : 400/403/422/451 + x-rv-disposition BLOCK + zero provider calls + no stage 'U'.
                   Excluded from the latency cohorts. A block on a benign prompt is a false positive.
    qualified    : 200, complete, one provider call (provider 200), content sha equal,
                   disposition ALLOW|REDACT|FLAG, every profile stage 'E' (sut mode)
    infra        : everything else (503 sheds, timeouts, incomplete streams, degraded 200s,
                   fail-closed BLOCK with a 'U' stage, ...)
  Latency cohort = offered - policy blocks. Infra requests count as +inf. Nearest rank.
    JSON        T_addon_total = client end - provider recv_to_last
    SSE-total   T_addon_total
    SSE-first   arrival of the client event that completes provider token 1 minus that token's
                provider emission (per-chunk data: fnv1a64(rid) % sample_mod == 0 subset)
    SSE-strict  max(total, first, max per-piece release lag). Reported, not a gate.
  Step verdict: PASS iff p99 JSON < SLO, p99 SSE-total < SLO, infra <= budget x offered, and
  0 schedule drops (late > 5 ms). Run validity comes from the harness analyzer.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import recompute_ref as R  # noqa: E402

SP = Path(__file__).resolve().parents[3]


def _inject_lengths() -> dict:
    cs = {c["id"]: c for c in json.loads((SP / "harness" / "shared" / "canaries.json").read_text())["canaries"]}
    return {"email": 1 + len(cs["out.email"]["value"]), "aws": 1 + len(cs["out.aws"]["value"]),
            "split-aws": 1 + len(cs["out.aws"]["value"])}


def classify(c: dict, p: dict | None, ncalls: int, exp: dict, stages_req: list[str], mode: str) -> tuple[str, list]:
    status = c.get("status", 0)
    disp = (c.get("disp") or "").upper() if mode == "sut" else "ALLOW"
    st = R.stages_of(c.get("stages"))
    cls = c.get("cls") or "benign"
    if status in R.BLOCK_STATUSES and disp == "BLOCK" and p is None:
        if any(v == "U" for v in st.values()):
            return "infra", ["block_on_unavailable"]
        e_in = (exp.get(c.get("cid")) or {}).get("input")
        return ("block_expected" if (e_in == "BLOCK" or cls in R.ATTACK) else "block_fp"), []
    why = []
    if c.get("err"):
        why.append(c["err"])
    if status != 200 and not c.get("err"):
        why.append(f"http_{status}")
    if not c.get("done_seen"):
        why.append("incomplete")
    if p is None:
        why.append("unjoined")
    else:
        if ncalls != 1:
            why.append(f"calls_{ncalls}")
        if p.get("status") != 200:
            why.append(f"prov_{p.get('status')}")
        ok = (c.get("content_sha256") == p.get("content_sha256") and
              (c.get("tool_args_sha256") or None) == (p.get("tool_args_sha256") or None))
        if not ok:
            il = R.inject_len(p.get("inject"))
            leak = [h for h in (c.get("canary_hits") or []) if str(h).startswith("out.")]
            lok = (il is not None and p.get("content_len") is not None and c.get("content_len") is not None
                   and 0 <= c["content_len"] - (p["content_len"] - il) <= 64)
            if not (p.get("inject_applied") and not leak and lok):
                why.append("content_mismatch")
    if mode == "sut":
        if disp not in R.QUAL_DISP:
            why.append(f"disp_{disp or 'missing'}")
        for s in stages_req:
            if st.get(s) != "E":
                why.append(f"stage_{s}_{st.get(s) or 'missing'}")
    return ("infra" if why else "qualified"), why


def run(a: argparse.Namespace) -> dict:
    R.INJECT_LEN = _inject_lengths()
    root = Path(a.run)
    lg_dirs = sorted(p for p in root.glob("*/lg") if p.is_dir())
    prov_dirs = sorted(p for p in root.glob("*/prov") if p.is_dir())
    by_rid, calls, nonce2rid, prov_n = R.load_provider(prov_dirs)
    exp = R.expectations(a.corpus)
    stages_req = [s for s in a.stages.split(",") if s] if a.mode == "sut" else []
    cat, reasons, anomalies = Counter(), Counter(), Counter()
    json_total, sse_total, sse_first, sse_strict, all_total, sse_lag = ([] for _ in range(6))
    offered = offered_sse = offered_json = drops = 0
    late_max = 0
    sampled_sse = 0
    for d in lg_dirs:
        f = R.find(d, "requests.jsonl")
        if f is None:
            anomalies["missing_requests_jsonl"] += 1
            continue
        with R.open_any(f) as fh:
            for line in fh:
                if not line.strip():
                    continue
                c = R.orjson.loads(line)
                if c.get("ph", 2) != 2:
                    continue
                offered += 1
                stream = bool(c.get("stream"))
                offered_sse += stream
                offered_json += not stream
                late = c.get("late_ns", 0)
                late_max = max(late_max, late)
                if late > a.drop_ms * 1e6 or c.get("err") == "inflight_cap":
                    drops += 1
                rid = c["rid"]
                p, ncalls = by_rid.get(rid), calls.get(rid, 0)
                if p is None and c.get("nonce") in nonce2rid:
                    prid = nonce2rid[c["nonce"]]
                    p, ncalls = by_rid.get(prid), calls.get(prid, 0)
                k, why = classify(c, p, ncalls, exp, stages_req, a.mode)
                cat[k] += 1
                for w in why:
                    reasons[w] += 1
                if k.startswith("block"):
                    continue  # policy outcome: outside the latency cohort
                sampled = R.fnv1a64(rid) % a.sample_mod == 0
                if stream and sampled:
                    sampled_sse += 1
                if k == "infra":
                    (sse_total if stream else json_total).append(R.INF)
                    all_total.append(R.INF)
                    if stream and sampled:
                        sse_first.append(R.INF)
                        sse_strict.append(R.INF)
                    continue
                if not (c.get("end_ns") and p.get("recv_to_last_ns") is not None):
                    anomalies["qualified_without_total"] += 1
                    continue
                total = c["end_ns"] - p["recv_to_last_ns"]
                all_total.append(total)
                if not stream:
                    json_total.append(total)
                    continue
                sse_total.append(total)
                if not sampled:
                    continue
                P, C = p.get("emit_cum"), c.get("arr_cum")
                if not (p.get("emit_ns") and c.get("arr_ns") and P and C and C[-1] == P[-1]):
                    anomalies["sampled_sse_unmappable"] += 1
                    continue
                lag, first = R.lag_piece(p["emit_ns"], P, c["arr_ns"], C)
                if lag is None or first is None:
                    anomalies["sampled_sse_lag_none"] += 1
                    continue
                sse_first.append(first)
                sse_lag.append(lag)
                sse_strict.append(max(total, first, lag))
    measure_s = None
    lg_cpu = []
    valid_notes = []
    for d in lg_dirs:
        mf = d / "manifest.json"
        if not mf.exists():
            valid_notes.append(f"missing manifest {d}")
            continue
        man = json.loads(mf.read_text())
        measure_s = (man.get("config") or {}).get("duration_s")
        cnt = man.get("counts") or {}
        if cnt.get("scheduled") != cnt.get("recorded"):
            valid_notes.append(f"records incomplete {d}")
    dists = {"JSON_total": R.dist(json_total), "SSE_total": R.dist(sse_total), "SSE_first_tok1": R.dist(sse_first),
             "SSE_strict": R.dist(sse_strict), "ALL_total": R.dist(all_total), "SSE_lag_qualified": R.dist(sse_lag)}
    infra = cat["infra"]
    ierr = infra / offered if offered else None
    p99j, p99s = dists["JSON_total"].get("p99"), dists["SSE_total"].get("p99")
    lt = lambda v: v is not None and v != R.INF and v < a.slo_ms  # noqa: E731
    checks = {"p99_json_total_lt_slo": lt(p99j), "p99_sse_total_lt_slo": lt(p99s),
              "infra_le_budget": ierr is not None and ierr <= a.err_budget, "zero_drops": drops == 0,
              "records_complete": not valid_notes}
    if a.mode == "direct":
        checks = {"zero_drops": drops == 0, "zero_infra": infra == 0, "records_complete": not valid_notes}
    return {
        "run": root.name, "mode": a.mode, "sample_mod": a.sample_mod, "offered": offered,
        "offered_sse": offered_sse, "offered_json": offered_json, "measure_s": measure_s,
        "offered_rps": round(offered / measure_s, 2) if measure_s else None,
        "qualified": cat["qualified"], "qualified_rps": round(cat["qualified"] / measure_s, 2) if measure_s else None,
        "categories": dict(cat), "infra_rate": ierr, "infra_pct": round(100 * ierr, 4) if ierr is not None else None,
        "infra_reasons": dict(reasons.most_common(20)), "drops": drops, "late_max_ms": round(late_max / 1e6, 3),
        "fp_block_rate": round(cat["block_fp"] / offered, 5) if offered else None,
        "sampled_sse_in_cohort": sampled_sse, "anomalies": dict(anomalies), "provider_records": prov_n,
        "latency_ms": dists, "checks": checks, "verdict": "PASS" if all(checks.values()) else "FAIL",
        "notes": valid_notes,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True)
    ap.add_argument("--mode", default="sut", choices=["sut", "direct"])
    ap.add_argument("--corpus")
    ap.add_argument("--stages", default=R.STAGES_DEFAULT)
    ap.add_argument("--sample-mod", type=int, default=1)
    ap.add_argument("--slo-ms", type=float, default=20.0)
    ap.add_argument("--err-budget", type=float, default=0.001)
    ap.add_argument("--drop-ms", type=float, default=5.0)
    ap.add_argument("--out")
    a = ap.parse_args()
    r = run(a)
    s = json.dumps(r, indent=1, default=str)
    if a.out:
        Path(a.out).write_text(s + "\n")
    L = r["latency_ms"]
    print(f"{r['run']} {r['mode']}: {r['verdict']} offered {r['offered']} ({r['offered_rps']}/s) qualified "
          f"{r['qualified']} ({r['qualified_rps']}/s) infra {r['categories'].get('infra', 0)} ({r['infra_pct']}%) "
          f"FP-blocks {r['categories'].get('block_fp', 0)} drops {r['drops']}")
    for k in ("JSON_total", "SSE_total", "SSE_first_tok1", "SSE_strict", "ALL_total"):
        print(f"  {k:15s} {L[k]}")
    print(f"  checks {r['checks']} infra_reasons {r['infra_reasons']} anomalies {r['anomalies']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
