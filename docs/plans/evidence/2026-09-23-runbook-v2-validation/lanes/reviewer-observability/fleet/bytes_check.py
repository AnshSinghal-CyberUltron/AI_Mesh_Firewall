#!/usr/bin/env python3
"""Reconstruct the edge->client IP bytes/request from olg (decoded body bytes, SSE events) + TCP/IP header
overhead from the ACCT packet counts, and compare with the lane's ACCT figure that feeds fleet_select.py.
usage: bytes_check.py RUN"""
import json, re, sys
from collections import Counter
from pathlib import Path
import orjson
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import recompute as R  # noqa: E402
RAW = Path.home() / "rv-evidence-raw/proto-bench-fleet/runs"

def acct(p):
    out = {}
    for line in p.read_text().splitlines():
        m = re.match(r"\s*(\d+)\s+(\d+)\s+.*/\*\s*(\S+)\s*\*/", line)
        if m:
            out[m.group(3)] = (int(m.group(1)), int(m.group(2)))
    return out

run = sys.argv[1]; rd = RAW / run
step = json.loads((rd / "step.json").read_text())
edge = [d for d in rd.glob("rv-pbf-edge-*/sut") if (d / "snap-meas_start/iptables_acct.txt").exists()]
by_rid, calls, n2r, _ = R.load_provider(sorted(rd.glob("rv-pbf-prov-*/prov")))
n = 0; body = 0; events = 0; sse = 0; tok = 0; tok_n = 0; st = Counter(); body_by = Counter(); cnt_by = Counter()
for d in sorted(rd.glob("rv-pbf-lg-*/lg")):
    with R.open_any(R.find(d, "requests.jsonl")) as fh:
        for line in fh:
            c = orjson.loads(line)
            if c.get("ph") != 2: continue
            n += 1; b = c.get("bytes") or 0; body += b
            kind = ("sse" if c.get("stream") else "json") if c.get("status") == 200 else f"http_{c.get('status')}"
            body_by[kind] += b; cnt_by[kind] += 1
            if c.get("stream") and c.get("status") == 200:
                sse += 1; events += c.get("events") or 0
                p = by_rid.get(c["rid"])
                if p and p.get("tokens_out"): tok += p["tokens_out"]; tok_n += 1
res = {"run": run, "offered": n, "body_B_per_offered": round(body / n, 1),
       "body_B_mean_by_kind": {k: round(body_by[k] / cnt_by[k], 1) for k in cnt_by}, "count_by_kind": dict(cnt_by),
       "sse_events_per_response": round(events / sse, 1) if sse else None,
       "sse_tokens_out_mean": round(tok / tok_n, 1) if tok_n else None,
       "sse_body_B_per_output_token": round(body_by["sse"] / tok, 1) if tok else None}
if edge:
    e = edge[0]
    a, b = acct(e / "snap-meas_start/iptables_acct.txt"), acct(e / "snap-meas_end/iptables_acct.txt")
    lg_ips = [k for k in b if k.startswith("out_") and b[k][1] - a.get(k, (0, 0))[1] > 0 and k.split("_", 1)[1] in
              ("10.146.0.11", "10.146.0.12", "10.146.0.9", "10.146.0.17")]
    pk = sum(b[k][0] - a[k][0] for k in lg_ips); by = sum(b[k][1] - a[k][1] for k in lg_ips)
    res.update({"edge_to_clients_IP_B_per_offered": round(by / n, 1), "edge_to_clients_pkts_per_offered": round(pk / n, 1),
                "IP_minus_body_B": round((by - body) / n, 1),
                "tcpip_hdr_B_at_52B_per_pkt": round(pk * 52 / n, 1),
                "residual_B_(http_hdr+chunk_framing)": round((by - body - pk * 52) / n, 1),
                "IP_over_body": round(by / body, 3)})
print(json.dumps(res, indent=1))
