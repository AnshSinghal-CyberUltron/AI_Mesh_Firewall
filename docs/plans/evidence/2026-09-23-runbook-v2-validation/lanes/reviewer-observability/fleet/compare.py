#!/usr/bin/env python3
"""Compare reviewer recompute (out/<run>.json) with the lane's pbf_summary.json; print a markdown table."""
import json, sys
from pathlib import Path
FO = Path(__file__).resolve().parent
LANE = Path("/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-fleet/runs")
rows = []
for f in sorted((FO / "out").glob("*.json")):
    r = f.stem
    me = json.loads(f.read_text())
    ls = json.loads((LANE / r / "pbf_summary.json").read_text())
    st = ls.get("step") or {}
    off = me["offered"]
    cat = me["categories"]
    q = cat.get("qualified", 0); inf = cat.get("infra", 0); fp = cat.get("block_fp", 0) + cat.get("block_expected", 0)
    def g(d, k): return (d or {}).get(k)
    rows.append({
        "run": r, "units": len((st.get("units") or "").split()), "rate": st.get("rate"),
        "tgt": "edge" if ":8080" in (st.get("targets") or "") else "direct",
        "q(me/lane)": f"{q}/{ls['qualified']}", "infra(me/lane)": f"{inf}/{ls['infra_errors']}",
        "blk(me/lane)": f"{fp}/{ls['policy_block_fp']+ls['policy_block_expected']}",
        "infra%": round(100 * inf / off, 3),
        "p99fw me/lane": f"{g(me['PUB_T_fw_addon'],'p99')}/{round(ls['T_fw_addon']['p99'],3)}",
        "p99nohold me/lane": f"{g(me['PUB_nohold_T_addon_total'],'p99')}/{round(ls['T_fw_addon_nohold']['p99'],3)}",
        "first p99 me/lane": f"{g(me['PUB_first_byte_sse'],'p99')}/{round(ls['T_addon_first_sse']['p99'],3)}",
        "lag p99 me/lane": f"{g(me['sample_lag'],'p99')}/{round(ls['T_release_lag_max']['p99'],3)}",
        "C nohold p99": g(me["C_nohold"], "p99"), "C nohold p99.9": g(me["C_nohold"], "p999"),
        "C fw p99": g(me["C_T_fw_addon"], "p99"),
        "ABC fw p50/p99": f"{g(me['ABC_T_fw_addon'],'p50')}/{g(me['ABC_T_fw_addon'],'p99')}",
        "tok1 p50/p99": f"{g(me['sample_first_tok1_sse'],'p50')}/{g(me['sample_first_tok1_sse'],'p99')}",
        "ABC json p99": g(me["ABC_json"], "p99"),
        "lane knee": "PASS" if ls.get("load_knee_pass") else "FAIL",
        "knee under (c)": "PASS" if (g(me["C_nohold"], "p99") not in (None, float('inf')) and g(me["C_nohold"], "p99") < 20 and inf / off <= 0.001) else "FAIL",
    })
cols = list(rows[0])
print("| " + " | ".join(cols) + " |"); print("|" + "---|" * len(cols))
for x in rows:
    print("| " + " | ".join(str(x[c]) for c in cols) + " |")
