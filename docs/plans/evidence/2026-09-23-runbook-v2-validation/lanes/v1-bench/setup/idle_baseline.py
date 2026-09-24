#!/usr/bin/env python3
"""Idle CPU baseline per container from a sampler.jsonl recorded with NO load (whole file minus the
first/last 5 s). usage: idle_baseline.py sampler.jsonl[.zst]"""
import json, sys
sys.path.insert(0, "/tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/1b4d4994-cf08-4d34-a6cb-54fd36f36877/scratchpad/harness")
from analyze import open_any, loads  # noqa: E402
rows = []
with open_any(sys.argv[1]) as fh:
    for line in fh:
        if line.strip():
            r = loads(line)
            if "meta" not in r:
                rows.append(r)
a, b = rows[5], rows[-6]
dt = b["wall"] - a["wall"]
out = {"seconds": round(dt, 1), "cores_idle": {c: round((b["ctr"][c]["usage_usec"] - a["ctr"][c]["usage_usec"]) / 1e6 / dt, 4)
                                              for c in sorted(a["ctr"])}}
tot = sum(b["host"]) - sum(a["host"]); idle = (b["host"][3] + b["host"][4]) - (a["host"][3] + a["host"][4])
out["host_util_pct_idle"] = round(100 * (tot - idle) / tot, 2)
print(json.dumps(out, indent=1))
