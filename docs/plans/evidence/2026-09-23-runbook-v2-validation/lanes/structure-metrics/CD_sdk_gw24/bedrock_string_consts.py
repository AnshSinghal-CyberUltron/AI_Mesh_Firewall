#!/usr/bin/env python3
"""Cross-check: every AST string constant that IS an env-style name containing BEDROCK
(regex ^[A-Z0-9_]*BEDROCK[A-Z0-9_]*$) in non-test Python, with the call it is an argument of.
Usage: bedrock_string_consts.py <root>..."""
import ast, os, re, sys
from collections import defaultdict
NAME = re.compile(r"^[A-Z0-9_]*BEDROCK[A-Z0-9_]*$")
rows = []
for r in sys.argv[1:]:
    for dp, dn, fn in os.walk(r):
        dn[:] = [d for d in dn if d not in ("__pycache__", "tests", "test", ".venv", "node_modules", ".claude-flow", ".swarm")]
        for f in fn:
            if not f.endswith(".py") or f.startswith("test_") or f == "conftest.py":
                continue
            p = os.path.join(dp, f)
            t = ast.parse(open(p, encoding="utf-8").read())
            par = {}
            for n in ast.walk(t):
                for c in ast.iter_child_nodes(n):
                    par[c] = n
            for n in ast.walk(t):
                if isinstance(n, ast.Constant) and isinstance(n.value, str) and NAME.match(n.value):
                    pn = par.get(n)
                    ctx = type(pn).__name__
                    if isinstance(pn, ast.Call):
                        ctx = "call:" + ast.unparse(pn.func)
                    rows.append((n.value, os.path.relpath(p, os.path.dirname(r.rstrip('/'))), n.lineno, ctx))
by = defaultdict(list)
for v, p, l, c in rows:
    by[v].append(f"{p}:{l} [{c}]")
for v in sorted(by):
    print(v, len(by[v]))
    for x in by[v]:
        print("   ", x)
print("DISTINCT:", len(by), "prefix BEDROCK_:", sum(1 for v in by if v.startswith("BEDROCK_")))
