#!/usr/bin/env python3
"""Phase-2 table (loop isolation, corrected metrics a-d) from pbu_summary.json + pbu_c4.json of each run.

  phase2_table.py RUN [RUN ...]
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

RUNS = Path("/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs")


def g(d, *ks):
    for k in ks:
        if not isinstance(d, dict):
            return None
        d = d.get(k)
    return d


def main() -> int:
    rows = []
    for r in sys.argv[1:]:
        s = json.loads((RUNS / r / "pbu_summary.json").read_text())
        c = json.loads((RUNS / r / "pbu_c4.json").read_text()) if (RUNS / r / "pbu_c4.json").exists() else {}
        su = s.get("sut") or {}
        wh = su.get("worker_hist") or {}
        rows.append((r, s, c, su, wh))
    print("| run | rate | infra % (reasons) | sem:U | C4 p50 / p99 / p99.9 (all offered, inf) | C4 p99 qualified | STRICT p99 all / q "
          "| LOAD p99 | first_tok1 p99 | proc_extra p99 | holdback wait tokens p50/p99/max |")
    print("|" + "---|" * 11)
    for r, s, c, su, wh in rows:
        reasons = {k: v for k, v in (s.get("infra_reasons") or {}).items()
                   if not k.startswith(("stage_", "incomplete", "unjoined", "disposition_missing"))}
        sem_u = (su.get("worker_counts") or {}).get("guard_unavailable_findings", 0)
        ht = c.get("holdback_wait_per_piece_tokens") or {}
        print(f"| {r} | {g(s, 'step', 'rate')} | {100 * (s.get('infra_error_rate') or 0):.3f} {reasons} | {sem_u} "
              f"| {g(c, 'c4_all', 'p50')} / {g(c, 'c4_all', 'p99')} / {g(c, 'c4_all', 'p999')} | {g(c, 'c4_q', 'p99')} "
              f"| {g(c, 'strict_all', 'p99')} / {g(c, 'strict_q', 'p99')} | {g(c, 'load_q', 'p99')} | {g(c, 'first_tok1_q', 'p99')} "
              f"| {g(c, 'proc_extra_q', 'p99')} | {ht.get('p50')}/{ht.get('p99')}/{ht.get('max')} |")
    print()
    print("| run | loop lag p99 / p99.9 / max_cum | t_input p99 | tokenize p99 | guard wait p99 | owner queue p99 | CPU-ms/req (workers+owners) "
          "| gw cores | workers min/median/max | owner0/owner1 | conns/worker min/median/max | GPU SM % |")
    print("|" + "---|" * 12)
    for r, s, c, su, wh in rows:
        def h(k, q="p99_ms"):
            v = (wh.get(k) or {}).get(q)
            return "-" if v is None else f"{v:.2f}"
        wu = su.get("worker_util") or {}
        cr = su.get("cpu_cores_by_role") or {}
        cm = su.get("worker_conns_max") or {}
        gpu = su.get("gpu") or {}
        print(f"| {r} | {h('loop_lag_ns')} / {h('loop_lag_ns', 'p99.9_ms')} / {h('loop_lag_ns', 'max_cum_ms')} | {h('t_input_ns')} "
              f"| {h('t_tokenize_ns')} | {h('t_guard_wait_ns')} | {h('guard_queue_ns')} | {su.get('gateway_cpu_ms_per_req')} "
              f"({su.get('worker_cpu_ms_per_req')}+{su.get('owner_cpu_ms_per_req')}) | {su.get('gateway_cores')} "
              f"| {wu.get('min')}/{wu.get('median')}/{wu.get('max')} | {cr.get('owner0')}/{cr.get('owner1')} "
              f"| {cm.get('min')}/{cm.get('median')}/{cm.get('max')} | {g(gpu, '0', 'sm_mean')}/{g(gpu, '1', 'sm_mean')} |")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
