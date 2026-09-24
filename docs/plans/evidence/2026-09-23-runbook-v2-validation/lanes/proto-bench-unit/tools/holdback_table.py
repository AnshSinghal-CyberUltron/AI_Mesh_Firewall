#!/usr/bin/env python3
"""Holdback attribution table (measurement 3) from pbu_summary.json of hb-on-*/hb-off-*/d-sse-* steps.

Client side (harness definitions, every stream sampled with -sample-mod 1):
  T_release_lag_max  worst added delay of any content piece in a stream (includes the input phase)
  T_addon_first      first content byte added delay; T_addon_total end-of-stream added delay
Gateway side (rvproto histograms, per released piece): release_lag = send time - arrival of the oldest
  released char; holdback_wait = time the piece waited for more upstream bytes; release_processing =
  compute from upstream bytes ready to frame sent.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

RUNS = Path("/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs")


def main() -> int:
    names = sys.argv[1:] or ["hb-on-10", "hb-on-20", "hb-on-30", "hb-off-10", "hb-off-20", "hb-off-30", "d-sse-040"]
    print("| run | ITL ms | scan | qualified | T_release_lag_max p50 | p99 | max | T_addon_first p50 | p99 | T_addon_total p99 "
          "| T_fw_addon p99 | gw release_lag p50 | p99 | gw holdback_wait p50 | p99 | gw release_proc p50 | p99 | infra % |")
    print("|" + "---|" * 18)
    for n in names:
        f = RUNS / n / "pbu_summary.json"
        if not f.exists():
            print(f"| {n} | (missing) |")
            continue
        s = json.loads(f.read_text())
        st = s.get("step") or {}
        itl = (st.get("prov_flags") or "").split("-itl ")[-1].split("ms")[0]
        wh = (s.get("sut") or {}).get("worker_hist") or {}

        def c(k: str, q: str) -> str:
            v = (s.get(k) or {}).get(q)
            return "-" if v is None else f"{v:.2f}"

        def w(k: str, q: str) -> str:
            v = (wh.get(k) or {}).get(q)
            return "-" if v is None else f"{v:.2f}"
        scan = "direct" if n.startswith("d-") else ("ON" if "-on-" in n else "OFF")
        print(f"| {n} | {itl} | {scan} | {s.get('qualified')} | {c('T_release_lag_max', 'p50')} | {c('T_release_lag_max', 'p99')} "
              f"| {c('T_release_lag_max', 'max')} | {c('T_addon_first_sse', 'p50')} | {c('T_addon_first_sse', 'p99')} "
              f"| {c('T_addon_total_sse', 'p99')} | {c('T_fw_addon', 'p99')} | {w('release_lag_ns', 'p50_ms')} | {w('release_lag_ns', 'p99_ms')} "
              f"| {w('holdback_wait_ns', 'p50_ms')} | {w('holdback_wait_ns', 'p99_ms')} | {w('release_processing_ns', 'p50_ms')} "
              f"| {w('release_processing_ns', 'p99_ms')} | {100 * (s.get('infra_error_rate') or 0):.3f} |")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
