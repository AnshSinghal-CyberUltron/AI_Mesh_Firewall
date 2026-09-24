#!/usr/bin/env python3
"""proto-bench-fleet post-processor for one step (multi-unit, edge, shared Redis).

Client-side classification is proto-bench-unit's `classify()` (copied verbatim to
pbu_analyze_copy.py, sha256 96c1773a...), which imports the harness analyze.py (c9f89fe8) for loading,
joining, per-request metrics and nearest-rank percentiles, and applies the CONTROLLER rule:
  qualified   = 200 complete, one provider call, content sha equal, unique nonce, all stages :E,
                disposition ALLOW|REDACT|FLAG
  policy BLOCK stratum = 400/403/422/451 + x-rv-disposition BLOCK + zero provider calls (benign FP on
                the HEADLINE corpus); a BLOCK whose stages show a detector UNAVAILABLE is an infra error
  infra error = everything else (5xx incl. 503 sheds, incomplete, timeouts, degraded 200s)
  strict PASS = p99 T_fw_addon(qualified) < 20 ms AND infra <= 0.1% of offered AND 0 drops AND
                0 safety failures AND run valid
  load-knee PASS (labelled, NOT the controller rule) = same with T_addon_total instead of T_fw_addon
                (excludes the structural pattern-aware SSE holdback, which is load-independent)
This file adds: SUT side summed over every unit under test (CPU by role from exact schedstat runtime,
GPU dmon, rvproto histogram/counter deltas merged across units), the nginx edge (CPU, NIC), the
shared Redis (ops/s, server usec/call, CPU, PING RTT from a unit), and bytes/request per hop from
iptables ACCT counters (IP-layer bytes incl. headers and ACKs) over the measurement window.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import pbu_analyze_copy as P  # noqa: E402

A = P.A
IPS = {  # asia-northeast1-a lane VMs
    "10.146.0.10": "edge", "10.146.0.13": "redis", "10.146.0.8": "prov-1", "10.146.0.6": "prov-2",
    "10.146.0.11": "lg-1", "10.146.0.12": "lg-2", "10.146.0.9": "lg-3",
    "10.146.0.2": "unit-2", "10.146.0.5": "unit-3", "10.146.0.3": "unit-4", "10.146.0.4": "unit-5",
    "10.146.0.7": "unit-6", "10.146.0.14": "unit-7",
    "10.146.0.15": "redis2", "10.146.0.16": "prov-3", "10.146.0.17": "lg-4",
    "10.146.0.20": "edge2", "10.146.0.18": "unit-8", "10.146.0.19": "unit-9",
}


def acct(path: Path) -> dict[str, tuple[int, int]]:
    """iptables -L ACCT -v -x -n -> {comment: (pkts, bytes)}"""
    out = {}
    if not path.exists():
        return out
    for line in path.read_text().splitlines():
        m = re.match(r"\s*(\d+)\s+(\d+)\s+.*/\*\s*(\S+)\s*\*/", line)
        if m:
            out[m.group(3)] = (int(m.group(1)), int(m.group(2)))
    return out


def acct_delta(sut: Path) -> dict[str, dict[str, int]]:
    a, b = acct(sut / "snap-meas_start" / "iptables_acct.txt"), acct(sut / "snap-meas_end" / "iptables_acct.txt")
    out = {}
    for k, (pk, by) in b.items():
        pa, ba = a.get(k, (0, 0))
        d, ip = k.split("_", 1)
        out[f"{d}_{IPS.get(ip, ip)}"] = {"pkts": pk - pa, "bytes": by - ba}
    return out


def redis_info(p: Path) -> dict[str, str]:
    out = {}
    if p.exists():
        for line in p.read_text().splitlines():
            if ":" in line and not line.startswith("#"):
                k, v = line.split(":", 1)
                out[k.strip()] = v.strip()
    return out


def window_samples(sut: Path) -> tuple[dict, list]:
    sched = json.loads((sut / "schedule.json").read_text())
    f = A.find_raw(sut, "samples.jsonl")
    samples = A.read_jsonl(f) if f is not None else []
    t0, t1 = sched["meas_start"], sched["meas_end"]
    return sched, [s for s in samples if t0 - 1.0 <= s["t"] <= t1 + 1.0]


def cpu_by_role(win: list) -> dict:
    if len(win) < 2:
        return {}
    a, b = win[0], win[-1]
    dt = b["t"] - a["t"]
    role_ns: Counter = Counter()
    per_proc = defaultdict(list)
    restarted = []
    for pid, pb in b["procs"].items():
        pa = a["procs"].get(pid)
        if pa is None:
            restarted.append(pb["role"])
            continue
        role = "owner" if pb["role"].startswith("owner") else pb["role"]
        v = pb["run_ns"] - pa["run_ns"]
        role_ns[role] += v
        per_proc[role].append(round(v / 1e9 / dt, 3))
        if pb["role"].startswith("owner"):
            role_ns[pb["role"]] += v
    cores = []
    for cpu, vb in b["cpu"]["sched"].items():
        if cpu == "cpu":
            continue
        va = a["cpu"]["sched"].get(cpu)
        if va:
            cores.append(round((vb[0] - va[0]) / 1e9 / dt, 3))
    na = a["net"].get("ens4") or a["net"].get("eth0")
    nb = b["net"].get("ens4") or b["net"].get("eth0")
    nic = None
    if na and nb:
        nic = {"rx_mbit_s": round((nb["rx_bytes"] - na["rx_bytes"]) * 8 / 1e6 / dt, 1),
               "tx_mbit_s": round((nb["tx_bytes"] - na["tx_bytes"]) * 8 / 1e6 / dt, 1),
               "rx_pkts_s": round((nb["rx_pkts"] - na["rx_pkts"]) / dt), "tx_pkts_s": round((nb["tx_pkts"] - na["tx_pkts"]) / dt)}
    return {"window_s": round(dt, 2), "cores_by_role": {r: round(v / 1e9 / dt, 3) for r, v in sorted(role_ns.items())},
            "ns_by_role": dict(role_ns), "per_proc_util": {r: sorted(v) for r, v in per_proc.items()},
            "per_core_util": {"max": max(cores, default=None), "mean": round(sum(cores) / len(cores), 3) if cores else None,
                              "sum": round(sum(cores), 2), "n": len(cores)},
            "nic": nic, "restarted": restarted, "loadavg_max": max(float(s["load"][0]) for s in win)}


def gpu_dmon(sut: Path, sched: dict) -> dict:
    dm = sut / "dmon.txt"
    if not dm.exists():
        return {}
    import datetime as _dt
    cols = None
    per = defaultdict(list)
    for line in dm.read_text().splitlines():
        if line.startswith("#"):
            if "gpu" in line and cols is None:
                cols = line.lstrip("#").split()
            continue
        parts = line.split()
        if not cols or len(parts) != len(cols):
            continue
        row = dict(zip(cols, parts))
        try:
            hh, mm, ss = (int(x) for x in row["Time"].split(":"))
            day = _dt.datetime.fromtimestamp(sched["meas_start"], _dt.timezone.utc).replace(hour=hh, minute=mm, second=ss)
            if sched["meas_start"] <= day.timestamp() <= sched["meas_end"]:
                per[row["gpu"]].append(float(row["sm"]))
        except (KeyError, ValueError):
            continue
    return {g: {"n": len(v), "sm_mean": round(sum(v) / len(v), 1), "sm_p95": sorted(v)[int(0.95 * (len(v) - 1))],
                "sm_max": max(v)} for g, v in sorted(per.items()) if v}


def merge_hist(summaries: list[tuple[dict, dict]]) -> None:
    pass


def unit_side(run: Path, units: list[str], n_meas: int) -> dict:
    out: dict = {"per_unit": {}}
    wb_all, wa_all, ob_all, oa_all = [], [], [], []
    tot_ns: Counter = Counter()
    wall = None
    for u in units:
        sut = run / u / "sut"
        if not sut.exists():
            out["per_unit"][u] = {"missing": True}
            continue
        sched, win = window_samples(sut)
        c = cpu_by_role(win)
        wall = c.get("window_s") or wall
        for r in ("worker", "owner", "launcher", "redis"):
            tot_ns[r] += c.get("ns_by_role", {}).get(r, 0)
        s0, s1 = sut / "snap-meas_start", sut / "snap-meas_end"
        row = {"cpu": {k: v for k, v in c.items() if k != "ns_by_role"}, "gpu": gpu_dmon(sut, sched), "acct": acct_delta(sut)}
        if s0.exists() and s1.exists():
            wb, ob = P.load_metrics_dir(s0)
            wa, oa = P.load_metrics_dir(s1)
            # disambiguate worker indices across units by prefixing the unit
            for lst_in, lst_out in ((list(wb.values()), wb_all), (list(wa.values()), wa_all), (ob, ob_all), (oa, oa_all)):
                for x in lst_in:
                    y = dict(x)
                    y["worker"] = f"{u}:{x['worker']}"
                    lst_out.append(y)
            wh, wc, wn = P.hist_delta(list(wb.values()), list(wa.values()))
            row["worker_counts"] = {k: v for k, v in sorted(wc.items()) if v}
            row["t_input_p99_ms"] = (wh.get("t_input_ns") or {}).get("p99_ms")
            row["notes"] = wn
            per_w = []
            sheds_w = []
            for key, w in wa.items():
                b = wb.get(key)
                if b is None or b.get("pid") != w.get("pid"):
                    continue
                per_w.append(w["count"].get("admitted", 0) - b["count"].get("admitted", 0))
                sheds_w.append(sum(v - b["count"].get(k, 0) for k, v in w["count"].items() if k.startswith("shed{")))
            if per_w:
                mean = sum(per_w) / len(per_w)
                row["per_worker_admitted"] = {"n": len(per_w), "min": min(per_w), "max": max(per_w),
                                              "mean": round(mean, 1), "max_over_mean": round(max(per_w) / mean, 3) if mean else None,
                                              "sheds_per_worker": sorted(sheds_w)}
            g = {}
            for w in wa.values():
                for k, v in (w.get("gauge") or {}).items():
                    if k.startswith(("plan_version_info", "audit_completeness_ratio", "audit_dropped", "lease_held")):
                        g.setdefault(k, []).append(v)
            row["gauges_end"] = {k: (min(v), max(v), len(v)) for k, v in g.items()}
        out["per_unit"][u] = row
    if wa_all:
        wh, wc, wn = P.hist_delta(wb_all, wa_all)
        oh, oc, on = P.hist_delta(ob_all, oa_all)
        out["worker_hist"] = wh
        out["worker_counts"] = {k: v for k, v in sorted(wc.items()) if v}
        out["owner_hist"] = oh
        out["owner_counts"] = {k: v for k, v in sorted(oc.items()) if v}
        out["notes"] = wn + on
    if wall and n_meas:
        gw = tot_ns["worker"] + tot_ns["owner"] + tot_ns["launcher"]
        out["gateway_cores_total"] = round(gw / 1e9 / wall, 2)
        out["cpu_ms_per_req"] = {"gateway": round(gw / 1e6 / n_meas, 3), "workers": round(tot_ns["worker"] / 1e6 / n_meas, 3),
                                 "owners": round(tot_ns["owner"] / 1e6 / n_meas, 3)}
    return out


def edge_side(run: Path, n_meas: int) -> dict | None:
    sut = run / "rv-pbf-edge-1" / "sut"
    if not sut.exists():
        sut = run / "rv-pbf-edge-2" / "sut"
    if not sut.exists():
        return None
    sched, win = window_samples(sut)
    c = cpu_by_role(win)
    ad = acct_delta(sut)
    return {"cpu": {k: v for k, v in c.items() if k != "ns_by_role"},
            "nginx_cpu_ms_per_req": round(c.get("ns_by_role", {}).get("nginx", 0) / 1e6 / n_meas, 3) if n_meas else None,
            "acct": ad}


def redis_side(run: Path, n_meas: int, units: list[str]) -> dict | None:
    sut = run / "rv-pbf-redis-1" / "sut"
    if not sut.exists():
        sut = run / "rv-pbf-redis-2" / "sut"
    if not sut.exists():
        return None
    sched, win = window_samples(sut)
    c = cpu_by_role(win)
    i0, i1 = redis_info(sut / "snap-meas_start" / "redis_info.txt"), redis_info(sut / "snap-meas_end" / "redis_info.txt")
    dt = sched["meas_end"] - sched["meas_start"]
    res = {"cpu": {k: v for k, v in c.items() if k != "ns_by_role"},
           "redis_cpu_cores": c.get("cores_by_role", {}).get("redis")}
    if i0 and i1:
        cmds = int(i1["total_commands_processed"]) - int(i0["total_commands_processed"])
        res["ops_per_s"] = round(cmds / dt, 1)
        res["ops_per_req"] = round(cmds / n_meas, 3) if n_meas else None
        res["net_in_kbps"] = round((int(i1["total_net_input_bytes"]) - int(i0["total_net_input_bytes"])) * 8 / 1e3 / dt, 1)
        res["net_out_kbps"] = round((int(i1["total_net_output_bytes"]) - int(i0["total_net_output_bytes"])) * 8 / 1e3 / dt, 1)
        res["connected_clients_end"] = int(i1.get("connected_clients", 0))
        res["used_memory_mb_end"] = round(int(i1.get("used_memory", 0)) / 2**20, 1)
        cs = {}
        for k, v in i1.items():
            if k.startswith("cmdstat_"):
                f1 = dict(x.split("=") for x in v.split(","))
                f0 = dict(x.split("=") for x in i0.get(k, "calls=0,usec=0").split(",")) if k in i0 else {"calls": "0", "usec": "0"}
                calls = int(f1["calls"]) - int(f0.get("calls", 0))
                usec = int(f1["usec"]) - int(f0.get("usec", 0))
                if calls:
                    cs[k[8:]] = {"calls_per_s": round(calls / dt, 1), "usec_per_call": round(usec / calls, 2)}
        res["commandstats"] = cs
        for k in ("instantaneous_ops_per_sec", "used_cpu_sys", "used_cpu_user"):
            res[k + "_end"] = i1.get(k)
    for u in units:
        pj = run / u / "sut" / "redis_ping.json"
        if pj.exists():
            d = json.loads(pj.read_text())
            res["ping_rtt_from"] = u
            res["ping_rtt_us"] = d["summary"]
            ps = d.get("per_second") or []
            if ps:
                res["ping_rtt_per_second_p99_max_us"] = round(max(r["p99"] for r in ps), 1)
    return res


def wire_bytes(run: Path, s: dict, units: list[str], edge: dict | None) -> dict:
    """Bytes per measured request per hop, from iptables ACCT (IP-layer, incl. TCP/IP headers + ACKs)
    over the measurement window, plus olg response-body bytes (app layer)."""
    n = s["offered"]
    out = {"requests_in_window": n}
    tot = Counter()
    for u in units:
        ad = acct_delta(run / u / "sut")
        for k, v in ad.items():
            tot[k] += v["bytes"]
    unit_to_client = tot.get("out_edge", 0) + tot.get("out_edge2", 0) + sum(tot.get(f"out_lg-{i}", 0) for i in (1, 2, 3, 4))
    client_to_unit = tot.get("in_edge", 0) + tot.get("in_edge2", 0) + sum(tot.get(f"in_lg-{i}", 0) for i in (1, 2, 3, 4))
    to_prov = sum(tot.get(f"out_prov-{i}", 0) for i in (1, 2, 3))
    from_prov = sum(tot.get(f"in_prov-{i}", 0) for i in (1, 2, 3))
    out["unit_ip_bytes_per_req"] = {
        "unit_to_client_side": round(unit_to_client / n, 1), "client_side_to_unit": round(client_to_unit / n, 1),
        "unit_to_provider": round(to_prov / n, 1), "provider_to_unit": round(from_prov / n, 1),
        "unit_to_redis": round((tot.get("out_redis", 0) + tot.get("out_redis2", 0)) / n, 1),
        "redis_to_unit": round((tot.get("in_redis", 0) + tot.get("in_redis2", 0)) / n, 1)}
    if edge:
        ad = edge["acct"]
        e_to_c = sum(ad.get(f"out_lg-{i}", {}).get("bytes", 0) for i in (1, 2, 3, 4))
        c_to_e = sum(ad.get(f"in_lg-{i}", {}).get("bytes", 0) for i in (1, 2, 3, 4))
        out["edge_ip_bytes_per_req"] = {"edge_to_clients": round(e_to_c / n, 1), "clients_to_edge": round(c_to_e / n, 1)}
    out["olg_resp_body_bytes_mean"] = s.get("resp_body_bytes_mean")
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True)
    ap.add_argument("--mode", default="sut", choices=["sut", "direct"])
    ap.add_argument("--profile-stages", default="canon,det,sem,resolve,dispatch,out,audit")
    ap.add_argument("--corpus", default=None)
    ap.add_argument("--policy", default="none", choices=["none", "enforce"])
    ap.add_argument("--slo-ms", type=float, default=20.0)
    ap.add_argument("--err-budget", type=float, default=0.001)
    ap.add_argument("--drop-ms", type=float, default=5.0)
    a = ap.parse_args()
    run = Path(a.run)
    step = json.loads((run / "step.json").read_text()) if (run / "step.json").exists() else {}
    stages = [x for x in a.profile_stages.split(",") if x] if a.mode == "sut" else []
    s = P.classify(run, a.mode, stages, a.corpus, a.policy, a.slo_ms, a.err_budget, a.drop_ms)
    s.pop("_manifests", None)
    units = [u for u in (step.get("units") or "").split() if u] if a.mode == "sut" else []
    if a.mode == "sut":
        s["units_side"] = unit_side(run, units, s["offered"])
        s["edge_side"] = edge_side(run, s["offered"])
        s["redis_side"] = redis_side(run, s["offered"], units)
        s["wire"] = wire_bytes(run, s, units, s["edge_side"])
    s["step"] = step
    s["tools"] = {"pbf_analyze": "pbf_analyze.py", "classify": "pbu_analyze_copy.py (96c1773a)", "harness_analyze": "c9f89fe8"}
    (run / "pbf_summary.json").write_text(json.dumps(s, indent=1, default=str) + "\n")
    L = [f"# {run.name}: strict {'PASS' if s['pass'] else 'FAIL'} | load-knee {'PASS' if s.get('load_knee_pass') else 'FAIL'} "
         f"({a.mode}, units={len(units)}, rate={step.get('rate')})", "",
         f"checks: {s['checks']}", f"load-knee checks: {s.get('load_knee_checks')}",
         f"offered {s['offered']} ({s['offered_rps']}/s) qualified {s['qualified']} ({s['qualified_rps']}/s) "
         f"FP-blocks {s['policy_block_fp']} ({s['fp_rate']}) infra {s['infra_errors']} ({s['infra_error_rate']}) "
         f"drops {s['drops']} safety {s['safety_failures']}",
         f"infra reasons: {s['infra_reasons']}", f"infra detail: {s['infra_detail']}", ""]
    for k in ("T_fw_addon", "T_fw_addon_nohold", "T_fw_addon_sse", "T_fw_addon_json", "T_addon_first_sse",
              "T_addon_total_sse", "T_addon_total_json", "T_release_lag_max", "lateness"):
        L.append(f"{k}: {A.fmt(s[k])}")
    L.append(f"loadgen: {s['loadgen']}  provider_cpu_busy_max: {s.get('provider_cpu_busy_max')}")
    if a.mode == "sut":
        us = s["units_side"]
        L.append(f"gateway cores total {us.get('gateway_cores_total')} cpu-ms/req {us.get('cpu_ms_per_req')}")
        for u, row in us["per_unit"].items():
            c = row.get("cpu", {})
            L.append(f"  {u}: cores {c.get('cores_by_role')} worker util max {max(c.get('per_proc_util', {}).get('worker', [0]) or [0])} "
                     f"per-core max {c.get('per_core_util', {}).get('max')} mean {c.get('per_core_util', {}).get('mean')} "
                     f"gpu {row.get('gpu')} t_input_p99 {row.get('t_input_p99_ms')} "
                     f"per-worker admitted {row.get('per_worker_admitted')}")
        wh = us.get("worker_hist") or {}
        for k in ("t_input_ns", "t_tokenize_ns", "t_guard_wait_ns", "guard_owner_rtt_ns", "guard_queue_ns", "guard_exec_ns",
                  "t_admit_ns", "release_processing_ns", "loop_lag_ns", "dispatch_pool_wait_ns", "audit_batch_write_ns"):
            if k in wh:
                L.append(f"W {k}: {wh[k]}")
        wc = us.get("worker_counts") or {}
        L.append("W counts: " + json.dumps({k: v for k, v in wc.items() if not k.startswith(("lease_granted", "quota_admitted"))}))
        L.append(f"edge: {json.dumps(s['edge_side']['cpu'] if s['edge_side'] else None)[:600]} nginx cpu-ms/req "
                 f"{(s['edge_side'] or {}).get('nginx_cpu_ms_per_req')}")
        r = s["redis_side"] or {}
        L.append(f"redis: ops/s {r.get('ops_per_s')} ops/req {r.get('ops_per_req')} cpu cores {r.get('redis_cpu_cores')} "
                 f"clients {r.get('connected_clients_end')} mem {r.get('used_memory_mb_end')}MB ping(us) {r.get('ping_rtt_us')}")
        L.append(f"redis cmdstats: {r.get('commandstats')}")
        L.append(f"wire: {s['wire']}")
    (run / "pbf_summary.md").write_text("\n".join(L) + "\n")
    print("\n".join(L))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
