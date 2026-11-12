"""F1a-3 plan revision 3: the head-of-line table of plan §3.6.4 from both heavy-lock runs.

  python -I summarize_hol.py <out dir>     (reads hol_rev3.jsonl and hol_rev3_repeat.jsonl)
"""
import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path

out = Path(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).parent / "out")
cells = defaultdict(list)
for name in ("hol_rev3.jsonl", "hol_rev3_repeat.jsonl"):
    for line in (out / name).read_text().splitlines():
        r = json.loads(line)
        cells[(r["pipeline_us"], r["flood"], r["variant"])].append(r)


def fmt(rows, key):
    vals = sorted(r[key] for r in rows)
    med = statistics.median(vals)
    rng = f" ({vals[0]:g}-{vals[-1]:g}, n={len(vals)})" if len(vals) > 1 else " (n=1)"
    return f"{med:g}{rng}"


print("| Per-event work | Unpaced streams | p99 lateness ms: none (B's client) | yield | pace (rev 3) "
      "| util none / pace | unpaced events/s per stream none / pace | CPU us per event none / pace |")
print("|---|---|---|---|---|---|---|---|")
for pipe in (0, 100):
    for fl in (0, 1, 4):
        n, y, p = (cells[(pipe, fl, v)] for v in ("none", "yield", "pace"))
        def fe(rows, fl=fl):
            return statistics.median(r["flood_events_per_s"] / max(1, fl) for r in rows) if fl else 0
        print(f"| {pipe} us | {fl} | {fmt(n, 'lateness_ms_p99')} | {fmt(y, 'lateness_ms_p99')} | "
              f"{fmt(p, 'lateness_ms_p99')} | {statistics.median(r['util'] for r in n):.2f} / "
              f"{statistics.median(r['util'] for r in p):.2f} | {fe(n):.0f} / {fe(p):.0f} | "
              f"{statistics.median(r['cpu_us_per_paced_event'] for r in n):.1f} / "
              f"{statistics.median(r['cpu_us_per_paced_event'] for r in p):.1f} |")
