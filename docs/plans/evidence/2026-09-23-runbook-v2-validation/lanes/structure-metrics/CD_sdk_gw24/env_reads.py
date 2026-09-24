#!/usr/bin/env python3
"""AST inventory of environment-variable READS in Python code.
Detects: os.environ.get(X) / os.getenv(X) / environ.get(X) / getenv(X) / os.environ[X] (Load) /
X in os.environ / os.environ.setdefault(X) / os.environ.pop(X,..) (counted as read_or_pop), plus
one level of wrapper helpers: a function whose body passes one of its parameters as the name
argument of a direct read (e.g. def _env_int(name, d): return int(os.getenv(name, d))) -> literal
first-arg calls to that helper count as indirect reads. Aliases like _getenv = os.getenv too.
Django-environ/decouple style env("X")/config("X") calls are reported separately if the module
imports environ/decouple.
Usage: env_reads.py <out.json> <root> [<root>...] [--exclude-tests] [--prefix BEDROCK_]
"""
import ast, os, sys, json
from collections import defaultdict
args = [a for a in sys.argv[1:] if not a.startswith("--")]
out_path, roots = args[0], args[1:]
excl_tests = "--exclude-tests" in sys.argv
prefix = None
if "--prefix" in sys.argv:
    prefix = sys.argv[sys.argv.index("--prefix") + 1]
    roots = [r for r in roots if r != prefix]
SKIP_DIRS = {"__pycache__", ".venv", "venv", "node_modules", ".claude-flow", ".swarm", "graphify-out", ".git"}


def is_test(path):
    b = os.path.basename(path)
    parts = path.split(os.sep)
    return b.startswith("test_") or b.endswith("_test.py") or b == "conftest.py" or "tests" in parts or "test" in parts


def dotted(e):
    parts = []
    while isinstance(e, ast.Attribute):
        parts.append(e.attr); e = e.value
    if isinstance(e, ast.Name):
        parts.append(e.id)
        return ".".join(reversed(parts))
    return None


DIRECT_CALLS = {"os.environ.get", "os.getenv", "environ.get", "getenv", "os.environ.setdefault",
                "environ.setdefault", "os.environ.pop", "environ.pop"}


def name_of(arg):
    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
        return arg.value, "literal"
    return ast.unparse(arg), "dynamic"


def scan_file(path):
    src = open(path, encoding="utf-8", errors="replace").read()
    try:
        t = ast.parse(src)
    except SyntaxError as e:
        return None, str(e)
    reads = []
    aliases = set()
    for n in ast.walk(t):
        if isinstance(n, ast.Assign) and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name):
            d = dotted(n.value)
            if d in DIRECT_CALLS:
                aliases.add(n.targets[0].id)
    # wrappers: functions with a param used as first arg of a direct read
    wrappers = {}
    for n in ast.walk(t):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            params = [a.arg for a in n.args.posonlyargs + n.args.args + n.args.kwonlyargs]
            for c in ast.walk(n):
                a0 = None
                if isinstance(c, ast.Call) and (dotted(c.func) in DIRECT_CALLS or dotted(c.func) in aliases) and c.args:
                    a0 = c.args[0]
                elif isinstance(c, ast.Subscript) and dotted(c.value) in ("os.environ", "environ"):
                    a0 = c.slice
                if a0 is not None:
                    names_used = {x.id for x in ast.walk(a0) if isinstance(x, ast.Name)}
                    for i, p in enumerate(params):
                        if p in names_used:
                            wrappers[n.name] = (i, p, ast.unparse(a0))
    for n in ast.walk(t):
        if isinstance(n, ast.Call):
            d = dotted(n.func)
            if (d in DIRECT_CALLS or d in aliases) and n.args:
                nm, kind = name_of(n.args[0])
                reads.append({"name": nm, "kind": kind, "via": d, "line": n.lineno})
            else:
                fn = n.func.id if isinstance(n.func, ast.Name) else (n.func.attr if isinstance(n.func, ast.Attribute) else None)
                if fn in wrappers:
                    i, p, expr = wrappers[fn]
                    if len(n.args) > i:
                        nm, kind = name_of(n.args[i])
                        if kind == "literal" and expr != p:
                            # wrapper builds the env name from the param (e.g. prefix + name)
                            kind = f"wrapper-built:{expr}"
                        reads.append({"name": nm, "kind": kind, "via": f"wrapper:{fn}", "line": n.lineno})
                    else:
                        for kw in n.keywords:
                            if kw.arg == p:
                                nm, kind = name_of(kw.value)
                                reads.append({"name": nm, "kind": kind, "via": f"wrapper:{fn}", "line": n.lineno})
        elif isinstance(n, ast.Subscript) and dotted(n.value) in ("os.environ", "environ") and isinstance(n.ctx, ast.Load):
            nm, kind = name_of(n.slice)
            reads.append({"name": nm, "kind": kind, "via": "os.environ[]", "line": n.lineno})
        elif isinstance(n, ast.Compare) and any(isinstance(o, (ast.In, ast.NotIn)) for o in n.ops) \
                and any(dotted(c) in ("os.environ", "environ") for c in n.comparators):
            nm, kind = name_of(n.left)
            reads.append({"name": nm, "kind": kind, "via": "in os.environ", "line": n.lineno})
    return {"reads": reads, "wrappers": {k: list(v) for k, v in wrappers.items()}, "aliases": sorted(aliases)}, None


files = []
for r in roots:
    for dp, dn, fn in os.walk(r):
        dn[:] = [d for d in dn if d not in SKIP_DIRS]
        for f in fn:
            if f.endswith(".py"):
                p = os.path.join(dp, f)
                if excl_tests and is_test(os.path.relpath(p, r)):
                    continue
                files.append(p)
files.sort()
by_name = defaultdict(list)
errors = {}
for p in files:
    res, err = scan_file(p)
    if err:
        errors[p] = err; continue
    for rd in res["reads"]:
        if prefix and not (rd["name"].startswith(prefix) or (rd["kind"] != "literal" and prefix in rd["name"])):
            continue
        by_name[rd["name"]].append({"file": p, "line": rd["line"], "via": rd["via"], "kind": rd["kind"]})
out = {"roots": roots, "exclude_tests": excl_tests, "prefix": prefix, "n_files": len(files),
       "distinct_names": len(by_name), "call_sites": sum(len(v) for v in by_name.values()),
       "names": {k: v for k, v in sorted(by_name.items())}, "parse_errors": errors}
json.dump(out, open(out_path, "w"), indent=1)
print(f"files={len(files)} distinct={len(by_name)} sites={out['call_sites']} parse_errors={len(errors)}")
for k in sorted(by_name):
    vias = sorted({x['via'] for x in by_name[k]})
    fl = sorted({os.path.relpath(x['file'], roots[0]) if x['file'].startswith(roots[0]) else x['file'] for x in by_name[k]})
    print(f"  {k}  sites={len(by_name[k])} via={vias} files={fl}")
