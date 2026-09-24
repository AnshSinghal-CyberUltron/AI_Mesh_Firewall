#!/usr/bin/env python3
"""Aggregate pbu_summary.json of many steps into markdown tables (recomputed only from the summaries,
which are themselves recomputed from raw files by pbu_analyze.py).

  aggregate.py [--runs DIR] [--match REGEX] [--out FILE.md]
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

RUNS = Path("/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs")


def g(d: dict | None, *path: str, default=None):
    for p in path:
        if not isinstance(d, dict) or p not in d:
            return default
        d = d[p]
    return d


def pct(d: dict | None, key: str) -> str:
    v = (d or {}).get(key)
    return "-" if v is None else f"{v:.1f}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", default=str(RUNS))
    ap.add_argument("--match", default=".*")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    rows = []
    for d in sorted(Path(a.runs).iterdir()):
        f = d / "pbu_summary.json"
        if not f.exists() or not re.search(a.match, d.name):
            continue
        s = json.loads(f.read_text())
        rows.append((d.name, s))
    L = []
    L.append("| run | rate | offered/s | achieved/s | qualified/s | strict | load | T_fw p50 | p90 | p99 | p99.9 | SSE p99 | JSON p99 "
             "| nohold p99 | first(SSE) p99 | lag p50 | lag p99 | lag max | infra % | infra reasons | FP % | drops |")
    L.append("|" + "---|" * 22)
    for name, s in rows:
        st = s.get("step") or {}
        reasons = {k: v for k, v in (s.get("infra_reasons") or {}).items()
                   if not k.startswith(("stage_", "incomplete", "unjoined", "disposition_missing"))}
        L.append(f"| {name} | {st.get('rate')} | {s.get('offered_rps')} | {s.get('achieved_rps')} | {s.get('qualified_rps')} "
                 f"| {'PASS' if s.get('pass') else 'FAIL'} | {'PASS' if s.get('load_knee_pass') else 'FAIL'} "
                 f"| {pct(s.get('T_fw_addon'), 'p50')} | {pct(s.get('T_fw_addon'), 'p90')} | {pct(s.get('T_fw_addon'), 'p99')} "
                 f"| {pct(s.get('T_fw_addon'), 'p999')} | {pct(s.get('T_fw_addon_sse'), 'p99')} | {pct(s.get('T_fw_addon_json'), 'p99')} "
                 f"| {pct(s.get('T_fw_addon_nohold'), 'p99')} | {pct(s.get('T_addon_first_sse'), 'p99')} "
                 f"| {pct(s.get('T_release_lag_max'), 'p50')} | {pct(s.get('T_release_lag_max'), 'p99')} | {pct(s.get('T_release_lag_max'), 'max')} "
                 f"| {100 * (s.get('infra_error_rate') or 0):.3f} | {reasons} | {100 * (s.get('fp_rate') or 0):.2f} | {s.get('drops')} |")
    L.append("")
    L.append("| run | gw cores | CPU-ms/req (gw) | workers | owners | redis | busiest worker (cores) | core util max (schedstat) "
             "| core util mean | GPU0 sm% | GPU1 sm% | guard wait p99 | owner rtt p99 | owner queue p99 | exec p99 | loop lag p99 | loop lag max "
             "| t_input p99 | tokenize p99 | release proc p99 | holdback wait p99 | RSS workers MB | RSS owners MB | lg CPU max % |")
    L.append("|" + "---|" * 25)
    for name, s in rows:
        su = s.get("sut") or {}
        wh = su.get("worker_hist") or {}
        gpu = su.get("gpu") or {}

        def h(k: str, q: str = "p99_ms") -> str:
            v = (wh.get(k) or {}).get(q)
            return "-" if v is None else f"{v:.2f}"
        lgmax = max((x.get("busy_max") or 0) for x in (s.get("loadgen") or [{}]))
        L.append(f"| {name} | {su.get('gateway_cores')} | {su.get('gateway_cpu_ms_per_req')} | {su.get('worker_cpu_ms_per_req')} "
                 f"| {su.get('owner_cpu_ms_per_req')} | {su.get('redis_cpu_ms_per_req')} | {g(su, 'worker_util', 'max')} "
                 f"| {g(su, 'per_core_util_schedstat', 'max')} | {g(su, 'per_core_util_schedstat', 'mean')} "
                 f"| {g(gpu, '0', 'sm_mean')} | {g(gpu, '1', 'sm_mean')} | {h('t_guard_wait_ns')} | {h('guard_owner_rtt_ns')} "
                 f"| {h('guard_queue_ns')} | {h('guard_exec_ns')} | {h('loop_lag_ns')} | {h('loop_lag_ns', 'max_cum_ms')} "
                 f"| {h('t_input_ns')} | {h('t_tokenize_ns')} | {h('release_processing_ns')} | {h('holdback_wait_ns')} "
                 f"| {g(su, 'rss_mb_max_total_by_role', 'worker')} | {g(su, 'rss_mb_max_total_by_role', 'owner')} | {lgmax} |")
    L.append("")
    L.append("| run | client->gw B/req | gw->client B/req | gw->prov B/req | prov->gw B/req | resp body SSE B | resp body JSON B "
             "| sheds by reason | guard unavailable | deadline expired | audit dropped |")
    L.append("|" + "---|" * 11)
    for name, s in rows:
        w = g(s, "wire", "per_client_request") or {}
        wc = g(s, "sut", "worker_counts") or {}
        oc = g(s, "sut", "owner_counts") or {}
        sheds = {k: v for k, v in wc.items() if k.startswith("shed")}
        L.append(f"| {name} | {w.get('client_to_gw')} | {w.get('gw_to_client')} | {w.get('gw_to_provider')} | {w.get('provider_to_gw')} "
                 f"| {g(s, 'resp_body_bytes_mean', 'sse')} | {g(s, 'resp_body_bytes_mean', 'json')} | {sheds} "
                 f"| {wc.get('guard_unavailable_findings', 0)} | {oc.get('guard_deadline_expired', 0)} "
                 f"| {g(s, 'sut', 'worker_gauges_at_end', 'audit_dropped')} |")
    text = "\n".join(L) + "\n"
    if a.out:
        Path(a.out).write_text(text)
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
