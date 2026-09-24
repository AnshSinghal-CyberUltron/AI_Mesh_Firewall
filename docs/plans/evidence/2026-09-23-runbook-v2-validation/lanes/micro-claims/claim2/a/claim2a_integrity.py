#!/usr/bin/env python3
"""claim2a_integrity.py -- consistency checks over the outputs of claim2a_emit_count.py all.
    cd <this dir> && python3 claim2a_integrity.py > integrity_checks.txt"""
import glob, json
from collections import Counter

print("## per-cell integrity (provenance, network guard, publish success, env contamination, level wiring)")
for fp in sorted(glob.glob("run_*.json")):
    r = json.load(open(fp))
    unpub = sum(v["unpublished_emits_total"] for v in r["scenarios"].values())
    print(f'{r["cell"]:16} live_repo_modules(start/end)={len(r["provenance"]["modules_from_LIVE_repo_tree"])}/'
          f'{len(r["provenance_end_of_run"]["modules_from_LIVE_repo_tree"])} baseline_modules(start/end)='
          f'{r["provenance"]["modules_from_baseline"]}/{r["provenance_end_of_run"]["modules_from_baseline"]} '
          f'blocked={len(r["blocked_network_attempts"])} unexpected={r["blocked_network_attempts_unexpected"]} '
          f'unpublished_emits={unpub} foreign_env_names={r["env_names_present_not_set_by_harness"]} '
          f'gateway.setLevel(DEBUG)->{[c["applied"] for c in r["gateway_setLevel_calls"]]} '
          f'logger_levels(gateway/bedrock/root)={r["logger_state"]["gateway"]["level"]}/{r["logger_state"]["bedrock"]["level"]}/{r["logger_state"]["root"]["level"]} '
          f'fake_converse_calls={r["fake_bedrock_converse_calls"]}')
print("\n## blocked-attempt classifications (all cells)")
c = Counter()
for fp in sorted(glob.glob("run_*.json")):
    for b in json.load(open(fp))["blocked_network_attempts"]:
        c[(b["op"], b["phase"], b["classification"][:60])] += 1
for k, v in c.items():
    print(v, k)
print("\n## DEBUG records that reached the ROOT StreamHandler (root logger level INFO) -- measured requests")
for fp in sorted(glob.glob("records_root_*_DEBUG.jsonl")):
    n = Counter()
    for line in open(fp):
        x = json.loads(line)
        if x["req_phase"] == "measured" and x["attr"] != "unattributed":
            n[x["levelname"]] += 1
    print(fp, dict(n))
print("\n## cross-check: DEBUG-cell non-DEBUG emits/request == INFO-cell emits/request (median)")
for p in ("P1", "P2", "P2b", "P2t2"):
    d = json.load(open(f"run_{p}_DEBUG.json"))["scenarios"]; i = json.load(open(f"run_{p}_INFO.json"))["scenarios"]
    for s in d:
        nd, iv = d[s]["non_debug_emits_per_request"]["median"], i[s]["emits_per_request"]["median"]
        bd = d[s]["by_logger_level_mean_per_req"].get("bedrock|DEBUG", 0)
        print(f"{p:5} {s:20} debug_nonDEBUG={nd} + bedrock_DEBUG(kept, BEDROCK_LOG_LEVEL=DEBUG)={bd} -> expect {nd + bd}; INFO cell={iv}: {'OK' if nd + bd == iv else 'MISMATCH'}")
