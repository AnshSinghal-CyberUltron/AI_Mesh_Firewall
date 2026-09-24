#!/usr/bin/env python3
"""claim2a_report_tables.py -- renders results_table.txt and record_legend.txt from the outputs of
claim2a_emit_count.py all (summary.json, run_*.json, records_pub_*.jsonl, thread_breakdown.json).

    cd <this dir> && python3 claim2a_report_tables.py
"""
import glob
import json

S = json.load(open("summary.json"))
TB = json.load(open("thread_breakdown.json"))


def mmm(d):
    return f"{d.get('min')}/{d.get('median')}/{d.get('max')}" if d else "-"


rows = ["profile    scenario              DEBUG emits/req  INFO emits/req  DEBUG-cell DEBUG-level  root StreamHandler/req  "
        "payload bytes/req  event-loop-thread emits  HTTP / action (DEBUG cell)   top (logger|level: mean/req) DEBUG cell",
        "                                 (min/med/max)    (min/med/max)   recs/req (median)        (median DEBUG/INFO)     "
        "(median DEBUG/INFO) (median DEBUG/INFO)",
        "-" * 200]
for p, sfx in [("P1", ""), ("P2", ""), ("P2b", ""), ("P2t2", ""), ("P1", "_noshim")]:
    d = S["cells"].get(f"{p}_DEBUG{sfx}", {}).get("scenarios", {})
    i = S["cells"].get(f"{p}_INFO{sfx}", {}).get("scenarios", {})
    for sc in ("s1_benign_nonstream", "s2_benign_stream", "s3_pii_nonstream", "s4_injection_block"):
        ds, is_ = d.get(sc, {}), i.get(sc, {})
        dbg_lvl = ds.get("by_level_mean_per_req", {}).get("DEBUG", 0)
        top = "; ".join(f"{k}: {v:g}" for k, v in list(ds.get("by_logger_level_mean_per_req", {}).items())[:6])
        loop_d = TB.get(f"{p}_DEBUG{sfx}", {}).get(sc, {}).get("event_loop_thread_emits_per_request", {}).get("median")
        loop_i = TB.get(f"{p}_INFO{sfx}", {}).get(sc, {}).get("event_loop_thread_emits_per_request", {}).get("median")
        rows.append(
            f"{p + sfx:10} {sc:21} {mmm(ds.get('emits_per_request')):16} {mmm(is_.get('emits_per_request')):15} "
            f"{dbg_lvl:<24g} {ds.get('root_stream_per_request', {}).get('median')}/{is_.get('root_stream_per_request', {}).get('median'):<21} "
            f"{ds.get('payload_bytes_per_request', {}).get('median')}/{is_.get('payload_bytes_per_request', {}).get('median'):<13} "
            f"{loop_d}/{loop_i:<21} {','.join(ds.get('statuses', {}))}/{','.join(ds.get('actions', {})):22} {top}")
with open("results_table.txt", "w") as f:
    f.write("## per-request RedisLogPublisher.emit() calls (gateway + bedrock publishers), 20 measured requests per "
            "scenario after 5 warm-ups, from summary.json/thread_breakdown.json\n")
    f.write("\n".join(rows) + "\n")
print("\n".join(rows))

seen = {}
for fp in sorted(glob.glob("records_pub_*.jsonl")):
    for line in open(fp):
        r = json.loads(line)
        if r["attr"] == "unattributed" or r["req_phase"] != "measured":
            continue
        msg = json.loads(r["payload"])["message"]
        head = msg.split("|")[0].strip()[:40] if r["logger"] == "bedrock" else ""
        k = (r["src"], r["logger"], r["levelname"], head)
        if k not in seen:
            seen[k] = (fp.split("records_pub_")[1][:-6], r["func"], msg[:170].replace("\n", " "))
with open("record_legend.txt", "w") as f:
    f.write("## every distinct per-request record source seen (measured requests, all cells); message = first seen\n")
    for (src, lg, lv, _h), (cell, fn, msg) in sorted(seen.items()):
        f.write(f"{src:24} {lg:24} {lv:7} func={fn} [first seen {cell}]\n    {msg!r}\n")
print(open("record_legend.txt").read())
