#!/usr/bin/env python3
"""error_timeline.py RUN_DIR...  list every non-200/non-policy response and every fail-closed (sem:U) block
with its scheduled wall-clock time (UTC), status, error detail, stages and response time."""
import datetime, json, pathlib, sys
sys.path.insert(0, "/tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/1b4d4994-cf08-4d34-a6cb-54fd36f36877/scratchpad/harness")
import analyze as A
for run in sys.argv[1:]:
    rows = []
    for d in sorted(pathlib.Path(run).glob("rv-pbu-lg-*/lg")):
        man = json.loads((d / "manifest.json").read_text())
        t0 = man["config"]["start_at_unix_ms"] / 1000
        for c in A.read_jsonl(A.find_raw(d, "requests.jsonl")):
            if (c.get("status") != 200 and c.get("disp") != "BLOCK") or "sem:U" in (c.get("stages") or ""):
                rows.append((t0 + c["sched_ns"] / 1e9, c.get("ph"), c.get("status"), c.get("err_detail"), c.get("stages"),
                             round(c.get("end_ns", 0) / 1e6, 1)))
    print(f"== {pathlib.Path(run).name}: {len(rows)} errors")
    for r in sorted(rows):
        print(" ", datetime.datetime.fromtimestamp(r[0], datetime.UTC).strftime("%H:%M:%S.%f")[:-3], "ph", r[1], r[2], r[3], r[4], f"{r[5]} ms")
