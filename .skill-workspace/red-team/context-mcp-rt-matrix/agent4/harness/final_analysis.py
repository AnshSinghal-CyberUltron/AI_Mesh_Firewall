#!/usr/bin/env python3
"""Post-run cross-agent/cross-tool leakage + coverage aggregation for agent4."""
import glob
import json
import os
import re

ROOT = os.path.join(os.path.dirname(__file__), "..")
EVID = os.path.join(ROOT, "evidence")

all_files = sorted(glob.glob(os.path.join(EVID, "**", "*.json"), recursive=True))
records = []
for f in all_files:
    if "_summary" in f or "_matrix_summary" in f or "_all_records_flat" in f or "_discovery" in f:
        continue
    try:
        d = json.load(open(f))
    except Exception:
        continue
    if isinstance(d, dict) and ("canary" in d or "server" in d):
        records.append((f, d))

# cross-agent/tool leakage: does canary from record A appear in response text of record B (different server)?
leaks = []
canary_by_record = []
for f, d in records:
    canary = d.get("canary")
    server = d.get("server")
    if canary and server:
        canary_by_record.append((f, server, canary))

for f, d in records:
    resp_text = json.dumps(d.get("response", {}), default=str)
    my_canary = d.get("canary")
    for other_f, other_server, other_canary in canary_by_record:
        if other_canary == my_canary:
            continue
        if other_canary in resp_text:
            leaks.append({"finding_in_file": f, "server": d.get("server"), "foreign_canary": other_canary, "foreign_server": other_server})

blocked_count = 0
allowed_count = 0
undefanged_exfil_with_real_secret = []
for f, d in records:
    a = d.get("analysis", {})
    if a.get("blocked"):
        blocked_count += 1
    else:
        allowed_count += 1

summary = {
    "total_records_scanned": len(records),
    "cross_agent_tool_canary_leaks": leaks,
    "cross_agent_tool_leak_count": len(leaks),
    "blocked_count": blocked_count,
    "allowed_count": allowed_count,
}

with open(os.path.join(EVID, "_final_cross_leak_analysis.json"), "w") as f:
    json.dump(summary, f, indent=2, default=str)

print(json.dumps(summary, indent=2, default=str)[:3000])
