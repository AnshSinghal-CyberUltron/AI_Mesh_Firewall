#!/usr/bin/env python3
"""Evaluate knee confirmations (driver_knee.sh): fine sweep + 3 x 300 s at the knee + 1 x 300 s at the next rate.

A repeat PASSES iff every offered request completed, 0 schedule drops (lateness > 5 ms) and p99 <= target.
Reports: highest repeatable passing rate (all 3 knee repeats pass) and the first failing rate.
usage: analyze_knee.py <knee_dir> [...]   (knee_dir contains knee.txt, fine/, confirm/)
"""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import analyze_ladder as AL  # noqa: E402


def main():
    out = {}
    for d in sys.argv[1:]:
        d = Path(d)
        kt = (d / "knee.txt").read_text() if (d / "knee.txt").exists() else ""
        m = re.search(r"knee=([\d.]+) next=([\d.]+) target=([\d.]+)", kt)
        if not m:
            out[str(d)] = {"error": "no knee.txt"}
            continue
        knee, nxt, tgt = map(float, m.groups())
        fine = AL.analyze(d / "fine")
        groups = AL.load_steps(d / "confirm")
        reps = []
        for k in sorted(groups):
            s = AL.step_metrics(groups[k])
            s["step"] = k
            s["pass"] = bool(s["completed"] == s["n"] and s["errors"] == 0 and s["drops_gt5ms"] == 0
                             and s["lat_ms"] and s["lat_ms"]["p99"] <= tgt)
            reps.append(s)
        knee_reps = [r for r in reps if abs(r["offered_wps"] - knee) < 1e-6][:3]
        next_rep = [r for r in reps if abs(r["offered_wps"] - nxt) < 1e-6]
        res = {"target_p99_ms": tgt, "knee_wps": knee, "next_wps": nxt,
               "fine_sweep": [{"offered_wps": s["offered_wps"], "p99": (s["lat_ms"] or {}).get("p99"),
                               "drops": s["drops_gt5ms"], "n": s["n"]} for s in fine["steps"]],
               "knee_repeats": [{"p50": r["lat_ms"]["p50"], "p99": r["lat_ms"]["p99"], "max": r["lat_ms"]["max"],
                                 "n": r["n"], "drops": r["drops_gt5ms"], "achieved_wps": r["achieved_wps"],
                                 "pass": r["pass"]} for r in knee_reps],
               "next_step": [{"p50": r["lat_ms"]["p50"], "p99": r["lat_ms"]["p99"], "n": r["n"], "drops": r["drops_gt5ms"],
                              "pass": r["pass"]} for r in next_rep],
               "knee_repeatable_pass": len(knee_reps) == 3 and all(r["pass"] for r in knee_reps)}
        out[str(d)] = res
        print(json.dumps({str(d.name): res}, indent=1))
    Path("knee_summary.json").write_text(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
