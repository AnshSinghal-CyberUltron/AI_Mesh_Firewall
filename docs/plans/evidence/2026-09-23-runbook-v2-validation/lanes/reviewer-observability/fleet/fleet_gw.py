#!/usr/bin/env python3
"""reviewer-observability/fleet: gateway-side deltas merged over all units of a fleet run, and the
client-vs-gateway comparison.  usage: fleet_gw.py RUN [A B]  (A,B = snapshot labels, default meas_start meas_end)"""
import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import gwsnap as G  # noqa: E402

RAW = Path.home() / "rv-evidence-raw/proto-bench-fleet/runs"
FO = Path(__file__).resolve().parent


def run_side(run: str, la="meas_start", lb="meas_end"):
    rd = RAW / run
    step = json.loads((rd / "step.json").read_text())
    units = (step.get("units") or "").split()
    wa, wb, oa, ob = {}, {}, {}, {}
    notes = []
    for u in units:
        s = rd / u / "sut"
        for stem, A, B in (("worker", wa, wb), ("owner", oa, ob)):
            a = G.load(s / f"snap-{la}", stem)
            b = G.load(s / f"snap-{lb}", stem)
            for k, v in a.items():
                A[f"{u}/{k}"] = v
            for k, v in b.items():
                B[f"{u}/{k}"] = v
    wh, wc, wn = G.delta(wa, wb)
    oh, oc, on = G.delta(oa, ob)
    return {"units": units, "procs_w": (len(wa), len(wb)), "procs_o": (len(oa), len(ob)), "notes": wn + on,
            "w_hist": {k: G.quantiles(v) for k, v in wh.items()}, "w_count": wc,
            "o_hist": {k: G.quantiles(v) for k, v in oh.items()}, "o_count": oc}


if __name__ == "__main__":
    run = sys.argv[1]
    la, lb = (sys.argv[2], sys.argv[3]) if len(sys.argv) > 3 else ("meas_start", "meas_end")
    print(json.dumps(run_side(run, la, lb), indent=1))
