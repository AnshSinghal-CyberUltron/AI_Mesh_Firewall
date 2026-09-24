#!/usr/bin/env python3
"""proto-bench-unit post-processor: recompute one step from raw files with the CONTROLLER's
qualification rule, plus SUT-side attribution (CPU, GPU, histograms, bytes on the wire).

Why this exists: harness analyze.py (c9f89fe8) counts every non-ALLOW/REDACT response as an error,
so a benign PG2 false-positive BLOCK (403, zero provider calls) is an "error" there. The controller
rule (runbook T01 "app_infra_error_rate <= 0.1%", §1.1 "policy blocks are correctness traffic
reported separately") is implemented here; analyze.py is imported for loading, joining, the
per-request metrics (T_addon_*, release lag) and percentiles, so those are computed identically.

Per request (measurement phase only):
  qualified   = analyze.evaluate() qualified (200, complete, one provider call, content sha equal,
                unique nonce, every --profile-stages stage :E) with disposition ALLOW|REDACT|FLAG.
  policy BLOCK stratum = 400/403/422/451 + x-rv-disposition BLOCK + ZERO provider calls and every
                stage that ran reported :E. Split into expected (corpus expect.input == BLOCK) and
                false-positive (anything else; for the benign HEADLINE/WORST corpora every block is FP).
  infra error = everything else: 5xx incl. 503 overload sheds, non-policy 4xx, transport errors,
                timeouts, incomplete streams, degraded 200s (a stage not :E, content mismatch), and a
                BLOCK whose stages show a detector UNAVAILABLE (fail-closed on an outage is an infra
                failure presenting as a block, not a policy decision).
Step PASS iff p99 T_fw_addon(qualified) < --slo-ms AND infra errors <= --err-budget of offered AND
  0 schedule drops AND 0 safety failures AND run valid (all scheduled recorded, no negative addons).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from types import SimpleNamespace

HARNESS = Path("/tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/"
               "1b4d4994-cf08-4d34-a6cb-54fd36f36877/scratchpad/harness")
sys.path.insert(0, str(Path(__file__).resolve().parent))  # proto-bench-fleet: pinned harness analyzer
import analyze_c9f89fe8 as A  # noqa: E402  (git fc5c983 analyze.py, sha256 c9f89fe8..., the controller-approved build)

SUB = 6
_HALF = 1 << SUB
_MASK = _HALF - 1


def bucket_value(idx: int) -> float:  # mirrors rvproto/runtime/metrics.py
    if idx < _HALF:
        return float(idx)
    shift = (idx >> SUB) - 1
    lo = (_HALF | (idx & _MASK)) << shift
    return lo + ((1 << shift) - 1) / 2.0


def stages_of(s: str | None) -> dict[str, str]:
    out = {}
    for part in (s or "").split(","):
        if ":" in part:
            k, v = part.split(":", 1)
            out[k.strip()] = v.strip()
    return out


# ------------------------------------------------------------------------------------------------
# client/provider classification
# ------------------------------------------------------------------------------------------------
CANARY_SAFETY = ("canary_reached_provider", "output_canary_reached_client")


def categorize(c: dict, p: dict | None, calls: int, args: SimpleNamespace, exp: dict, stages_req: list[str],
               dup: bool, mode: str, policy: str) -> tuple[str, dict, list[str], list[str]]:
    """One request -> (category, metrics, reasons, safety). Categories:
    qualified | policy_block_fp | policy_block_expected | detection_miss | safety_failure | infra_error."""
    outcome, reasons, m, saf = A.evaluate(c, p, calls, args, exp, stages_req, dup)
    status = c.get("status", 0)
    disp = (c.get("disp") or "").upper()
    st = stages_of(c.get("stages"))
    cls = c.get("cls") or "?"
    e_in = "ALLOW"
    ce = exp.get(c.get("cid"))
    if ce:
        e_in = ce[0].get("input", A.CLASS_DEFAULT.get(cls, "ALLOW"))
    elif policy == "enforce":
        e_in = A.CLASS_DEFAULT.get(cls, "ALLOW")
    canary = [x for x in saf if x.startswith(CANARY_SAFETY)]
    if canary:
        return "safety_failure", m, reasons, saf
    if mode == "sut" and status in A.BLOCK_STATUSES and disp == "BLOCK" and p is None:
        unavailable = [k for k, v in st.items() if v == "U"]
        if unavailable:  # fail-closed on an outage: infra failure presenting as a block
            return "infra_error", m, ["block_on_unavailable_" + "+".join(unavailable)], saf
        return ("policy_block_expected" if e_in == "BLOCK" else "policy_block_fp"), m, reasons, saf
    if outcome == "qualified":
        return "qualified", m, reasons, saf
    if outcome == "expected_block":
        return "policy_block_expected", m, reasons, saf
    if outcome == "error" and set(reasons) == {"disposition_FLAG"}:
        return "qualified", m, reasons, saf  # FLAG is a dispatching disposition (controller rule)
    miss = {"expected_block_not_blocked"} | {r for r in reasons if r.startswith("redact_expected_disposition_")}
    if policy == "enforce" and status == 200 and p is not None and reasons and set(reasons) <= miss | {"disposition_FLAG"}:
        return "detection_miss", m, reasons, saf  # detector recall, not a pipeline leak (no canary moved)
    return "infra_error", m, reasons, saf


def classify(run: Path, mode: str, stages_req: list[str], corpus: str | None, policy: str, slo_ms: float,
             err_budget: float, drop_ms: float) -> dict:
    lg_dirs = sorted(p for p in run.glob("*/lg") if p.is_dir())
    prov_dirs = sorted(p for p in run.glob("*/prov") if p.is_dir())
    prov = A.Provider()
    prov.load([str(p) for p in prov_dirs])
    exp = A.load_expectations(corpus) if corpus else {}
    args = SimpleNamespace(policy=policy, mode="sut" if mode == "sut" else "direct")
    files = A.expand([str(p) for p in lg_dirs], "requests.jsonl")
    nonce = Counter()
    total = 0
    for f in files:
        with A.open_any(f) as fh:
            for line in fh:
                if line.strip():
                    nonce[A.loads(line).get("nonce")] += 1
                    total += 1
    cat = Counter()
    infra_reasons = Counter()
    infra_detail = Counter()
    block_detail = Counter()
    by_class = defaultdict(Counter)
    safety = Counter()
    misses = Counter()
    miss_ex: list = []
    safety_ex: list = []
    err_ex: list = []
    neg = Counter()
    late, fw, fw_sse, fw_json, tot_sse, tot_json, first_sse, lag, ttft = ([] for _ in range(9))
    resp_bytes_sse, resp_bytes_json = [], []
    # "load" metric (labelled, NOT the controller rule): T_addon_total for every qualified request.
    # It holds the input phase (auth, detect, guard, dispatch) and the end-of-stream flush but no
    # mid-stream holdback; T_addon_first is not used because a stream whose first token is '.'
    # is itself held one ITL by the pattern-aware holdback (structural, load-independent).
    fw_nohold = []
    drops = 0
    offered = 0
    phase_counts = Counter()
    per_sec = defaultdict(lambda: Counter())
    unmappable = 0
    for f in files:
        with A.open_any(f) as fh:
            for line in fh:
                if not line.strip():
                    continue
                c = A.loads(line)
                ph = c.get("ph", 2)
                phase_counts[ph] += 1
                if ph != 2:
                    continue
                offered += 1
                late.append(c.get("late_ns", 0))
                if c.get("late_ns", 0) > drop_ms * 1e6 or c.get("err") == "inflight_cap":
                    drops += 1
                rid = c["rid"]
                p = prov.by_rid.get(rid)
                calls = prov.calls.get(rid, 0)
                if p is None and c.get("nonce") in prov.nonce_to_rid:
                    prid = prov.nonce_to_rid[c["nonce"]]
                    p, calls = prov.by_rid.get(prid), prov.calls.get(prid, 0)
                k, m, reasons, saf = categorize(c, p, calls, args, exp, stages_req, nonce[c.get("nonce")] > 1,
                                                mode, policy)
                status = c.get("status", 0)
                cls = c.get("cls") or "?"
                for s_ in saf:
                    kind = s_.split(":")[0]
                    if kind.startswith(CANARY_SAFETY):
                        safety[kind] += 1
                        if len(safety_ex) < 20:
                            safety_ex.append({"rid": rid, "cls": cls, "cid": c.get("cid"), "safety": s_})
                    else:
                        misses[kind] += 1
                for kk in ("addon_total", "addon_first", "lag_piece"):
                    if m.get(kk) is not None and m[kk] < 0:
                        neg[kk] += 1
                if k.startswith("policy_block"):
                    block_detail[c.get("err_detail") or ""] += 1
                if k == "infra_error":
                    for r in reasons:
                        infra_reasons[r.split(":")[0]] += 1
                    infra_detail[f"{status} {c.get('err') or ''} {c.get('err_detail') or ''}".strip()] += 1
                    if len(err_ex) < 25:
                        err_ex.append({"rid": rid, "status": status, "reasons": reasons, "err": c.get("err"),
                                       "err_detail": c.get("err_detail"), "disp": c.get("disp"),
                                       "stages": c.get("stages"), "stream": c.get("stream")})
                if k == "detection_miss" and len(miss_ex) < 20:
                    miss_ex.append({"rid": rid, "cls": cls, "cid": c.get("cid"), "reasons": reasons, "disp": c.get("disp")})
                cat[k] += 1
                by_class[cls][k] += 1
                sec = int(c.get("sched_ns", 0) // 1_000_000_000)
                per_sec[sec][k] += 1
                if m.get("lag_unmappable"):
                    unmappable += 1
                if k == "qualified" and "fw_addon" in m:
                    fw.append(m["fw_addon"])
                    fw_nohold.append(m["addon_total"])
                    if c.get("stream"):
                        fw_sse.append(m["fw_addon"])
                        tot_sse.append(m["addon_total"])
                        if "addon_first" in m:
                            first_sse.append(m["addon_first"])
                        if m.get("lag_piece") is not None:
                            lag.append(m["lag_piece"])
                        if c.get("first_ns"):
                            ttft.append(c["first_ns"])
                        resp_bytes_sse.append(c.get("bytes") or 0)
                    else:
                        fw_json.append(m["fw_addon"])
                        tot_json.append(m["addon_total"])
                        resp_bytes_json.append(c.get("bytes") or 0)
    manifests = []
    valid_reasons = [f"negative_{k}={v}" for k, v in neg.items()]
    lg_cpu = []
    measure_s = None
    for d in lg_dirs:
        mf = d / "manifest.json"
        if not mf.exists():
            valid_reasons.append(f"missing_manifest:{d}")
            continue
        man = json.loads(mf.read_text())
        manifests.append(man)
        cnt = man.get("counts", {})
        if cnt.get("scheduled") != cnt.get("recorded"):
            valid_reasons.append(f"records_incomplete:{d}")
        if man.get("interrupted"):
            valid_reasons.append(f"interrupted:{d}")
        conf = man.get("config", {})
        measure_s = conf.get("duration_s")
        t0 = (man.get("epoch_offset_s") or 0) + (conf.get("ramp_s") or 0) + (conf.get("warmup_s") or 0)
        cf = A.find_raw(d, "cpu.jsonl")
        busy = [s["busy"] for s in (A.read_jsonl(cf) if cf else []) if t0 <= s["t"] <= t0 + (conf.get("duration_s") or 0)]
        lg_cpu.append({"vm": d.parent.name, "busy_max": round(max(busy), 1) if busy else None,
                       "busy_mean": round(sum(busy) / len(busy), 1) if busy else None,
                       "late_max_us": (man.get("lateness_us_all_phases") or {}).get("max"),
                       "conn_opens": cnt.get("conn_opens"), "max_inflight": cnt.get("max_inflight")})
    infra = cat["infra_error"]
    d_fw = A.dist_ms(fw)
    p99 = d_fw.get("p99")
    ierr = infra / offered if offered else None
    checks = {
        "p99_T_fw_addon_lt_slo": p99 is not None and p99 < slo_ms,
        "infra_error_rate_le_budget": ierr is not None and ierr <= err_budget,
        "zero_schedule_drops": drops == 0,
        "zero_safety_failures": sum(safety.values()) == 0,
        "run_valid": not valid_reasons,
    }
    d_nh = A.dist_ms(fw_nohold)
    load_checks = {
        "p99_T_fw_addon_nohold_lt_slo": d_nh.get("p99") is not None and d_nh["p99"] < slo_ms,
        "infra_error_rate_le_budget": checks["infra_error_rate_le_budget"],
        "zero_schedule_drops": drops == 0,
        "zero_safety_failures": sum(safety.values()) == 0,
        "run_valid": not valid_reasons,
    }
    if mode == "direct":
        checks = {"zero_schedule_drops": drops == 0, "zero_errors": infra == 0, "run_valid": not valid_reasons,
                  "loadgen_cpu_lt_70": all((x["busy_max"] or 0) < 70 for x in lg_cpu)}
    psec = {str(k): dict(v) for k, v in sorted(per_sec.items())}
    return {
        "offered": offered, "measure_s": measure_s,
        "offered_rps": round(offered / measure_s, 2) if measure_s else None,
        "achieved_rps": round((cat["qualified"] + cat["policy_block_fp"] + cat["policy_block_expected"]) / measure_s, 2) if measure_s else None,
        "qualified": cat["qualified"],
        "qualified_rps": round(cat["qualified"] / measure_s, 2) if measure_s else None,
        "policy_block_fp": cat["policy_block_fp"], "policy_block_expected": cat["policy_block_expected"],
        "fp_rate": round(cat["policy_block_fp"] / offered, 5) if offered else None,
        "infra_errors": infra, "infra_error_rate": ierr,
        "infra_reasons": dict(infra_reasons.most_common()), "infra_detail": dict(infra_detail.most_common(20)),
        "block_detail": dict(block_detail.most_common(10)),
        "infra_examples": err_ex,
        "safety_failures": sum(safety.values()), "safety_reasons": dict(safety), "safety_examples": safety_ex,
        "detection_misses": cat["detection_miss"], "detection_miss_reasons": dict(misses), "detection_miss_examples": miss_ex,
        "by_class": {k: dict(v) for k, v in by_class.items()},
        "drops": drops, "lateness": A.dist_ms(late),
        "T_fw_addon": d_fw, "T_fw_addon_sse": A.dist_ms(fw_sse), "T_fw_addon_json": A.dist_ms(fw_json),
        "T_addon_total_sse": A.dist_ms(tot_sse), "T_addon_total_json": A.dist_ms(tot_json),
        "T_addon_first_sse": A.dist_ms(first_sse), "T_release_lag_max": A.dist_ms(lag),
        "release_lag_unmappable": unmappable, "client_ttft_sse": A.dist_ms(ttft),
        "resp_body_bytes_mean": {"sse": round(sum(resp_bytes_sse) / len(resp_bytes_sse), 1) if resp_bytes_sse else None,
                                 "json": round(sum(resp_bytes_json) / len(resp_bytes_json), 1) if resp_bytes_json else None},
        "loadgen": lg_cpu, "provider_records": prov.n,
        "provider_cpu_busy_max": max((s["busy"] for s in prov.cpu), default=None),
        "phase_counts": {str(k): v for k, v in phase_counts.items()},
        "valid": not valid_reasons, "invalid_reasons": valid_reasons,
        "checks": checks, "pass": all(checks.values()),
        "T_fw_addon_nohold": d_nh, "load_knee_checks": load_checks, "load_knee_pass": all(load_checks.values()),
        "per_second": psec,
        "_manifests": manifests,
    }


# ------------------------------------------------------------------------------------------------
# wire bytes (nstat / netdev deltas per VM)
# ------------------------------------------------------------------------------------------------
def nstat_map(p: Path) -> dict[str, int]:
    out = {}
    if not p.exists():
        return out
    for line in p.read_text().splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[1].isdigit():
            out[parts[0]] = int(parts[1])
    return out


def netdev_map(p: Path) -> dict[str, tuple[int, int]]:
    out = {}
    if not p.exists():
        return out
    for line in p.read_text().splitlines()[2:]:
        name, rest = line.split(":", 1)
        v = rest.split()
        out[name.strip()] = (int(v[0]), int(v[8]))
    return out


def wire(run: Path, lg_records: int, prov_records: int) -> dict:
    res = {}
    for role, pat, n in (("loadgens", "rv-pbu-lg-*", lg_records), ("provider", "rv-pbu-prov-*", prov_records)):
        ip_in = ip_out = nd_rx = nd_tx = 0
        for d in run.glob(pat):
            b, a = nstat_map(d / "nstat.before"), nstat_map(d / "nstat.after")
            ip_in += a.get("IpExtInOctets", 0) - b.get("IpExtInOctets", 0)
            ip_out += a.get("IpExtOutOctets", 0) - b.get("IpExtOutOctets", 0)
            nb, na = netdev_map(d / "netdev.before"), netdev_map(d / "netdev.after")
            for dev in ("ens4", "eth0"):
                if dev in na and dev in nb:
                    nd_rx += na[dev][0] - nb[dev][0]
                    nd_tx += na[dev][1] - nb[dev][1]
        res[role] = {"requests_all_phases": n, "ip_in_octets": ip_in, "ip_out_octets": ip_out,
                     "nic_rx_bytes": nd_rx, "nic_tx_bytes": nd_tx,
                     "ip_in_per_req": round(ip_in / n, 1) if n else None,
                     "ip_out_per_req": round(ip_out / n, 1) if n else None}
    lg = res["loadgens"]
    pv = res["provider"]
    res["per_client_request"] = {
        "client_to_gw": lg["ip_out_per_req"], "gw_to_client": lg["ip_in_per_req"],
        "gw_to_provider": round(pv["ip_in_octets"] / lg_records, 1) if lg_records else None,
        "provider_to_gw": round(pv["ip_out_octets"] / lg_records, 1) if lg_records else None,
    }
    return res


# ------------------------------------------------------------------------------------------------
# SUT side (unit sampler + metrics snapshots)
# ------------------------------------------------------------------------------------------------
def load_metrics_dir(d: Path) -> tuple[dict, list[dict]]:
    workers = [json.loads(p.read_text()) for p in sorted(d.glob("worker-*.json"))]
    owners = [json.loads(p.read_text()) for p in sorted(d.glob("owner-*.json"))]
    return {w["worker"]: w for w in workers}, owners


def hist_delta(before: list[dict], after: list[dict], same_pid: bool = True) -> tuple[dict, dict, list[str]]:
    """Merged (after - before) histograms and counters over matching processes (by index+pid)."""
    notes = []
    hist: dict[str, Counter] = defaultdict(Counter)
    hsum: Counter = Counter()
    hn: Counter = Counter()
    hmax: dict[str, int] = {}
    cnt: Counter = Counter()
    bmap = {(x["worker"], x["pid"]): x for x in before}
    for a in after:
        b = bmap.get((a["worker"], a["pid"]))
        if b is None:
            notes.append(f"process {a['worker']} pid {a['pid']} has no pre-window snapshot (restart?) -> counted whole")
            b = {"hist": {}, "count": {}}
        for name, h in a["hist"].items():
            hb = b["hist"].get(name, {"b": {}, "n": 0, "sum": 0})
            for k, v in h["b"].items():
                dv = v - hb["b"].get(k, 0)
                if dv:
                    hist[name][int(k)] += dv
            hn[name] += h["n"] - hb["n"]
            hsum[name] += h["sum"] - hb["sum"]
            hmax[name] = max(hmax.get(name, 0), h["max"])  # cumulative max (not windowed)
        for name, v in a["count"].items():
            cnt[name] += v - b["count"].get(name, 0)
    summ = {}
    for name, buckets in hist.items():
        n = hn[name]
        if n <= 0:
            continue
        items = sorted(buckets.items())
        row = {"n": n, "mean_ms": round(hsum[name] / n / 1e6, 4)}
        for q in (0.5, 0.9, 0.99, 0.999):
            rank, acc, val = q * n, 0, None
            for k, c in items:
                acc += c
                if acc >= rank:
                    val = bucket_value(k)
                    break
            row[f"p{q * 100:g}_ms"] = round((val or 0) / 1e6, 4)
        row["max_cum_ms"] = round(hmax[name] / 1e6, 4)
        summ[name] = row
    return summ, dict(cnt), notes


def sut_side(run: Path, n_meas: int) -> dict | None:
    u = run / "rv-proto-unit-1" / "unit"
    if not u.exists():
        return None
    sched = json.loads((u / "schedule.json").read_text())
    t0, t1 = sched["meas_start"], sched["meas_end"]
    samples = []
    f = A.find_raw(u, "samples.jsonl")
    if f is not None:
        samples = A.read_jsonl(f)
    win = [s for s in samples if t0 - 1.0 <= s["t"] <= t1 + 1.0]
    out: dict = {"schedule": sched, "samples_in_window": len(win)}
    if len(win) >= 2:
        a, b = win[0], win[-1]
        dt = b["t"] - a["t"]
        # per-role CPU (exact runtime ns over all threads) for processes present at both ends
        role_ns: Counter = Counter()
        role_ticks: Counter = Counter()
        restarted = []
        for pid, pb in b["procs"].items():
            pa = a["procs"].get(pid)
            if pa is None:
                restarted.append(pb["role"])
                continue
            role = re.sub(r"\d+$", "", pb["role"]) if pb["role"].startswith("owner") else pb["role"]
            role_ns[role] += pb["run_ns"] - pa["run_ns"]
            role_ticks[role] += pb["ticks"] - pa["ticks"]
            if pb["role"].startswith("owner"):
                role_ns[pb["role"]] += pb["run_ns"] - pa["run_ns"]
        hz = sched.get("hz", 100)
        gw_roles = ("worker", "owner", "launcher")
        gw_ns = sum(role_ns[r] for r in gw_roles)
        out["window_s"] = round(dt, 2)
        out["cpu_cores_by_role"] = {r: round(v / 1e9 / dt, 3) for r, v in sorted(role_ns.items())}
        out["cpu_cores_by_role_ticks"] = {r: round(v / hz / dt, 3) for r, v in sorted(role_ticks.items())}
        out["gateway_cores"] = round(gw_ns / 1e9 / dt, 3)
        out["gateway_cpu_ms_per_req"] = round(gw_ns / 1e6 / n_meas, 3) if n_meas else None
        out["worker_cpu_ms_per_req"] = round(role_ns["worker"] / 1e6 / n_meas, 3) if n_meas else None
        out["owner_cpu_ms_per_req"] = round(role_ns["owner"] / 1e6 / n_meas, 3) if n_meas else None
        out["redis_cpu_ms_per_req"] = round(role_ns["redis"] / 1e6 / n_meas, 3) if n_meas else None
        out["triton_cpu_ms_per_req"] = round(role_ns["triton"] / 1e6 / n_meas, 3) if role_ns.get("triton") and n_meas else None
        out["processes_without_window_start"] = restarted
        # per-worker CPU utilisation (busiest worker = single-thread event-loop saturation signal)
        wutil = []
        for pid, pb in b["procs"].items():
            pa = a["procs"].get(pid)
            if pa is not None and pb["role"] == "worker":
                wutil.append(round((pb["run_ns"] - pa["run_ns"]) / 1e9 / dt, 3))
        wutil.sort()
        out["worker_util"] = {"n": len(wutil), "min": wutil[0] if wutil else None,
                              "median": wutil[len(wutil) // 2] if wutil else None, "max": wutil[-1] if wutil else None}
        # per-core utilisation from /proc/schedstat (task run ns) and /proc/stat (tick-sampled)
        cores_s, cores_t = [], []
        for cpu, vb in b["cpu"]["sched"].items():
            if cpu == "cpu":
                continue
            va = a["cpu"]["sched"].get(cpu)
            if va:
                cores_s.append(round((vb[0] - va[0]) / 1e9 / dt, 3))
        for cpu, vb in b["cpu"]["stat"].items():
            if cpu == "cpu":
                continue
            va = a["cpu"]["stat"].get(cpu)
            if va:
                tot = sum(vb) - sum(va)
                idle = (vb[3] + vb[4]) - (va[3] + va[4])
                cores_t.append(round(1 - idle / tot, 3) if tot else None)
        out["per_core_util_schedstat"] = {"max": max(cores_s, default=None), "mean": round(sum(cores_s) / len(cores_s), 3) if cores_s else None,
                                          "sum_cores": round(sum(cores_s), 2), "values": cores_s}
        out["per_core_util_procstat"] = {"max": max((c for c in cores_t if c is not None), default=None),
                                         "mean": round(sum(c for c in cores_t if c is not None) / len(cores_t), 3) if cores_t else None,
                                         "values": cores_t}
        # memory / fds over the window
        rss: dict[str, int] = defaultdict(int)
        rss_max_proc: dict[str, int] = defaultdict(int)
        fds: dict[str, int] = defaultdict(int)
        for s in win:
            tot = Counter()
            ftot = Counter()
            for pid, p in s["procs"].items():
                role = "owner" if p["role"].startswith("owner") else p["role"]
                tot[role] += p["rss_kb"]
                ftot[role] += max(p["fds"], 0)
                rss_max_proc[role] = max(rss_max_proc[role], p["rss_kb"])
            for r, v in tot.items():
                rss[r] = max(rss[r], v)
            for r, v in ftot.items():
                fds[r] = max(fds[r], v)
        out["rss_mb_max_total_by_role"] = {r: round(v / 1024, 1) for r, v in rss.items()}
        out["rss_mb_max_single_proc_by_role"] = {r: round(v / 1024, 1) for r, v in rss_max_proc.items()}
        out["fds_max_total_by_role"] = dict(fds)
        # unit NIC (both hops together)
        na, nb = a["net"].get("ens4") or a["net"].get("eth0"), b["net"].get("ens4") or b["net"].get("eth0")
        if na and nb:
            out["unit_nic"] = {"rx_mbit_s": round((nb["rx_bytes"] - na["rx_bytes"]) * 8 / 1e6 / dt, 2),
                               "tx_mbit_s": round((nb["tx_bytes"] - na["tx_bytes"]) * 8 / 1e6 / dt, 2),
                               "rx_bytes_per_req": round((nb["rx_bytes"] - na["rx_bytes"]) / n_meas, 1) if n_meas else None,
                               "tx_bytes_per_req": round((nb["tx_bytes"] - na["tx_bytes"]) / n_meas, 1) if n_meas else None}
        out["loadavg_max"] = max(float(s["load"][0]) for s in win)
        gw = [s["gauges"] for s in win if s.get("gauges")]
        if gw:
            keys = sorted({k for g in gw for k in g})
            out["gauges_window"] = {k: {"max": max(g.get(k, 0.0) for g in gw),
                                        "mean": round(sum(g.get(k, 0.0) for g in gw) / len(gw), 2)} for k in keys}
        # time series (10 s) for plateau checks: gateway cores, worker RSS total, fds, active streams later
        ts = []
        for s in samples[:: 10]:
            tot_rss = sum(p["rss_kb"] for p in s["procs"].values() if p["role"] in ("worker", "launcher") or p["role"].startswith("owner"))
            tot_fd = sum(max(p["fds"], 0) for p in s["procs"].values() if p["role"] in ("worker", "launcher") or p["role"].startswith("owner"))
            row = {"t": round(s["t"] - sched["start"], 1), "gw_rss_mb": round(tot_rss / 1024, 1), "gw_fds": tot_fd,
                   "tcp": s["net"].get("sockstat")}
            g = s.get("gauges") or {}
            if g:
                row["active_streams"] = g.get("w.active_streams.sum")
                row["inflight"] = g.get("w.inflight_requests.sum")
            ts.append(row)
        out["timeseries_10s"] = ts
    # GPU (nvidia-smi dmon -s um -o T): columns Time gpu sm mem enc dec jpg ofa fb bar1 ccpm
    dm = u / "dmon.txt"
    if dm.exists():
        cols = None
        per_gpu: dict[str, list[tuple[float, float, float]]] = defaultdict(list)
        date0 = sched["meas_start"]
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
            except (KeyError, ValueError):
                continue
            import datetime as _dt
            day = _dt.datetime.fromtimestamp(date0, _dt.timezone.utc).replace(hour=hh, minute=mm, second=ss)
            t = day.timestamp()
            if t0 <= t <= t1:
                try:
                    per_gpu[row["gpu"]].append((float(row["sm"]), float(row["mem"]), float(row.get("fb", 0))))
                except ValueError:
                    pass
        out["gpu"] = {g: {"samples": len(v), "sm_mean": round(sum(x[0] for x in v) / len(v), 1),
                          "sm_max": max(x[0] for x in v), "mem_mean": round(sum(x[1] for x in v) / len(v), 1),
                          "fb_mb_max": max(x[2] for x in v)} for g, v in sorted(per_gpu.items()) if v}
    # metrics snapshot deltas over the measurement window
    s0, s1 = u / "snap-meas_start", u / "snap-meas_end"
    if s0.exists() and s1.exists():
        wb, ob = load_metrics_dir(s0)
        wa, oa = load_metrics_dir(s1)
        wh, wc, wn = hist_delta(list(wb.values()), list(wa.values()))
        oh, oc, on = hist_delta(ob, oa)
        out["worker_hist"] = wh
        out["worker_counts"] = {k: v for k, v in sorted(wc.items()) if v}
        out["owner_hist"] = oh
        out["owner_counts"] = {k: v for k, v in sorted(oc.items()) if v}
        out["snapshot_notes"] = wn + on
        g = {}
        for w in wa.values():
            for k, v in (w.get("gauge") or {}).items():
                if k in ("inflight_cap", "input_queue_cap", "guard_queue_cap_tokens", "guard_tokens_per_s",
                         "audit_queue_bound", "audit_completeness_ratio", "audit_dropped"):
                    g.setdefault(k, set()).add(v)
        out["worker_gauges_at_end"] = {k: sorted(v) for k, v in g.items()}
        out["owner_gauges_at_end"] = [o.get("gauge") for o in oa]
    else:
        out["snapshot_notes"] = ["missing meas_start/meas_end snapshots"]
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
    stages = [s for s in a.profile_stages.split(",") if s] if a.mode == "sut" else []
    s = classify(run, a.mode, stages, a.corpus, a.policy, a.slo_ms, a.err_budget, a.drop_ms)
    mans = s.pop("_manifests")
    lg_all = sum((m.get("counts") or {}).get("recorded", 0) for m in mans)
    s["wire"] = wire(run, lg_all, s["provider_records"])
    s["sut"] = sut_side(run, s["offered"]) if a.mode == "sut" else None
    s["step"] = json.loads((run / "step.json").read_text()) if (run / "step.json").exists() else None
    s["tool"] = {"pbu_analyze": Path(__file__).name, "harness_analyze_sha": None}
    (run / "pbu_summary.json").write_text(json.dumps(s, indent=1, default=str) + "\n")
    L = [f"# {run.name}: strict {'PASS' if s['pass'] else 'FAIL'} | load-knee {'PASS' if s.get('load_knee_pass') else 'FAIL'} ({a.mode})", "",
         f"checks: {s['checks']}", f"load-knee checks: {s.get('load_knee_checks')}",
         f"offered {s['offered']} ({s['offered_rps']}/s) qualified {s['qualified']} ({s['qualified_rps']}/s) "
         f"FP-blocks {s['policy_block_fp']} ({s['fp_rate']}) expected-blocks {s['policy_block_expected']} "
         f"infra {s['infra_errors']} ({s['infra_error_rate']}) drops {s['drops']} safety {s['safety_failures']} "
         f"detection-misses {s['detection_misses']}",
         f"by class: {s['by_class']}",
         f"infra reasons: {s['infra_reasons']}", f"infra detail: {s['infra_detail']}", ""]
    for k in ("T_fw_addon", "T_fw_addon_nohold", "T_fw_addon_sse", "T_fw_addon_json", "T_addon_first_sse", "T_addon_total_sse",
              "T_addon_total_json", "T_release_lag_max", "client_ttft_sse", "lateness"):
        L.append(f"{k}: {A.fmt(s[k])}")
    L.append(f"loadgen: {s['loadgen']}")
    L.append(f"wire per client request: {s['wire']['per_client_request']}")
    if s["sut"]:
        su = s["sut"]
        L.append(f"gateway cores {su.get('gateway_cores')} cpu-ms/req {su.get('gateway_cpu_ms_per_req')} "
                 f"(workers {su.get('worker_cpu_ms_per_req')}, owners {su.get('owner_cpu_ms_per_req')}, redis {su.get('redis_cpu_ms_per_req')})")
        L.append(f"worker util {su.get('worker_util')}; per-core schedstat max {su.get('per_core_util_schedstat', {}).get('max')} "
                 f"mean {su.get('per_core_util_schedstat', {}).get('mean')}; procstat max {su.get('per_core_util_procstat', {}).get('max')}")
        L.append(f"gpu: {su.get('gpu')}")
        L.append(f"rss max total by role (MB): {su.get('rss_mb_max_total_by_role')}; fds {su.get('fds_max_total_by_role')}")
        wh = su.get("worker_hist") or {}
        for k in ("t_input_ns", "t_admit_ns", "t_tokenize_ns", "t_det_scan_ns", "t_guard_wait_ns", "guard_owner_rtt_ns",
                  "guard_queue_ns", "guard_exec_ns", "dispatch_headers_ns", "release_lag_ns", "holdback_wait_ns",
                  "release_processing_ns", "t_finalize_ns", "loop_lag_ns", "guard_windows_per_request"):
            if k in wh:
                L.append(f"W {k}: {wh[k]}")
        for k, v in (su.get("owner_hist") or {}).items():
            L.append(f"O {k}: {v}")
        L.append(f"worker counts: {su.get('worker_counts')}")
        L.append(f"owner counts: {su.get('owner_counts')}")
        L.append(f"gauges: {su.get('worker_gauges_at_end')} owners: {su.get('owner_gauges_at_end')}")
        L.append(f"notes: {su.get('snapshot_notes')}")
    (run / "pbu_summary.md").write_text("\n".join(L) + "\n")
    print("\n".join(L))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
