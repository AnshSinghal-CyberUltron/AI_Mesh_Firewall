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


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True)
    ap.add_argument("--gw", required=True)
    ap.add_argument("--guards", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    run = Path(a.run)
    sm = json.loads((run / "split_metrics.json").read_text()) if (run / "split_metrics.json").exists() else {}
    n = sm.get("offered") or 0
    gw = side(run, a.gw, n)
    guards = {g: side(run, g, n) for g in a.guards.split()}
    d: dict = {"n_measured_requests": n}
    if gw:
        roles = gw.get("cpu_cores_by_role") or {}
        dt = gw.get("window_s") or 0
        ms = lambda cores: round(cores * dt * 1000 / n, 3) if n and dt else None  # noqa: E731
        d["gw_cores"] = {r: roles.get(r) for r in ("worker", "launcher", "redis")}
        d["gw_cpu_ms_per_req"] = ms((roles.get("worker") or 0) + (roles.get("launcher") or 0))
        d["gw_redis_cpu_ms_per_req"] = ms(roles.get("redis") or 0)
        d["gw_worker_util"] = gw.get("worker_util")
        d["gw_core_util_schedstat"] = {k: (gw.get("per_core_util_schedstat") or {}).get(k) for k in ("max", "mean", "sum_cores")}
        d["gw_nic"] = nic_window(run, a.gw, n)
        wh = gw.get("worker_hist") or {}
        d["guard_rtt"] = pick(wh, "guard_owner_rtt_ns")
        d["guard_wait"] = pick(wh, "t_guard_wait_ns")
        d["guard_queue_seen_by_worker"] = pick(wh, "guard_queue_ns")
        d["guard_exec_seen_by_worker"] = pick(wh, "guard_exec_ns")
        wc = gw.get("worker_counts") or {}
        d["windows_per_request"] = round(wc["guard_windows"] / wc["admitted"], 3) if wc.get("admitted") and wc.get("guard_windows") else None
        d["t_input"] = pick(wh, "t_input_ns")
        d["loop_lag"] = pick(wh, "loop_lag_ns")
        d["gw_counts"] = gw.get("worker_counts")
    for g, s in guards.items():
        if not s:
            continue
        roles = s.get("cpu_cores_by_role") or {}
        dt = s.get("window_s") or 0
        owner_cores = sum(v for r, v in roles.items() if r == "owner")
        gg = d.setdefault("guards", {})[g] = {
            "owner_cores": round(owner_cores, 3),
            "owner_cpu_ms_per_req": round(owner_cores * dt * 1000 / n, 3) if n and dt else None,
            "gpu": s.get("gpu"), "nic": s.get("unit_nic"),
            "owner_queue_gauges": {k: v for k, v in (s.get("gauges_window") or {}).items() if k.startswith("o.")},
            "owner_hist": {k: pick(s.get("owner_hist"), k) for k in ("guard_queue_ns", "guard_exec_ns")},
            "owner_counts": s.get("owner_counts"), "core_util": {k: (s.get("per_core_util_schedstat") or {}).get(k) for k in ("max", "mean")},
        }
        nics = nic_window(run, g, n) or {}
        gg["nic"] = nics
        tot = [v["rx_bytes_per_req"] + v["tx_bytes_per_req"] for v in nics.values() if v.get("rx_bytes_per_req") is not None]
        if tot:
            gg["gw_guard_bytes_per_req"] = round(sum(tot), 1)
    out = {"derived": d, "gw": gw, "guards": guards}
    Path(a.out).write_text(json.dumps(out, indent=1, default=str) + "\n")
    print(json.dumps(d, indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
