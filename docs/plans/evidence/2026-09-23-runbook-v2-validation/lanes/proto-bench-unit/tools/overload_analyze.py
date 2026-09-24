#!/usr/bin/env python3
"""GW19 overload step analysis (pbu_overload.sh): timeline + per-phase stats from raw files.

Phases on the base loadgen's clock (s from its start): ramp, warm, pre, burst, post.
Per request the controller categories of pbu_analyze.categorize() are used (qualified / policy block /
infra error, with 503 overload sheds broken out). The "admitted cohort" = qualified requests.
Recovery = first 10 s bin after the burst with zero sheds and p99 T_addon_total <= 1.25 x the pre-burst p99.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pbu_analyze as P  # noqa: E402

A = P.A


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True)
    ap.add_argument("--profile-stages", default="canon,det,sem,resolve,dispatch,out,audit")
    ap.add_argument("--bin", type=float, default=10.0)
    a = ap.parse_args()
    run = Path(a.run)
    step = json.loads((run / "step.json").read_text())
    stages = a.profile_stages.split(",")
    prov = A.Provider()
    prov.load([str(p) for p in sorted(run.glob("*/prov"))])
    args = SimpleNamespace(policy="none", mode="sut")
    lgs = sorted(p for p in run.glob("rv-pbu-lg-*/lg") if p.is_dir())
    mans = {d: json.loads((d / "manifest.json").read_text()) for d in lgs}
    base_dir = next(d for d in lgs if mans[d]["config"]["lg_index"] == 0)
    t_base = mans[base_dir]["config"]["start_at_unix_ms"] / 1000.0
    ramp, warm = step["ramp_s"], step["warmup_s"]
    pre, burst, post = step["pre_s"], step["burst_s"], step["post_s"]
    edges = {"pre": (ramp + warm, ramp + warm + pre), "burst": (ramp + warm + pre, ramp + warm + pre + burst),
             "post": (ramp + warm + pre + burst, ramp + warm + pre + burst + post)}
    nonce = Counter()
    recs = []
    for d in lgs:
        off = mans[d]["config"]["start_at_unix_ms"] / 1000.0 - t_base
        f = A.find_raw(d, "requests.jsonl")
        with A.open_any(f) as fh:
            for line in fh:
                if line.strip():
                    c = A.loads(line)
                    nonce[c.get("nonce")] += 1
                    recs.append((off + c.get("sched_ns", 0) / 1e9, d.parent.name, c))
    bins = defaultdict(lambda: {"n": 0, "cat": Counter(), "tot": [], "fw": [], "shed_rt": []})
    phase = defaultdict(lambda: {"n": 0, "cat": Counter(), "tot": [], "fw": [], "first": [], "lag": [], "shed_rt": [],
                                 "infra_reasons": Counter(), "detail": Counter(), "by_lg": Counter()})
    drops = Counter()
    for t, lg, c in recs:
        rid = c["rid"]
        p = prov.by_rid.get(rid)
        calls = prov.calls.get(rid, 0)
        if p is None and c.get("nonce") in prov.nonce_to_rid:
            prid = prov.nonce_to_rid[c["nonce"]]
            p, calls = prov.by_rid.get(prid), prov.calls.get(prid, 0)
        k, m, reasons, _ = P.categorize(c, p, calls, args, {}, stages, nonce[c.get("nonce")] > 1, "sut", "none")
        status = c.get("status", 0)
        shed = status == 503 and "overloaded" in (c.get("err_detail") or "")
        kk = "shed_503" if shed else k
        ph = next((name for name, (lo, hi) in edges.items() if lo <= t < hi), None)
        if c.get("late_ns", 0) > 5e6:
            drops[ph or "outside"] += 1
        b = int(t // a.bin) * a.bin
        bins[b]["n"] += 1
        bins[b]["cat"][kk] += 1
        if k == "qualified" and "addon_total" in m:
            bins[b]["tot"].append(m["addon_total"])
            bins[b]["fw"].append(m["fw_addon"])
        if shed:
            bins[b]["shed_rt"].append(c.get("end_ns") or 0)
        if ph is None:
            continue
        s = phase[ph]
        s["n"] += 1
        s["cat"][kk] += 1
        s["by_lg"][lg] += 1
        if k == "qualified" and "addon_total" in m:
            s["tot"].append(m["addon_total"])
            s["fw"].append(m["fw_addon"])
            if c.get("stream") and "addon_first" in m:
                s["first"].append(m["addon_first"])
            if m.get("lag_piece") is not None:
                s["lag"].append(m["lag_piece"])
        if shed:
            s["shed_rt"].append(c.get("end_ns") or 0)
        if k == "infra_error" and not shed:
            for r in reasons:
                s["infra_reasons"][r.split(":")[0]] += 1
            s["detail"][f"{status} {c.get('err') or ''} {c.get('err_detail') or ''}".strip()] += 1
    out = {"step": step, "phases": {}, "timeline": [], "drops_by_phase": dict(drops)}
    for ph, (lo, hi) in edges.items():
        s = phase[ph]
        dur = hi - lo
        n = s["n"]
        out["phases"][ph] = {
            "window_s": [lo, hi], "offered": n, "offered_rps": round(n / dur, 1),
            "qualified_rps": round(s["cat"]["qualified"] / dur, 1),
            "categories": dict(s["cat"]), "shed_rate": round(s["cat"]["shed_503"] / n, 4) if n else None,
            "infra_error_rate_excl_shed": round(s["cat"]["infra_error"] / n, 5) if n else None,
            "infra_error_rate_incl_shed": round((s["cat"]["infra_error"] + s["cat"]["shed_503"]) / n, 5) if n else None,
            "infra_reasons_excl_shed": dict(s["infra_reasons"]), "infra_detail_excl_shed": dict(s["detail"].most_common(8)),
            "T_addon_total_admitted": A.dist_ms(s["tot"]), "T_fw_addon_admitted": A.dist_ms(s["fw"]),
            "T_addon_first_sse_admitted": A.dist_ms(s["first"]), "T_release_lag_max_admitted": A.dist_ms(s["lag"]),
            "shed_response_time": A.dist_ms(s["shed_rt"]), "by_loadgen": dict(s["by_lg"]),
        }
    pre_p99 = out["phases"]["pre"]["T_addon_total_admitted"].get("p99")
    rec_t = None
    for b in sorted(bins):
        x = bins[b]
        d_tot = A.dist_ms(x["tot"])
        row = {"t": b, "offered_rps": round(x["n"] / a.bin, 1), "qualified_rps": round(x["cat"]["qualified"] / a.bin, 1),
               "shed_rps": round(x["cat"]["shed_503"] / a.bin, 1), "infra_rps": round(x["cat"]["infra_error"] / a.bin, 1),
               "fp_block_rps": round(x["cat"]["policy_block_fp"] / a.bin, 1),
               "p50_addon_total_ms": d_tot.get("p50"), "p99_addon_total_ms": d_tot.get("p99"),
               "p99_fw_addon_ms": A.dist_ms(x["fw"]).get("p99"), "shed_rt_p99_ms": A.dist_ms(x["shed_rt"]).get("p99")}
        out["timeline"].append(row)
        if (rec_t is None and b >= edges["burst"][1] and x["cat"]["shed_503"] == 0 and x["cat"]["infra_error"] == 0
                and pre_p99 and d_tot.get("p99") is not None and d_tot["p99"] <= 1.25 * pre_p99):
            rec_t = b
    out["recovery_s_after_burst_end"] = None if rec_t is None else round(rec_t - edges["burst"][1], 1)
    # probe (Retry-After)
    pf = run / "rv-pbu-lg-2" / "probe.jsonl"
    pz = A.find_raw(run / "rv-pbu-lg-2", "probe.jsonl")
    probes = A.read_jsonl(pz) if pz else []
    pc = Counter(p["status"] for p in probes)
    ra = Counter((p["status"], p["retry_after"], p["retry_after_ms"] != "") for p in probes)
    ra_ms = sorted(int(p["retry_after_ms"]) for p in probes if p.get("retry_after_ms"))
    out["probe"] = {"n": len(probes), "status": dict(pc),
                    "status_retry_after_hasms": {f"{k[0]}|ra={k[1]}|ms={k[2]}": v for k, v in ra.most_common(12)},
                    "retry_after_ms": {"n": len(ra_ms), "min": ra_ms[0] if ra_ms else None,
                                       "p50": ra_ms[len(ra_ms) // 2] if ra_ms else None, "max": ra_ms[-1] if ra_ms else None},
                    "file": str(pf)}
    # unit: OOM, restarts, sheds by reason, RSS and gauge timeline
    u = run / "rv-proto-unit-1" / "unit"
    unit = {}
    if u.exists():
        before = (u / "dmesg.before").read_text().splitlines() if (u / "dmesg.before").exists() else []
        after = (u / "dmesg.after").read_text().splitlines() if (u / "dmesg.after").exists() else []
        new = after[len(before):] if after[: len(before)] == before else [l for l in after if l not in set(before)]
        unit["dmesg_new_lines"] = len(new)
        unit["dmesg_oom_or_kill"] = [l for l in new if any(w in l.lower() for w in ("oom", "killed process", "out of memory"))]
        # only the rvproto python processes (pgrep -af also lists the capturing shell itself)
        pb = [x for x in (u / "pids.before").read_text().split("\n") if "-m rvproto.serve" in x] if (u / "pids.before").exists() else []
        pa = [x for x in (u / "pids.after").read_text().split("\n") if "-m rvproto.serve" in x] if (u / "pids.after").exists() else []
        unit["rvproto_pids_unchanged"] = sorted(pb) == sorted(pa)
        unit["rvproto_procs_before_after"] = [len([x for x in pb if x.strip()]), len([x for x in pa if x.strip()])]
        sut = P.sut_side(run, sum(ph["offered"] for ph in out["phases"].values()))
        if sut:
            unit["worker_counts"] = sut.get("worker_counts")
            unit["owner_counts"] = sut.get("owner_counts")
            unit["rss_mb_max_total_by_role"] = sut.get("rss_mb_max_total_by_role")
            unit["rss_mb_max_single_proc_by_role"] = sut.get("rss_mb_max_single_proc_by_role")
            unit["gpu"] = sut.get("gpu")
            unit["gauges_window"] = sut.get("gauges_window")
            unit["gateway_cores"] = sut.get("gateway_cores")
            unit["worker_hist"] = {k: v for k, v in (sut.get("worker_hist") or {}).items()
                                   if k in ("t_input_ns", "t_guard_wait_ns", "guard_owner_rtt_ns", "loop_lag_ns")}
        samples = A.read_jsonl(A.find_raw(u, "samples.jsonl")) if A.find_raw(u, "samples.jsonl") else []
        sched = json.loads((u / "schedule.json").read_text())
        tl = []
        for s in samples:
            if not s.get("gauges"):
                continue
            rss = sum(p["rss_kb"] for p in s["procs"].values() if p["role"] in ("worker", "launcher") or p["role"].startswith("owner"))
            g = s["gauges"]
            tl.append({"t": round(s["t"] - sched["start"], 1), "gw_rss_mb": round(rss / 1024, 1),
                       "inflight": g.get("w.inflight_requests.sum"), "active_streams": g.get("w.active_streams.sum"),
                       "input_q": g.get("w.input_queue_depth.sum"), "guard_tokens": g.get("w.guard_inflight_tokens.sum"),
                       "owner_q_items": g.get("o.guard_queue_items.sum")})
        unit["gauge_timeline_5s"] = tl
    out["unit"] = unit
    (run / "overload_summary.json").write_text(json.dumps(out, indent=1, default=str) + "\n")
    L = [f"# {run.name} overload: base {step['base_rate']}/s + burst {step['burst_rate']}/s for {burst}s", ""]
    for ph, v in out["phases"].items():
        L.append(f"## {ph} {v['window_s']}: offered {v['offered_rps']}/s qualified {v['qualified_rps']}/s shed {v['shed_rate']} "
                 f"infra(excl shed) {v['infra_error_rate_excl_shed']} cats {v['categories']}")
        L.append(f"   admitted T_addon_total {A.fmt(v['T_addon_total_admitted'])}")
        L.append(f"   admitted T_fw_addon    {A.fmt(v['T_fw_addon_admitted'])}")
        L.append(f"   shed response time     {A.fmt(v['shed_response_time'])}")
        L.append(f"   infra detail excl shed {v['infra_detail_excl_shed']}")
    L.append(f"recovery after burst end: {out['recovery_s_after_burst_end']} s; drops by phase {out['drops_by_phase']}")
    L.append(f"probe: {out['probe']}")
    L.append(f"unit: oom={unit.get('dmesg_oom_or_kill')} pids_unchanged={unit.get('rvproto_pids_unchanged')} "
             f"rss={unit.get('rss_mb_max_total_by_role')} counts={unit.get('worker_counts')} owner={unit.get('owner_counts')}")
    L.append("timeline (t, offered, qualified, shed, infra, p99 addon_total, p99 fw):")
    for r in out["timeline"]:
        L.append(f"  {r['t']:6.0f} {r['offered_rps']:7.1f} {r['qualified_rps']:7.1f} {r['shed_rps']:7.1f} {r['infra_rps']:6.1f} "
                 f"{r['p99_addon_total_ms']} {r['p99_fw_addon_ms']}")
    (run / "overload_summary.md").write_text("\n".join(L) + "\n")
    print("\n".join(L))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
