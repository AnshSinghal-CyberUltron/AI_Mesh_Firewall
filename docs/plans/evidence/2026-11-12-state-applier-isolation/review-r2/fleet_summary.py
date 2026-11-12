"""Author, PR #136 round 2: reviewer B's fleet_attack JSONL, before (B's run) vs after (this head).

python -I fleet_summary.py <before.jsonl> <after.jsonl>. Same cells as B's summarize.py, with any
counter name accepted (round 2 adds held-back gauges and the in-flight counter)."""
import json
import sys


def cell(r: dict) -> str:
    v = "; ".join(f"{o}={s}" for o, s in r["victims_past_bound"].items()) or "-"
    hk = r.get("hostile_key_models_after") or {}
    hk = (" hostile-key/models=" + ",".join(f"{k}:{s}" for k, s in hk.items())) if hk else ""
    ctr = ",".join(f"{k.replace('amf_state_', '')}={v}" for k, v in r["counters"].items()) or "none"
    return (f"cycle: {r['attack_cycle']} (raised {r['cycles_raised']}/{r['loop_cycles'] + 1}) | "
            f"served after bound {r['served_past_bound']}, revisions {r['revisions_applied']}, "
            f"ready={r['ready_past_bound'][0]} | victim {v}{hk} | counters {ctr} | "
            f"max GIL gap {r['max_gil_gap_ms']} ms, cycle {r['attack_cycle_wall_ms']} ms wall | "
            f"canary in logs={r['canary_in_logs']} text={r['exception_text_markers_in_logs']}")


before = {tuple(r["scenarios"]): r for r in map(json.loads, open(sys.argv[1]))}
after = {tuple(r["scenarios"]): r for r in map(json.loads, open(sys.argv[2]))}
for key in before:
    name = key[0] if len(key) == 1 else f"{len(key)} scenarios in one round"
    print(f"### {name}")
    print(f"  before 1dba148a: {cell(before[key])}")
    print(f"  after  {sys.argv[3] if len(sys.argv) > 3 else 'head'}: {cell(after[key]) if key in after else 'n/a'}")
