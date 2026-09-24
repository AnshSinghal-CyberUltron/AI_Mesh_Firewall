#!/usr/bin/env python3
"""Client-observed vs gateway-observed timing per fleet run (measurement window)."""
import json, sys
from pathlib import Path
FO = Path(__file__).resolve().parent
sys.path.insert(0, str(FO))
import fleet_gw  # noqa: E402
rows = []
for f in sorted((FO / "out").glob("*.json")):
    r = f.stem
    me = json.loads(f.read_text())
    step = json.loads((fleet_gw.RAW / r / "step.json").read_text())
    try:
        gw = fleet_gw.run_side(r)
    except Exception as e:  # noqa: BLE001
        print(r, "gw error", e, file=sys.stderr); continue
    wh = gw["w_hist"]
    ti, rl, ll = wh.get("t_input_ns") or {}, wh.get("release_lag_max_ns") or {}, wh.get("loop_lag_ns") or {}
    hd, lg = me["hdr_addon_sse"], me["sample_lag"]
    def gap(a, b):
        return None if a is None or b is None else round(a - b, 2)
    rows.append({
        "run": r, "units": len(gw["units"]), "rate": step.get("rate"),
        "tgt": "edge" if ":8080" in (step.get("targets") or "") else "direct",
        "gw t_input p50/p90/p99": f"{ti.get('p50_ms')}/{ti.get('p90_ms')}/{ti.get('p99_ms')}",
        "cli hdr_addon p50/p90/p99": f"{hd.get('p50')}/{hd.get('p90')}/{hd.get('p99')}",
        "gap p50": gap(hd.get("p50"), ti.get("p50_ms")), "gap p99": gap(hd.get("p99"), ti.get("p99_ms")),
        "gap mean": gap(hd.get("mean_finite"), ti.get("mean_ms")),
        "gw lagmax p50/p99": f"{rl.get('p50_ms')}/{rl.get('p99_ms')}",
        "cli lag p50/p99": f"{lg.get('p50')}/{lg.get('p99')}",
        "lag gap p50": gap(lg.get("p50"), rl.get("p50_ms")), "lag gap p99": gap(lg.get("p99"), rl.get("p99_ms")),
        "lag gap mean": gap(lg.get("mean_finite"), rl.get("mean_ms")),
        "loop_lag p90/p99/max": f"{ll.get('p90_ms')}/{ll.get('p99_ms')}/{ll.get('top_bucket_ms')}",
    })
cols = list(rows[0])
print("| " + " | ".join(cols) + " |"); print("|" + "---|" * len(cols))
for x in rows:
    print("| " + " | ".join(str(x[c]) for c in cols) + " |")
