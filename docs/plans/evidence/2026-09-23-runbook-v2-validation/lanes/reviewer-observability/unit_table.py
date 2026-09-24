#!/usr/bin/env python3
"""reviewer-observability: unit-lane table = published (pbu_summary.json) vs independent recompute (PUB),
rule (c) / rules (a)(b)(c) / client-side C4 views, client vs gateway, and whole-run counter reconciliation.
usage: unit_table.py RUN...   (reads out/unit/<run>.json, out/c4/<run>.json, out/unit/<run>.gw.*.json)"""
import json
import sys
from collections import Counter
from pathlib import Path

import orjson

RO = Path(__file__).resolve().parent
RAW = Path.home() / "rv-evidence-raw/proto-bench-unit/runs"
sys.path.insert(0, str(RO))
import recompute as R  # noqa: E402


def j(p):
    try:
        return json.loads(Path(p).read_text())
    except (OSError, ValueError):
        return None


def g(d, *ks):
    for k in ks:
        if d is None:
            return None
        d = d.get(k) if isinstance(d, dict) else None
    return d


def client_counts_all_phases(run):
    """status / disp counts over ALL phases (to reconcile with whole-run gateway counters)."""
    cnt = Counter()
    for d in sorted((RAW / run).glob("*/lg")):
        f = R.find(d, "requests.jsonl")
        with R.open_any(f) as fh:
            for line in fh:
                c = orjson.loads(line)
                cnt["requests"] += 1
                s = c.get("status", 0)
                cnt[f"status_{s}"] += 1
                if s == 403 and (c.get("disp") or "") == "BLOCK":
                    st = R.stages_of(c.get("stages"))
                    cnt["block_U" if any(v == "U" for v in st.values()) else "block_policy"] += 1
                if c.get("err") and s == 0:
                    cnt["transport_" + c["err"]] += 1
    prov = 0
    for d in sorted((RAW / run).glob("*/prov")):
        f = R.find(d, "records.jsonl")
        with R.open_any(f) as fh:
            prov += sum(1 for line in fh if line.strip())
    cnt["provider_records"] = prov
    return cnt


