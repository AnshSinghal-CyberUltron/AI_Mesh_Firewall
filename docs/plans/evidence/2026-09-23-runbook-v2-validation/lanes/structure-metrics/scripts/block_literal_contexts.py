#!/usr/bin/env python3
"""Classify every AST occurrence of the string constant 'block' (and 'BLOCK', 'blocked', 'deny')
by syntactic context, per module. Usage: block_literal_contexts.py <pkg_dir> [literals...]
Non-test modules only (excludes */tests/*, conftest.py)."""
import ast, os, sys, json
from collections import Counter, defaultdict
pkg = sys.argv[1]
lits = set(sys.argv[2:]) or {"block"}
files = []
for dp, dn, fn in os.walk(pkg):
    dn[:] = [d for d in dn if d not in ("tests", "__pycache__", ".claude-flow", ".swarm", "graphify-out")]
    for f in fn:
        if f.endswith(".py") and f != "conftest.py" and not f.startswith("test_"):
            files.append(os.path.join(dp, f))
files.sort()
per_mod = defaultdict(Counter)
examples = defaultdict(list)
for p in files:
    src = open(p, encoding="utf-8").read()
    t = ast.parse(src)
    parents = {}
    for n in ast.walk(t):
        for c in ast.iter_child_nodes(n):
            parents[c] = n
    # docstring constants
    doc = set()
    for n in ast.walk(t):
        if isinstance(n, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and n.body \
           and isinstance(n.body[0], ast.Expr) and isinstance(n.body[0].value, ast.Constant):
            doc.add(n.body[0].value)
    for n in ast.walk(t):
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and n.value in lits and n not in doc:
            par = parents.get(n)
            ctx = type(par).__name__
            if isinstance(par, ast.Compare):
                ctx = "Compare"
            elif isinstance(par, (ast.Tuple, ast.List, ast.Set)):
                gp = parents.get(par)
                ctx = f"{type(par).__name__}-in-{type(gp).__name__}"
            elif isinstance(par, ast.keyword):
                ctx = f"kw:{par.arg}"
            elif isinstance(par, ast.Dict):
                i = [id(v) for v in par.values].index(id(n)) if id(n) in [id(v) for v in par.values] else None
                if i is not None:
                    k = par.keys[i]
                    ctx = f"dictval:{k.value if isinstance(k, ast.Constant) else ast.unparse(k) if k else '**'}"
                else:
                    ctx = "dictkey"
            elif isinstance(par, ast.Assign):
                ctx = "Assign:" + ",".join(ast.unparse(tt) for tt in par.targets)[:40]
            elif isinstance(par, ast.Call):
                ctx = "callarg:" + ast.unparse(par.func)[:40]
            rel = os.path.relpath(p, pkg)
            per_mod[rel][ctx] += 1
            if len(examples[ctx]) < 3:
                examples[ctx].append(f"{rel}:{n.lineno}")
agg = Counter()
for m, c in per_mod.items():
    agg.update(c)
print(json.dumps({"n_files_scanned": len(files), "context_totals": agg.most_common(),
                  "per_module_totals": {m: sum(c.values()) for m, c in sorted(per_mod.items())},
                  "examples": examples}, indent=1))
