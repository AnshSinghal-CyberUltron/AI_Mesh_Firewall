#!/usr/bin/env python3
"""v1 adapter: attach v1's OWN per-request audit evidence to olg records so the shared analyzer
(harness/analyze.py --mode sut) can apply the QUALIFIED rule to v1.

v1 sends no x-rv-disposition / x-rv-stages headers. Its per-request record of what it did is the
audit event it writes for every request (Postgres policy_enforcementevent, metadata.event_type =
'request'): action allow|redact|block and pipeline_trace.stages[] {name, action, latency_ms}. The
event's input_text carries the prompt, whose first user bytes are the harness nonce "(ref-a-b-c-d-e-f)".

For each olg record: disp := event.action upper-cased; stages := "<name>:E" when the stage action is
not "skip" else "<name>:S", over v1's nine stages. Records with no audit event keep no disposition
and are therefore NOT qualified (and are counted as audit gaps). Raw olg files are never modified:
the augmented copy is written to --out-dir. Also emits v1's internal decomposition per cohort.

usage: v1_adapter.py --olg LGDIR --events events.jsonl --out-dir DIR [--phases 2] [--fields xrv|v1]
  --fields xrv (default): fill disp / stages ("<name>:E|S") — the pre-READY analyzer reads these.
  --fields v1           : fill v1-native final_action (pipeline_trace.final_action from the audit event) and
                          v1_stages ("auth:allow,...,output_guardrail:skip"), NO disp/stages — for the READY
                          analyzer's --disposition-source v1 extractor.
"""
import argparse, json, re, sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, "/tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/1b4d4994-cf08-4d34-a6cb-54fd36f36877/scratchpad/harness")
from analyze import open_any, find_raw, loads, pct_sorted  # noqa: E402

NONCE_RE = re.compile(r"\(ref-[a-z]+(?:-[a-z]+){5}\)")
V1_STAGES = ("auth", "kill_switch", "rate_limit", "policy", "input_scan", "model_routing", "model_input",
             "model_output", "output_guardrail")
DISP = {"allow": "ALLOW", "redact": "REDACT", "block": "BLOCK"}


def parse_stages(s):
    out = []
    for part in (s or "").split(","):
        bits = part.split(":")
        if len(bits) >= 2:
            lat = None
            try:
                lat = float(bits[2]) if len(bits) > 2 and bits[2] else None
            except ValueError:
                pass
            out.append((bits[0], bits[1], lat))
    return out


def q(vals):
    v = sorted(x for x in vals if x is not None)
    if not v:
        return None
    return {"n": len(v), "p50": pct_sorted(v, 0.50), "p90": pct_sorted(v, 0.90), "p99": pct_sorted(v, 0.99),
            "max": v[-1]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--olg", required=True)
    ap.add_argument("--events", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--phases", default="2")
    ap.add_argument("--fields", default="xrv", choices=["xrv", "v1"])
    a = ap.parse_args()
    phases = {int(x) for x in a.phases.split(",")}

    ev_by_nonce, dup = {}, Counter()
    n_ev = 0
    with open(a.events, "rb") as fh:
        for line in fh:
            if not line.strip():
                continue
            e = json.loads(line)
            n_ev += 1
            m = NONCE_RE.search(e.get("inp") or "")
            keys = []
            if e.get("client_corr"):
                keys.append("rid:" + e["client_corr"])   # v1 records the client's x-request-id here
            if m:
                keys.append("nonce:" + m.group(0))
            for k in keys:
                if k in ev_by_nonce:
                    dup[k] += 1
                ev_by_nonce[k] = e

    src = find_raw(Path(a.olg), "requests.jsonl")
    if src is None:
        sys.exit(f"no requests.jsonl in {a.olg}")
    out_dir = Path(a.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    n = matched = 0
    by_phase = Counter()
    unmatched_by_status = Counter()
    disp_ct = Counter()
    stage_ct = Counter()
    internal = defaultdict(lambda: defaultdict(list))
    with open_any(src) as fh, open(out_dir / "requests.jsonl", "w") as out:
        for line in fh:
            if not line.strip():
                continue
            r = loads(line)
            n += 1
            e = ev_by_nonce.get("rid:" + str(r.get("rid"))) or ev_by_nonce.get("nonce:" + str(r.get("nonce")))
            if e is not None:
                matched += 1
                by_phase[(r.get("ph"), "matched")] += 1
                st = parse_stages(e.get("stages"))
                if a.fields == "v1":
                    r.pop("disp", None); r.pop("stages", None)
                    r["final_action"] = e.get("final") or e.get("action")
                    r["v1_stages"] = ",".join(f"{nm}:{act or 'skip'}" for nm, act, _ in st)
                    disp_ct[str(r["final_action"]).upper()] += 1
                else:
                    r["disp"] = DISP.get(e.get("action"), (e.get("action") or "").upper())
                    r["stages"] = ",".join(f"{nm}:{'S' if act in ('skip', '') else 'E'}" for nm, act, _ in st)
                    disp_ct[r["disp"]] += 1
                r["v1_event_id"] = e.get("id")
                r["v1_event_type"] = e.get("et")
                for nm, act, _ in st:
                    stage_ct[(nm, act)] += 1
                if r.get("ph") in phases:
                    coh = "sse" if r.get("stream") else "json"
                    for key in ("pre_ms", "post_ms", "overhead_ms", "total_ms"):
                        try:
                            internal[coh][key].append(float(e[key]) if e.get(key) is not None else None)
                        except (TypeError, ValueError):
                            pass
                    for nm, act, lat in st:
                        internal[coh]["stage:" + nm].append(lat)
            else:
                by_phase[(r.get("ph"), "unmatched")] += 1
                unmatched_by_status[r.get("status")] += 1
            out.write(json.dumps(r, separators=(",", ":")) + "\n")
    import shutil
    for p in Path(a.olg).iterdir():   # every other olg artefact (manifest, cpu, timeseries, hostinfo) as-is
        if p.is_file() and not p.name.startswith("requests.jsonl"):
            shutil.copy2(p, out_dir / p.name)
    rep = {
        "olg_records": n, "audit_events_read": n_ev, "join_keys": len(ev_by_nonce),
        "duplicate_nonce_events": sum(dup.values()), "matched": matched, "unmatched": n - matched,
        "audit_completeness": round(matched / n, 6) if n else None,
        "by_phase": {f"ph{k[0]}_{k[1]}": v for k, v in sorted(by_phase.items(), key=str)},
        "unmatched_by_http_status": {str(k): v for k, v in unmatched_by_status.items()},
        "dispositions": dict(disp_ct),
        "stage_actions": {f"{k[0]}:{k[1]}": v for k, v in sorted(stage_ct.items())},
        "v1_internal_ms_measure_phase": {coh: {k: q(v) for k, v in d.items()} for coh, d in internal.items()},
    }
    (out_dir / "v1_adapter_report.json").write_text(json.dumps(rep, indent=1))
    print(json.dumps({k: rep[k] for k in ("olg_records", "matched", "unmatched", "audit_completeness",
                                           "dispositions", "unmatched_by_http_status")}))


if __name__ == "__main__":
    main()
