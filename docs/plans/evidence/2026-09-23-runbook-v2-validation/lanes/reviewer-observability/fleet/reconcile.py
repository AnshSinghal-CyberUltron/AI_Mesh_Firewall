#!/usr/bin/env python3
"""Whole-run (snap-pre -> snap-post) reconciliation of gateway counters against the wire:
client olg records (all phases), synthprov records, merged over all units of the run."""
import json, sys
from collections import Counter
from pathlib import Path
import orjson
FO = Path(__file__).resolve().parent
sys.path.insert(0, str(FO)); sys.path.insert(0, str(FO.parent))
import fleet_gw  # noqa: E402
import recompute as R  # noqa: E402

def client_counts(rd: Path):
    st = Counter(); disp = Counter(); stages_u = 0; n = 0; errs = Counter()
    for d in sorted(rd.glob("rv-pbf-lg-*/lg")):
        with R.open_any(R.find(d, "requests.jsonl")) as fh:
            for line in fh:
                c = orjson.loads(line); n += 1
                st[c.get("status", 0)] += 1
                disp[(c.get("status", 0), c.get("disp") or "")] += 1
                if c.get("err"): errs[c["err"] + ":" + (c.get("err_detail") or "")[:40]] += 1
                if ":U" in (c.get("stages") or ""): stages_u += 1
    return n, st, disp, stages_u, errs

def prov_count(rd: Path):
    n = 0; st = Counter()
    for d in sorted(rd.glob("rv-pbf-prov-*/prov")):
        with R.open_any(R.find(d, "records.jsonl")) as fh:
            for line in fh:
                if line.strip():
                    n += 1; st[orjson.loads(line).get("status")] += 1
    return n, st

rows = []
for f in sorted((FO / "out").glob("*.json")):
    r = f.stem; rd = fleet_gw.RAW / r
    try:
        gw = fleet_gw.run_side(r, "pre", "post")
    except Exception as e:  # noqa: BLE001
        print(r, "ERR", e, file=sys.stderr); continue
    wc, oc = gw["w_count"], gw["o_count"]
    n, st, disp, su, errs = client_counts(rd)
    pn, pst = prov_count(rd)
    shed = sum(v for k, v in wc.items() if k.startswith("shed{"))
    blk_cli = sum(v for (s, dd), v in disp.items() if s == 403 and dd == "BLOCK")
    row = {"run": r, "client_all": n, "gw_admitted": wc.get("admitted"), "c200": st.get(200, 0),
           "gw_ALLOW": wc.get("disposition_ALLOW"), "gw_provider_calls": wc.get("provider_calls"), "synthprov_records": pn,
           "cli403BLOCK": blk_cli, "gw_BLOCK": wc.get("disposition_BLOCK"), "cli503": st.get(503, 0), "gw_shed": shed,
           "other_status": {k: v for k, v in st.items() if k not in (200, 403, 503)},
           "audit_enq": wc.get("audit_enqueued"), "2A+B": 2 * wc.get("disposition_ALLOW", 0) + wc.get("disposition_BLOCK", 0),
           "audit_written": wc.get("audit_written"), "audit_dropped": wc.get("audit_dropped", 0),
           "W_guard_windows": wc.get("guard_windows"), "O_guard_windows": oc.get("guard_windows"),
           "O_owner_requests": oc.get("owner_requests"), "admitted-shed": wc.get("admitted", 0) - shed,
           "gw_notes": gw["notes"][:3], "procs_w": gw["procs_w"], "cli_stage_U": su,
           "prov_status": dict(pst), "client_err_top": dict(errs.most_common(4))}
    ok = (row["client_all"] == row["gw_admitted"] and row["gw_ALLOW"] == row["gw_provider_calls"] == row["synthprov_records"] == row["c200"]
          and row["cli403BLOCK"] == row["gw_BLOCK"] and row["cli503"] == row["gw_shed"] and row["audit_enq"] == row["2A+B"] == row["audit_written"]
          and row["W_guard_windows"] == row["O_guard_windows"] and row["O_owner_requests"] == row["admitted-shed"])
    row["ALL_MATCH"] = ok
    rows.append(row)
    print(json.dumps(row))
