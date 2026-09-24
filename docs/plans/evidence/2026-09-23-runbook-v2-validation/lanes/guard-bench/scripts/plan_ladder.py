#!/usr/bin/env python3
"""Print an offered-load ladder (windows/s) for W-window requests from a capacity_probe.json:
serial capacity = W / p50 service(batch=W); ceiling = best b / p50 service(b) over b<=B.
Ladder = serial capacity x fractions, extended to 1.1 x the batched ceiling."""
import json, sys
cap = json.load(open(sys.argv[1])); W = int(sys.argv[2]); B = int(sys.argv[3])
svc = {r["batch"]: r["service_ms"]["p50"] for r in cap["rows"]}
serial = W / (svc[W] / 1000.0) if W in svc else W / (min(v for b, v in svc.items() if b >= W) / 1000.0)
ceil_ = max([b / (svc[b] / 1000.0) for b in svc if b <= max(B, W)] + [serial])
fr = [0.05, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0]
rates = sorted({round(serial * f, 1) for f in fr} | {round(ceil_ * f, 1) for f in (1.0, 1.1)})
print(",".join(str(r) for r in rates))
