#!/usr/bin/env python3
"""Per-unit and per-worker admitted/shed split over the measurement window (snapshot deltas)."""
import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import gwsnap as G  # noqa: E402
RAW = Path.home() / "rv-evidence-raw/proto-bench-fleet/runs"
for run in sys.argv[1:]:
    rd = RAW / run; step = json.loads((rd / "step.json").read_text())
    out = {"run": run}
    for u in step["units"].split():
        s = rd / u / "sut"
        a, b = G.load(s / "snap-meas_start", "worker"), G.load(s / "snap-meas_end", "worker")
        adm, shed = [], []
        for k, wb in b.items():
            wa = a.get(k, {"count": {}})
            adm.append(wb["count"].get("admitted", 0) - wa["count"].get("admitted", 0))
            shed.append(sum(v - wa["count"].get(kk, 0) for kk, v in wb["count"].items() if kk.startswith("shed{")))
        m = sum(adm) / len(adm)
        out[u] = {"admitted": sum(adm), "sheds": sum(shed), "workers": len(adm), "worker_max_over_mean": round(max(adm) / m, 3),
                  "worker_min_over_mean": round(min(adm) / m, 3), "shed_pct": round(100 * sum(shed) / sum(adm), 3)}
    print(json.dumps(out))