def main():
    runs = sys.argv[1:]
    rows = []
    rec = []
    for r in runs:
        pub = j(RAW / r / "pbu_summary.json")
        me = j(RO / "out/unit" / f"{r}.json")
        c4 = j(RO / "out/c4" / f"{r}.json")
        gw = j(RO / "out/unit" / f"{r}.gw.meas.json")
        gww = j(RO / "out/unit" / f"{r}.gw.whole.json")
        if not me:
            continue
        rate = g(pub, "step", "rate")
        row = {
            "run": r, "rate": rate,
            "pub_q": g(pub, "qualified"), "me_q": g(me, "categories", "qualified"),
            "pub_infra": g(pub, "infra_errors"), "me_infra": g(me, "categories", "infra"),
            "pub_fp": g(pub, "policy_block_fp"), "me_fp": g(me, "categories", "block_fp"),
            "pub_fw99": g(pub, "T_fw_addon", "p99"), "me_fw99": g(me, "PUB_T_fw_addon", "p99"),
            "pub_nohold99": g(pub, "T_fw_addon_nohold", "p99"), "me_nohold99": g(me, "PUB_nohold_T_addon_total", "p99"),
            "pub_first99": g(pub, "T_addon_first_sse", "p99"), "me_first99": g(me, "PUB_first_byte_sse", "p99"),
            "pub_json99": g(pub, "T_fw_addon_json", "p99"), "me_json99": g(me, "PUB_json", "p99"),
            "C_nohold99": g(me, "C_nohold", "p99"), "C_fw99": g(me, "C_T_fw_addon", "p99"),
            "ABC_fw99": g(me, "ABC_T_fw_addon", "p99"), "ABC_n": g(me, "ABC_T_fw_addon", "n"),
            "tok1_p50": g(me, "sample_first_tok1_sse", "p50"), "tok1_p99": g(me, "sample_first_tok1_sse", "p99"),
            "C4_p99": g(c4, "C4_T_fw_addon_ABC", "p99"), "C4_p50": g(c4, "C4_T_fw_addon_ABC", "p50"),
            "C4_n": g(c4, "C4_T_fw_addon_ABC", "n"),
            "extra_p50": g(c4, "proc_extra_per_stream", "p50"), "extra_p99": g(c4, "proc_extra_per_stream", "p99"),
            "hdr_p50": g(me, "hdr_addon_sse", "p50"), "hdr_p99": g(me, "hdr_addon_sse", "p99"),
            "hdr_mean": g(me, "hdr_addon_sse", "mean_finite"),
            "gw_tin_p50": g(gw, "worker", "hist", "t_input_ns", "p50_ms"),
            "gw_tin_p99": g(gw, "worker", "hist", "t_input_ns", "p99_ms"),
            "gw_tin_mean": g(gw, "worker", "hist", "t_input_ns", "mean_ms"),
            "gw_proc_p99": g(gw, "worker", "hist", "release_processing_ns", "p99_ms"),
            "gw_lagmax_p99": g(gw, "worker", "hist", "release_lag_max_ns", "p99_ms"),
            "gw_looplag_p99": g(gw, "worker", "hist", "loop_lag_ns", "p99_ms"),
            "gw_looplag_p999": g(gw, "worker", "hist", "loop_lag_ns", "p99.9_ms"),
        }
        rows.append(row)
        if gww:
            cc = client_counts_all_phases(r)
            wc = gww["worker"]["count"]
            oc = gww["owner"]["count"]
            sheds = sum(v for k, v in wc.items() if k.startswith("shed"))
            rec.append({
                "run": r, "client_requests_all": cc["requests"], "gw_admitted": wc.get("admitted"),
                "gw_ALLOW": wc.get("disposition_ALLOW"), "gw_provider_calls": wc.get("provider_calls"),
                "provider_records": cc["provider_records"], "client_200": cc["status_200"],
                "gw_BLOCK": wc.get("disposition_BLOCK"), "client_403_block": cc["block_policy"] + cc["block_U"],
                "client_403_block_U": cc["block_U"], "gw_guard_unavailable": wc.get("guard_unavailable_findings", 0),
                "gw_shed": sheds, "client_503": cc["status_503"], "client_other": {k: v for k, v in cc.items() if k.startswith("status_") and k not in ("status_200", "status_403", "status_503")} or None,
                "audit_enqueued": wc.get("audit_enqueued"), "2A+B": (wc.get("disposition_ALLOW") or 0) * 2 + (wc.get("disposition_BLOCK") or 0),
                "audit_written": wc.get("audit_written"), "audit_dropped": wc.get("audit_dropped", 0),
                "unaudited_sheds_share": round(sheds / wc["admitted"], 5) if wc.get("admitted") else None,
                "w_guard_windows": wc.get("guard_windows"), "o_guard_windows": oc.get("guard_windows"),
                "o_owner_requests": oc.get("owner_requests"), "admitted_minus_shed": (wc.get("admitted") or 0) - sheds,
                "notes": gww["worker"]["notes"] + gww["owner"]["notes"],
            })
    cols = list(rows[0].keys()) if rows else []
    print("| " + " | ".join(cols) + " |")
    print("|" + "---|" * len(cols))
    for row in rows:
        print("| " + " | ".join("" if row[c] is None else str(row[c]) for c in cols) + " |")
    print()
    if rec:
        cols = list(rec[0].keys())
        print("| " + " | ".join(cols) + " |")
        print("|" + "---|" * len(cols))
        for row in rec:
            print("| " + " | ".join("" if row[c] is None else str(row[c]) for c in cols) + " |")


if __name__ == "__main__":
    main()
