#!/usr/bin/env python3
"""Block/terminal-decision sites under three explicit AST definitions.
Usage: decision_sites.py <pkg_dir> <out_items.json>
DS-A  'block' produced as a value: every str constant == "block" that is NOT (a) an operand of a
      comparison / membership test (incl. elements of a tuple/list/set that is such an operand),
      (b) a dict key, (c) a docstring. Counted per distinct (module,line).
DS-B1 terminal refusal responses: `return X` / `raise X` where X (after await) is a call to
      JSONResponse/Response/PlainTextResponse/StreamingResponse/HTTPException with a literal
      status 400-499 or 403-family (literal int 4xx), OR a call whose callee name matches
      /block|reject|deny|refus|forbid/ (e.g. _build_block_response).
DS-B2 policy-block subset of B1: literal status 403, or callee name matches /block|deny|refus|forbid/.
DS-C  broad grep-equivalent: any non-comment code line containing the quoted literal 'block'.
DS-D  all terminal refusal returns: `return X`/`raise X` where X (after await) is a response-class call
      with a literal 4xx status, OR a response-class call (any/dynamic/200 status) whose arguments contain a
      dict with an "error" key (HTTP or JSON-RPC error envelope), OR a call whose callee name matches
      /block|reject|deny|refus|forbid|error_response|too_large/. Literal 5xx responses are excluded
      (upstream/internal failures, not decisions).
DS-AD union of DS-A and DS-D by (module,line).
"""
import ast, os, re, sys, json
from collections import defaultdict
pkg, outp = sys.argv[1], sys.argv[2]
files = []
for dp, dn, fn in os.walk(pkg):
    dn[:] = [d for d in dn if d not in ("tests", "__pycache__", ".claude-flow", ".swarm", "graphify-out")]
    for f in fn:
        if f.endswith(".py") and f != "conftest.py" and not f.startswith("test_"):
            files.append(os.path.join(dp, f))
files.sort()
RESP = {"JSONResponse", "Response", "PlainTextResponse", "StreamingResponse", "ORJSONResponse", "HTMLResponse",
        "HTTPException", "_SSEResponse"}
NAMERX = re.compile(r"block|reject|deny|refus|forbid", re.I)
NAMERX_POLICY = re.compile(r"block|deny|refus|forbid", re.I)
def callee(c):
    f = c.func
    return f.attr if isinstance(f, ast.Attribute) else f.id if isinstance(f, ast.Name) else None
def status_of(c):
    for kw in c.keywords:
        if kw.arg == "status_code":
            return kw.value.value if isinstance(kw.value, ast.Constant) else "dyn"
    nm = callee(c)
    if nm == "HTTPException" and c.args:
        a = c.args[0]; return a.value if isinstance(a, ast.Constant) else "dyn"
    if nm in RESP and len(c.args) >= 2:
        a = c.args[1]; return a.value if isinstance(a, ast.Constant) else "dyn"
    return None
items = {"DS-A": [], "DS-B1": [], "DS-B2": [], "DS-C": [], "DS-D": [], "DS-AD": []}
NAMERX_D = re.compile(r"block|reject|deny|refus|forbid|error_response|too_large", re.I)
def has_error_key(c):
    for d in ast.walk(c):
        if isinstance(d, ast.Dict) and any(isinstance(k, ast.Constant) and k.value == "error" for k in d.keys):
            return True
    return False
for p in files:
    rel = os.path.relpath(p, pkg)
    src = open(p, encoding="utf-8").read()
    t = ast.parse(src)
    parents = {}
    for n in ast.walk(t):
        for c in ast.iter_child_nodes(n):
            parents[c] = n
    doc = set()
    for n in ast.walk(t):
        if isinstance(n, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and n.body \
           and isinstance(n.body[0], ast.Expr) and isinstance(n.body[0].value, ast.Constant):
            doc.add(n.body[0].value)
    seenA = set()
    for n in ast.walk(t):
        if isinstance(n, ast.Constant) and n.value == "block" and n not in doc:
            par = parents[n]
            if isinstance(par, ast.Compare):
                continue
            if isinstance(par, (ast.Tuple, ast.List, ast.Set)) and isinstance(parents.get(par), ast.Compare):
                continue
            if isinstance(par, ast.Dict) and any(k is n for k in par.keys):
                continue
            if n.lineno not in seenA:
                seenA.add(n.lineno)
                items["DS-A"].append([rel, n.lineno, src.splitlines()[n.lineno - 1].strip()[:140]])
    for n in ast.walk(t):
        if isinstance(n, (ast.Return, ast.Raise)):
            v = n.value if isinstance(n, ast.Return) else n.exc
            if isinstance(v, ast.Await):
                v = v.value
            if not isinstance(v, ast.Call):
                continue
            nm = callee(v)
            if nm is None:
                continue
            st = status_of(v) if nm in RESP else None
            b1 = (nm in RESP and isinstance(st, int) and 400 <= st <= 499) or bool(NAMERX.search(nm))
            b2 = (nm in RESP and st == 403) or bool(NAMERX_POLICY.search(nm))
            rec = [rel, n.lineno, f"{type(n).__name__} {nm} status={st}"]
            if b1: items["DS-B1"].append(rec)
            if b2: items["DS-B2"].append(rec)
            is5xx = isinstance(st, int) and st >= 500
            bd = (nm in RESP and isinstance(st, int) and 400 <= st <= 499) or \
                 (nm in RESP and not is5xx and has_error_key(v)) or bool(NAMERX_D.search(nm))
            if bd: items["DS-D"].append(rec)
    rx = re.compile(r"[\"']block[\"']")
    for i, l in enumerate(src.splitlines(), 1):
        if not l.lstrip().startswith("#") and rx.search(l):
            items["DS-C"].append([rel, i, l.strip()[:140]])
a = {(r[0], r[1]) for r in items["DS-A"]}
d = {(r[0], r[1]) for r in items["DS-D"]}
items["DS-AD"] = [[m, l, "A" if (m, l) in a and (m, l) not in d else "D" if (m, l) not in a else "A+D"] for m, l in sorted(a | d)]
summary = {}
for k, v in items.items():
    per = defaultdict(int)
    for rel, ln, _ in v:
        per[rel] += 1
    summary[k] = {"sites": len(v), "modules": len(per), "mcp_proxy.py": per.get("mcp_proxy.py", 0),
                  "main.py": per.get("main.py", 0), "per_module": dict(sorted(per.items(), key=lambda x: -x[1]))}
json.dump({"n_files": len(files), "summary": summary, "items": items}, open(outp, "w"), indent=1)
print(json.dumps({"n_files": len(files), **{k: {kk: vv for kk, vv in v.items() if kk != 'per_module'} for k, v in summary.items()}}, indent=1))
for k in summary: print(k, summary[k]["per_module"])
