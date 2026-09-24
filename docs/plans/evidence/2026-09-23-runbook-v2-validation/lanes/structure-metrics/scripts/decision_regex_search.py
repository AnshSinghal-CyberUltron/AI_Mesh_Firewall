#!/usr/bin/env python3
"""Brute-force: can any union (by line) of <=3 plausible 'block/terminal-decision' regexes,
over any of 4 scopes, reproduce the runbook's '283 across 25 modules' (and mcp_proxy=72)?
Usage: decision_regex_search.py <export_root>"""
import os, re, sys, itertools, json
root = sys.argv[1]
SKIP = {"__pycache__", ".claude-flow", ".swarm", "graphify-out", ".venv", "node_modules", "tests", "migrations", ".claude"}
def pyfiles(sub):
    out = []
    for dp, dn, fn in os.walk(os.path.join(root, sub)):
        dn[:] = [d for d in dn if d not in SKIP]
        for f in fn:
            if f.endswith(".py") and not f.startswith("test_") and f != "conftest.py" and not f.endswith("_test.py"):
                out.append(os.path.join(dp, f))
    return sorted(out)
scopes = {
 "S1_gateway_pkg": pyfiles("gateway/ai_mesh_gateway"),
 "S2_gateway_pkg+shared": pyfiles("gateway/ai_mesh_gateway") + pyfiles("shared"),
 "S3_+services": pyfiles("gateway/ai_mesh_gateway") + pyfiles("shared") + pyfiles("services"),
 "S4_all_runtime(gateway,shared,services,workers,control)": pyfiles("gateway") + pyfiles("shared") + pyfiles("services") + pyfiles("workers") + pyfiles("control"),
}
Q = r"[\"']"
P = {
 "P1_q_block": rf"{Q}block{Q}",
 "P2_q_block_noncompare": None,  # computed specially
 "P3_action_eq_block": rf"\w*action\w*\s*=\s*{Q}block{Q}",
 "P4_decision_eq_block": rf"decision\s*=\s*{Q}block{Q}",
 "P5_dict_action_block": rf"{Q}\w*action{Q}\s*:\s*{Q}block{Q}",
 "P6_return_block": rf"\breturn\s+{Q}block{Q}",
 "P7_status_403": r"status_code\s*=\s*403|\b403\s*,",
 "P8_build_block_response": r"_build_(safe_)?block_response\(",
 "P9_blocked_true": r"\b\w*blocked\w*\s*=\s*True",
 "P10_should_block_true": r"\bshould_block\w*\s*=\s*True",
 "P11_return_JSONResponse_4xx": r"return\s+JSONResponse\(\s*(status_code\s*=\s*)?4\d\d",
 "P12_raise_HTTPException": r"raise\s+HTTPException",
 "P14_event_type_blocked": rf"event_type\s*=\s*{Q}\w*blocked{Q}",
 "P15_q_blocked": rf"{Q}blocked{Q}",
 "P17_METRICS_blocked": rf"METRICS\[{Q}blocked{Q}\]",
 "P18_return_line_mentions_block": r"\breturn\b.*block",
 "P19_block_fn_call": r"\b\w*block\w*\(",
 "P20_any_ident_eq_block": rf"\b\w+\s*=\s*{Q}block{Q}",
}
cmp_rx = re.compile(rf"([=!]=|\bin|\bnot in)\s*\(?[^#]*{Q}block{Q}|{Q}block{Q}\s*(,[^)]*)?\)?\s*(==|!=|\bin\b)")
def match(name, line):
    if name == "P2_q_block_noncompare":
        return re.search(rf"{Q}block{Q}", line) and not cmp_rx.search(line)
    return re.search(P[name], line)
# precompute per scope per pattern: set of (file,lineno)
cache = {}
for sn, files in scopes.items():
    lines = []
    for f in files:
        try:
            for i, l in enumerate(open(f, encoding="utf-8"), 1):
                if l.lstrip().startswith("#"):
                    continue
                lines.append((f, i, l))
        except UnicodeDecodeError:
            pass
    for pn in P:
        cache[(sn, pn)] = {(f, i) for f, i, l in lines if match(pn, l)}
hits = []
names = list(P)
for sn in scopes:
    for k in (1, 2, 3):
        for combo in itertools.combinations(names, k):
            s = set().union(*(cache[(sn, c)] for c in combo))
            mods = {f for f, _ in s}
            mcp = sum(1 for f, _ in s if f.endswith("/mcp_proxy.py"))
            n = len(s)
            if abs(n - 283) <= 3 or (len(mods) == 25 and abs(n - 283) <= 30) or mcp == 72:
                hits.append({"scope": sn, "combo": combo, "lines": n, "modules": len(mods), "mcp_proxy": mcp})
single = {sn: {pn: {"lines": len(cache[(sn, pn)]), "modules": len({f for f, _ in cache[(sn, pn)]}),
                    "mcp_proxy": sum(1 for f, _ in cache[(sn, pn)] if f.endswith('/mcp_proxy.py'))} for pn in P} for sn in scopes}
exact = [h for h in hits if h["lines"] == 283 and h["modules"] == 25]
print(json.dumps({"scope_file_counts": {k: len(v) for k, v in scopes.items()}, "single_patterns": single,
                  "exact_283_25": exact, "near_hits": hits[:400], "n_near_hits": len(hits)}, indent=1))
