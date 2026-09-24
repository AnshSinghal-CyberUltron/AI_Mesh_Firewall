#!/usr/bin/env python3
"""split lane: SUT-side attribution for one step. The gateway VM and each guard VM are analysed
separately with the unit lane's code (tools/pbu_analyze_ref.py sut_side, unchanged): exact per-process
CPU from /proc/PID/task/*/schedstat, per-core schedstat, GPU from nvidia-smi dmon, NIC bytes, and
histogram/counter deltas of the rvproto metric dumps between meas_start and meas_end.

Derived per step:
  gateway CPU-ms/request   workers + launcher on the gateway VM (redis reported separately)
  owner CPU-ms/request     guard owner process(es) on the guard VM(s)
  GPU sm%                  dmon mean/max over the measurement window, per guard VM
  guard RTT                worker histogram guard_owner_rtt_ns (submit -> answer, incl. queue + exec + network)
  guard wait               worker histogram t_guard_wait_ns
  owner queue              owner gauges guard_queue_items/windows (max over the window) + owner guard_queue_ns
  gw<->guard bytes/request guard VM NIC rx+tx over the window / measured requests
                           (the guard VM carries only this traffic plus a small ssh/sampler overhead)
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pbu_analyze_ref as P  # noqa: E402


def side(run: Path, vm: str, n_meas: int) -> dict | None:
    src = run / vm / "sut"
    if not src.exists():
        return None
    with tempfile.TemporaryDirectory() as td:  # sut_side reads <run>/rv-proto-unit-1/unit
        shim = Path(td) / "rv-proto-unit-1"
        shim.mkdir()
        (shim / "unit").symlink_to(src.resolve())
        return P.sut_side(Path(td), n_meas)


def nic_window(run: Path, vm: str, n_meas: int) -> dict | None:
    """Bytes on every non-lo NIC over the measurement window (the sampler's first/last samples in it)."""
    u = run / vm / "sut"
    f = P.A.find_raw(u, "samples.jsonl")
    if f is None or not (u / "schedule.json").exists():
        return None
    sc = json.loads((u / "schedule.json").read_text())
    win = [x for x in P.A.read_jsonl(f) if sc["meas_start"] - 1.0 <= x["t"] <= sc["meas_end"] + 1.0]
    if len(win) < 2:
        return None
    a, b = win[0], win[-1]
    dt = b["t"] - a["t"]
    out = {}
    for name, nb in b["net"].items():
        na = a["net"].get(name)
        if not isinstance(nb, dict) or not isinstance(na, dict):
            continue
        rx, tx = nb["rx_bytes"] - na["rx_bytes"], nb["tx_bytes"] - na["tx_bytes"]
        out[name] = {"rx_mbit_s": round(rx * 8 / 1e6 / dt, 2), "tx_mbit_s": round(tx * 8 / 1e6 / dt, 2),
                     "rx_bytes_per_req": round(rx / n_meas, 1) if n_meas else None,
                     "tx_bytes_per_req": round(tx / n_meas, 1) if n_meas else None}
    return out


def pick(h: dict | None, name: str) -> dict | None:
    return None if not h or name not in h else {k: h[name].get(k) for k in ("n", "p50_ms", "p90_ms", "p99_ms", "p99.9_ms", "mean_ms")}


def gw_block(run: Path, vm: str, n: int, s: dict) -> dict:
    roles = s.get("cpu_cores_by_role") or {}
    dt = s.get("window_s") or 0
    ms = lambda cores: round(cores * dt * 1000 / n, 3) if n and dt else None  # noqa: E731
    wh = s.get("worker_hist") or {}
    wc = s.get("worker_counts") or {}
    return {"cores": {r: roles.get(r) for r in ("worker", "launcher", "redis")},
            "cpu_ms_per_req": ms((roles.get("worker") or 0) + (roles.get("launcher") or 0)),
            "redis_cpu_ms_per_req": ms(roles.get("redis") or 0),
            "vm_cores_busy": (s.get("per_core_util_schedstat") or {}).get("sum_cores"),
            "worker_util": s.get("worker_util"),
            "core_util": {k: (s.get("per_core_util_schedstat") or {}).get(k) for k in ("max", "mean", "sum_cores")},
            "nic": nic_window(run, vm, n), "guard_rtt": pick(wh, "guard_owner_rtt_ns"), "guard_wait": pick(wh, "t_guard_wait_ns"),
            "guard_queue_seen_by_worker": pick(wh, "guard_queue_ns"), "guard_exec_seen_by_worker": pick(wh, "guard_exec_ns"),
            "t_input": pick(wh, "t_input_ns"), "t_tokenize": pick(wh, "t_tokenize_ns"), "loop_lag": pick(wh, "loop_lag_ns"),
            "windows_per_request": round(wc["guard_windows"] / wc["admitted"], 3) if wc.get("admitted") and wc.get("guard_windows") else None,
            "admitted": wc.get("admitted"), "counts": wc}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True)
    ap.add_argument("--gws", "--gw", dest="gws", required=True)
    ap.add_argument("--guards", required=True)
    ap.add_argument("--extra", default="")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    run = Path(a.run)
    sm = json.loads((run / "split_metrics.json").read_text()) if (run / "split_metrics.json").exists() else {}
    n = sm.get("offered") or 0
    gws = {g: side(run, g, n) for g in a.gws.split()}
    guards = {g: side(run, g, n) for g in a.guards.split()}
    extras = {g: side(run, g, n) for g in a.extra.split()}
    d: dict = {"n_measured_requests": n, "gateways": {}, "guards": {}, "extras": {}}
    for g, s in gws.items():
        if s:
            d["gateways"][g] = gw_block(run, g, n, s)
    G = list(d["gateways"].values())
    if G:
        tot = [x["cpu_ms_per_req"] for x in G if x["cpu_ms_per_req"] is not None]
        d["gw_cpu_ms_per_req"] = round(sum(tot), 3) if tot else None  # all gateways, per client request
        d["gw_cores_busy_total"] = round(sum(x["vm_cores_busy"] or 0 for x in G), 2)
        worst = lambda key, q: max(((x.get(key) or {}).get(q) or 0) for x in G)  # noqa: E731
        d["guard_rtt"] = {q: worst("guard_rtt", q) for q in ("p50_ms", "p99_ms", "p99.9_ms")}
        d["t_input"] = {q: worst("t_input", q) for q in ("p99_ms", "p99.9_ms")}
        d["loop_lag"] = {q: worst("loop_lag", q) for q in ("p99_ms", "p99.9_ms")}
        d["worker_util_max"] = max(((x.get("worker_util") or {}).get("max") or 0) for x in G)
        d["windows_per_request"] = G[0].get("windows_per_request")
        d["gw_admitted_share"] = {g: x.get("admitted") for g, x in d["gateways"].items()}
    for g, s in guards.items():
        if not s:
            continue
        roles = s.get("cpu_cores_by_role") or {}
        dt = s.get("window_s") or 0
        owner_cores = sum(v for r, v in roles.items() if r == "owner")
        nics = nic_window(run, g, n) or {}
        tot = [v["rx_bytes_per_req"] + v["tx_bytes_per_req"] for v in nics.values() if v.get("rx_bytes_per_req") is not None]
        d["guards"][g] = {
            "owner_cores": round(owner_cores, 3),
            "owner_cpu_ms_per_req": round(owner_cores * dt * 1000 / n, 3) if n and dt else None,
            "vm_cores_busy": (s.get("per_core_util_schedstat") or {}).get("sum_cores"),
            "gpu": s.get("gpu"), "nic": nics, "gw_guard_bytes_per_req": round(sum(tot), 1) if tot else None,
            "owner_queue_gauges": {k: v for k, v in (s.get("gauges_window") or {}).items() if k.startswith("o.")},
            "owner_hist": {k: pick(s.get("owner_hist"), k) for k in ("guard_queue_ns", "guard_exec_ns")},
            "owner_counts": s.get("owner_counts"),
            "core_util": {k: (s.get("per_core_util_schedstat") or {}).get(k) for k in ("max", "mean")},
        }
    for g, s in extras.items():
        if s:
            d["extras"][g] = {"vm_cores_busy": (s.get("per_core_util_schedstat") or {}).get("sum_cores"),
                              "core_util": {k: (s.get("per_core_util_schedstat") or {}).get(k) for k in ("max", "mean")},
                              "cores_by_role": s.get("cpu_cores_by_role"), "nic": nic_window(run, g, n)}
    # phase-A key names kept for split_table.py
    if len(G) == 1:
        g0 = G[0]
        d.update({"gw_core_util_schedstat": g0["core_util"], "gw_worker_util": g0["worker_util"], "gw_nic": g0["nic"],
                  "guard_wait": g0["guard_wait"], "guard_queue_seen_by_worker": g0["guard_queue_seen_by_worker"],
                  "guard_exec_seen_by_worker": g0["guard_exec_seen_by_worker"], "gw_counts": g0["counts"]})
        d["guard_rtt"] = g0["guard_rtt"]
        d["t_input"] = g0["t_input"]
        d["loop_lag"] = g0["loop_lag"]
    out = {"derived": d, "gws": gws, "guards": guards, "extras": extras}
    Path(a.out).write_text(json.dumps(out, indent=1, default=str) + "\n")
    print(json.dumps(d, indent=1, default=str)[:6000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
