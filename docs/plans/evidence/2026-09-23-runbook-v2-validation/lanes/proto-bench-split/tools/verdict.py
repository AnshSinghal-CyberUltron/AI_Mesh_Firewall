#!/usr/bin/env python3
"""verdict.py RUN_DIR -> one JSON line: the controller's step rule (2026-09-24):
PASS iff C4 p99 (c4_client.py over every stream, JSON + SSE) < 20 ms AND infra <= 0.1% of offered AND 0 drops.
Also carries the load-rule metrics (JSON / SSE-total p99, split_metrics.py) for the table."""
import json
import sys
from pathlib import Path

run = Path(sys.argv[1])
m = json.loads((run / "split_metrics.json").read_text())
c = json.loads((run / "c4.json").read_text())
c4 = c["C4_T_fw_addon_ABC"].get("p99")
L = m["latency_ms"]
direct = m["mode"] == "direct"
ok = (c4 is not None and c4 != float("inf") and c4 < 20.0) and (m["infra_rate"] or 0) <= 0.001 and m["drops"] == 0
if direct:
    ok = m["categories"].get("infra", 0) == 0 and m["drops"] == 0
print(json.dumps({"run": run.name, "verdict": "PASS" if ok else "FAIL", "rule": "C4 p99 < 20 ms, infra <= 0.1%, 0 drops",
                  "c4_p99": c4, "c4_p999": c["C4_T_fw_addon_ABC"].get("p999"), "c4_sse_p99": c["C4_sse"].get("p99"),
                  "c4_json_p99": c["C4_json"].get("p99"), "qualified_rps": m["qualified_rps"], "infra_pct": m["infra_pct"],
                  "drops": m["drops"], "json_total_p99": L["JSON_total"].get("p99"), "sse_total_p99": L["SSE_total"].get("p99"),
                  "sse_first_p99": L["SSE_first_tok1"].get("p99"), "load_rule_verdict": m["verdict"]}))
