#!/usr/bin/env python3
"""Analyze a b_test.sh run (plan push + org kill switch, or quota) from raw files.

  b_analyze.py RUN_DIR

Clock: every write instant (redis_timed.py t_send/t_reply) was taken on rv-pbf-lg-1; request instants are
wall(send) = start_at_unix_ms/1000 + (sched_ns + late_ns)/1e9 on each loadgen's own clock (lg-2/lg-3
differ from lg-1 only by NTP sync error, reported by chrony on each VM). Per-worker gauges come from the
workers' own 1 s metric dumps (exported_at is the worker's wall clock).
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pbu_analyze_copy as P  # noqa: E402

A = P.A


def load_requests(run: Path) -> list[dict]:
    out = []
    for lg in sorted(run.glob("rv-pbf-lg-*/lg")):
        man = json.loads((lg / "manifest.json").read_text())
        t0 = man["config"]["start_at_unix_ms"] / 1000.0
        targets = man["config"]["targets"]
        f = A.find_raw(lg, "requests.jsonl")
        for c in A.read_jsonl(f):
            c["_lg"] = lg.parent.name
            c["_t_send"] = t0 + (c.get("sched_ns", 0) + c.get("late_ns", 0)) / 1e9
            c["_unit"] = targets[c.get("target", 0)].split("//")[1].split(":")[0]
            out.append(c)
    return out


def jl(p: Path) -> list[dict]:
    return [json.loads(x) for x in p.read_text().splitlines() if x.strip()] if p.exists() else []


def worker_series(run: Path) -> dict:
    ser = {}
    for f in sorted((run / "b").glob("gp-*.jsonl")):
        ip = f.stem[3:]
        for r in jl(f):
            ser.setdefault((ip, r["worker"]), []).append(r)
    for k in ser:
        ser[k].sort(key=lambda r: r["exported_at"] or 0)
    return ser


def plan_ks(run: Path, reqs: list[dict]) -> dict:
    b = run / "b"
    res = {}
    pw = next(iter(b.glob("b-*-plan.json")), None)
    if pw:
        w = json.loads(pw.read_text())
        t_pub, newver = w["t_send"], w["args"][1]
        res["plan_write"] = w
        per_unit = defaultdict(lambda: {"old_after": 0, "new": 0, "last_old_after_ms": None, "first_new_ms": None})
        for c in reqs:
            v = c.get("plan_ver")
            if not v:
                continue
            dt = (c["_t_send"] - t_pub) * 1000
            u = per_unit[c["_unit"]]
            if v != newver and dt > 0:
                u["old_after"] += 1
                u["last_old_after_ms"] = max(u["last_old_after_ms"] or dt, dt)
            if v == newver:
                u["new"] += 1
                u["first_new_ms"] = dt if u["first_new_ms"] is None else min(u["first_new_ms"], dt)
        res["plan_client_view"] = {k: {kk: (round(vv, 1) if isinstance(vv, float) else vv) for kk, vv in v.items()}
                                   for k, v in sorted(per_unit.items())}
        # per worker (gauge dumps): bracket [last dump still old, first dump new]
        brackets = []
        for (ip, wk), rows in worker_series(run).items():
            last_old = first_new = None
            for r in rows:
                has_new = any(f'org="org-a",version="{newver}"' in p for p in r["plan"])
                if has_new and first_new is None and (r["exported_at"] or 0) >= t_pub - 5:
                    first_new = r["exported_at"]
                if not has_new and (r["exported_at"] or 0) <= (first_new or 1e18):
                    last_old = r["exported_at"]
            brackets.append({"unit": ip, "worker": wk,
                             "last_old_dump_ms": round((last_old - t_pub) * 1000, 1) if last_old else None,
                             "first_new_dump_ms": round((first_new - t_pub) * 1000, 1) if first_new else None})
        fn = [x["first_new_dump_ms"] for x in brackets if x["first_new_dump_ms"] is not None]
        res["plan_worker_brackets_summary"] = {"workers": len(brackets), "workers_with_new": len(fn),
                                               "max_first_new_dump_ms": max(fn) if fn else None,
                                               "min_first_new_dump_ms": min(fn) if fn else None,
                                               "never_converged": [x for x in brackets if x["first_new_dump_ms"] is None]}
        res["plan_worker_brackets"] = brackets
    on = next(iter(b.glob("b-*-ks-on.json")), None)
    off = next(iter(b.glob("b-*-ks-off.json")), None)
    if on and off:
        won, woff = json.loads(on.read_text()), json.loads(off.read_text())
        t_on, t_off = won["t_send"], woff["t_send"]
        res["ks_writes"] = {"on": won, "off": woff}
        per_unit = defaultdict(lambda: {"served_after_on": 0, "last_served_after_on_ms": None, "first_ks_503_ms": None,
                                        "ks_503": 0, "first_served_after_off_ms": None, "last_ks_503_after_off_ms": None,
                                        "other_errors_in_window": 0})
        for c in reqs:
            if c["_t_send"] < t_on - 2 or c["_t_send"] > t_off + 5:
                continue
            u = per_unit[c["_unit"]]
            ks = c.get("status") == 503 and "kill_switch" in (c.get("err_detail") or "")
            ok = c.get("status") in (200, 403) and not ks
            d_on = (c["_t_send"] - t_on) * 1000
            d_off = (c["_t_send"] - t_off) * 1000
            if c["_t_send"] < t_off:
                if ok and d_on > 0:
                    u["served_after_on"] += 1
                    u["last_served_after_on_ms"] = max(u["last_served_after_on_ms"] or d_on, d_on)
                if ks:
                    u["ks_503"] += 1
                    u["first_ks_503_ms"] = d_on if u["first_ks_503_ms"] is None else min(u["first_ks_503_ms"], d_on)
            else:
                if ok:
                    u["first_served_after_off_ms"] = d_off if u["first_served_after_off_ms"] is None else min(u["first_served_after_off_ms"], d_off)
                if ks:
                    u["last_ks_503_after_off_ms"] = max(u["last_ks_503_after_off_ms"] or d_off, d_off)
            if not ok and not ks:
                u["other_errors_in_window"] += 1
        res["ks_client_view"] = {k: {kk: (round(vv, 1) if isinstance(vv, float) else vv) for kk, vv in v.items()}
                                 for k, v in sorted(per_unit.items())}
        eng = []
        for (ip, wk), rows in worker_series(run).items():
            first = next((r["exported_at"] for r in rows if r["ks"] and (r["exported_at"] or 0) >= t_on - 1), None)
            clear = next((r["exported_at"] for r in rows if not r["ks"] and (r["exported_at"] or 0) >= t_off), None)
            eng.append({"unit": ip, "worker": wk, "first_engaged_dump_ms": round((first - t_on) * 1000, 1) if first else None,
                        "first_clear_dump_after_off_ms": round((clear - t_off) * 1000, 1) if clear else None})
        fe = [x["first_engaged_dump_ms"] for x in eng if x["first_engaged_dump_ms"] is not None]
        fc = [x["first_clear_dump_after_off_ms"] for x in eng if x["first_clear_dump_after_off_ms"] is not None]
        res["ks_worker_summary"] = {"workers": len(eng), "engaged": len(fe), "max_first_engaged_dump_ms": max(fe) if fe else None,
                                    "cleared": len(fc), "max_first_clear_dump_ms": max(fc) if fc else None}
    return res


def quota(run: Path, reqs: list[dict]) -> dict:
    b = run / "b"
    res = {}
    bw = next(iter(b.glob("b-*-budget.json")), None)
    step = json.loads((run / "step.json").read_text())
    res["budget_write"] = json.loads(bw.read_text()) if bw else None
    budget = int(step.get("budget") or 0)
    admitted = granted = refills = rejected = 0
    held_end = 0
    for f in sorted(b.glob("metrics-all-*.json")):
        m = json.loads(f.read_text())
        cnt = m.get("count", {})
        admitted += cnt.get('quota_admitted_tokens{org="org-q"}', 0)
        granted += cnt.get('lease_granted_tokens{org="org-q"}', 0)
        rejected += cnt.get('quota_rejected{org="org-q"}', 0)
        refills += cnt.get("lease_refills", 0)
        for w in m.get("per_worker", []):
            held_end += (w.get("gauge") or {}).get('lease_held_tokens{org="org-q"}', 0)
    st = defaultdict(int)
    t429 = []
    ok_t = []
    for c in reqs:
        st[c.get("status")] += 1
        if c.get("status") == 429:
            t429.append(c["_t_send"])
        if c.get("status") == 200:
            ok_t.append(c["_t_send"])
    tb = (res["budget_write"] or {}).get("t_send")
    res.update({
        "budget_tokens": budget, "admitted_tokens": admitted, "lease_granted_tokens": granted,
        "overshoot_tokens": admitted - budget, "overshoot_pct": round(100 * (admitted - budget) / budget, 4) if budget else None,
        "stranded_tokens_held_by_workers_at_end": held_end, "lease_refills": refills, "quota_rejected_refills": rejected,
        "status_counts": dict(st),
        "first_429_after_budget_s": round(min(t429) - tb, 3) if t429 and tb else None,
        "last_200_send_after_budget_s": round(max(ok_t) - tb, 3) if ok_t and tb else None,
        "n_200_after_first_429": sum(1 for t in ok_t if t429 and t > min(t429)),
    })
    return res


def main() -> int:
    run = Path(sys.argv[1])
    step = json.loads((run / "step.json").read_text())
    reqs = load_requests(run)
    out = {"run": run.name, "kind": step.get("kind"), "rate": step.get("rate"), "requests": len(reqs),
           "status_counts": {}}
    sc = defaultdict(int)
    for c in reqs:
        sc[f'{c.get("status")} {c.get("err_detail") or ""}'.strip()] += 1
    out["status_counts"] = dict(sorted(sc.items(), key=lambda x: -x[1])[:12])
    if step.get("kind") == "plan_ks":
        out.update(plan_ks(run, reqs))
    elif step.get("kind") == "quota":
        out.update(quota(run, reqs))
    (run / "b_summary.json").write_text(json.dumps(out, indent=1, default=str) + "\n")
    brief = {k: v for k, v in out.items() if k not in ("plan_worker_brackets",)}
    print(json.dumps(brief, indent=1, default=str)[:6000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
