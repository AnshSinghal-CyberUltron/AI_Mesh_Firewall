#!/usr/bin/env python3
"""Line-count matrix of candidate 'block/terminal-decision' regexes across a package.
Usage: regex_matrix.py <dir> [--include-tests]
Prints for each regex: matching lines total, #modules with >=1 match, count in main.py, mcp_proxy.py."""
import os, re, sys, json, itertools
root = sys.argv[1]
inc_tests = "--include-tests" in sys.argv
files = []
for dp, dn, fn in os.walk(root):
    dn[:] = [d for d in dn if d not in ("__pycache__", ".claude-flow", ".swarm", "graphify-out", ".venv", "node_modules")
             and (inc_tests or d != "tests")]
    for f in fn:
        if f.endswith(".py") and (inc_tests or (not f.startswith("test_") and f != "conftest.py")):
            files.append(os.path.join(dp, f))
files.sort()
Q = r"[\"']"
C = {
 "q_block_literal": rf"{Q}block{Q}",
 "q_block_literal_not_compare": rf"(?<![=!]=\s){Q}block{Q}(?!\s*[,)]?\s*(?:in\b))",
 "action_eq_block(assign/kw)": rf"\b\w*action\w*\s*=\s*{Q}block{Q}",
 "decision_eq_block(assign/kw)": rf"\b\w*decision\w*\s*=\s*{Q}block{Q}",
 "any_ident_eq_block(assign/kw)": rf"\b\w+\s*=\s*{Q}block{Q}",
 "dict_action_block": rf"{Q}\w*action{Q}\s*:\s*{Q}block{Q}",
 "return_block_literal": rf"\breturn\s+{Q}block{Q}",
 "compare_eq_block": rf"[=!]=\s*{Q}block{Q}",
 "status_code_403": r"status_code\s*=\s*403",
 "status_4xx_literal": r"status_code\s*=\s*4\d\d",
 "build_block_response": r"_build_(safe_)?block_response\(",
 "blocked_true": r"\b\w*blocked\w*\s*=\s*True",
 "should_block_true": r"should_block\w*\s*=\s*True",
 "event_type_blocked": rf"event_type\s*=\s*{Q}\w*blocked{Q}",
 "METRICS_blocked_inc": rf"METRICS\[{Q}blocked{Q}\]\s*\+=",
 "return_JSONResponse_4xx": r"return\s+JSONResponse\(\s*(status_code\s*=\s*)?4\d\d",
}
res = {}
for name, rx in C.items():
    r = re.compile(rx)
    tot = 0; mods = 0; per = {}
    for p in files:
        n = 0
        for line in open(p, encoding="utf-8"):
            s = line.lstrip()
            if s.startswith("#"):
                continue
            if r.search(line):
                n += 1
        if n:
            mods += 1; per[os.path.relpath(p, root)] = n; tot += n
    res[name] = {"lines": tot, "modules": mods, "main.py": per.get("main.py", 0),
                 "mcp_proxy.py": per.get("mcp_proxy.py", 0), "per_module": per}
print(json.dumps({"n_files": len(files), "results": res}, indent=1))
