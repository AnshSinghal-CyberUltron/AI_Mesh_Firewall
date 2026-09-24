#!/usr/bin/env python3
"""reviewer-observability / v1: join v1's own audit events (policy_enforcementevent export, event_type
request|stream_complete) to the client (olg, ALL phases) and provider (synthprov) records of the same run.

Join key: event.client_corr == olg rid (x-request-id), else the harness nonce in event.inp.
Per client record -> client outcome (ok | timeout | http_NNN:code | other err) x audit (none | action/status/
completed/had_error) x provider (calls, status, client_gone, tokens_out vs max_tokens).
Also: events that match no client record (orphans), events matching >1 client record, duplicate events per
request, and the lane's "audit completeness" (matched client records / all client records).
usage: audit_join.py RUN_DIR [--json OUT]
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

import orjson

NONCE_RE = re.compile(r"\(ref-[a-z]+(?:-[a-z]+){5}\)")


def open_any(p: Path):
    s = str(p)
    if s.endswith(".zst"):
        from compression import zstd
        return zstd.open(s, "rb")
    return open(s, "rb")


def find(d: Path, name: str):
    for c in (name, name + ".zst"):
        if (d / c).exists():
            return d / c
    return None


def client_outcome(c):
    st, err, det = c.get("status", 0), c.get("err"), (c.get("err_detail") or "")
    if st == 200 and not err and c.get("done_seen"):
        return "ok"
    if err in ("timeout", "drain_cancel", "connect", "transport", "eof_mid_stream", "no_done"):
        return f"{err}" + ("@200" if st == 200 else "")
    code = det.split("code=")[-1].split()[0] if "code=" in det else ""
    return f"http_{st}:{code}" if st else (err or "other")


def main():
    R = Path(sys.argv[1])
    lgd = next(R.glob("rv-v1-lg-*/lg"))
    pvd = next(R.glob("rv-v1-prov-*/prov"))
    events = []
    with open_any(R / "sut" / "v1-events.jsonl.zst") as fh:
        for line in fh:
            if line.strip():
                events.append(orjson.loads(line))
    by_key = defaultdict(list)
    for i, e in enumerate(events):
        if e.get("client_corr"):
            by_key["rid:" + e["client_corr"]].append(i)
        m = NONCE_RE.search(e.get("inp") or "")
        if m:
            by_key["nonce:" + m.group(0)].append(i)
    prov = {}
    pcalls = Counter()
    with open_any(find(pvd, "records.jsonl")) as fh:
        for line in fh:
            if line.strip():
                r = orjson.loads(line)
                pcalls[r["rid"]] += 1
                prov[r["rid"]] = r
    matrix = Counter()
    ev_used = Counter()
    n = matched = 0
    by_phase = Counter()
    per_outcome = defaultdict(Counter)
    examples = defaultdict(list)
    with open_any(find(lgd, "requests.jsonl")) as fh:
        for line in fh:
            if not line.strip():
                continue
            c = orjson.loads(line)
            n += 1
            idx = by_key.get("rid:" + c["rid"]) or by_key.get("nonce:" + str(c.get("nonce"))) or []
            uniq = sorted(set(idx))
            for i in uniq:
                ev_used[i] += 1
            out = client_outcome(c)
            p = prov.get(c["rid"])
            pc = pcalls.get(c["rid"], 0)
            if p is None:
                ptag = "prov:none"
            else:
                full = (p.get("tokens_out") == p.get("max_tokens_req")) if p.get("max_tokens_req") else None
                ptag = f"prov:{p.get('status')}" + (":client_gone" if p.get("client_gone") else "") + \
                       ("" if full in (None, True) else ":partial") + (f":calls{pc}" if pc > 1 else "")
            if uniq:
                matched += 1
                e = events[uniq[0]]
                atag = f"audit:{e.get('et')}:{e.get('action')}:{e.get('status_code')}:completed={e.get('completed')}:err={e.get('had_error')}"
                if len(uniq) > 1:
                    atag += f":DUP{len(uniq)}"
            else:
                atag = "audit:none"
            key = (out, atag, ptag)
            matrix[key] += 1
            by_phase[(c.get("ph"), "matched" if uniq else "unmatched")] += 1
            per_outcome[out]["n"] += 1
            if uniq:
                per_outcome[out]["audited"] += 1
            if len(examples[key]) < 2:
                examples[key].append({"rid": c["rid"], "status": c.get("status"), "err": c.get("err"),
                                      "err_detail": c.get("err_detail"), "end_ms": round((c.get("end_ns") or 0) / 1e6, 1),
                                      "stream": c.get("stream"), "event_id": events[uniq[0]].get("id") if uniq else None,
                                      "event_total_ms": events[uniq[0]].get("total_ms") if uniq else None})
    orphans = [i for i in range(len(events)) if ev_used[i] == 0]
    multi = [i for i in range(len(events)) if ev_used[i] > 1]
    res = {
        "run": R.name, "client_records_all_phases": n, "events": len(events), "matched_client_records": matched,
        "audit_completeness_matched_over_client": round(matched / n, 4) if n else None,
        "events_over_client": round(len(events) / n, 4) if n else None,
        "orphan_events": len(orphans), "events_matching_multiple_client_records": len(multi),
        "orphan_event_mix": dict(Counter((events[i].get("et"), events[i].get("action"), events[i].get("status_code"),
                                          events[i].get("completed")) for i in orphans).most_common(10)),
        "by_phase": {f"ph{k[0]}_{k[1]}": v for k, v in sorted(by_phase.items(), key=str)},
        "per_client_outcome": {k: dict(v) for k, v in per_outcome.items()},
        "matrix": [{"client": k[0], "audit": k[1], "provider": k[2], "n": v} for k, v in matrix.most_common()],
        "examples": {" | ".join(k): v for k, v in examples.items()},
    }
    s = json.dumps(res, indent=1, default=str)
    if len(sys.argv) > 3 and sys.argv[2] == "--json":
        Path(sys.argv[3]).write_text(s)
    print(s)


if __name__ == "__main__":
    main()
