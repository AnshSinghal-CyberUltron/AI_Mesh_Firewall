#!/usr/bin/env python3
"""Environment-variable reads via AST, robust to aliases and wrappers.
Usage: env_reads.py <root> <out.json> <subdir> [<subdir> ...]  (non-test .py files under each subdir)

A 'direct read' is any of (os may be aliased, e.g. `import os as _os`; environ/getenv may be imported bare):
  os.getenv(K,...) | os.environ.get(K,...) | os.environ[K] (Load) | K in os.environ | os.environ.setdefault(K,..)
  | os.environ.pop(K,..)
A 'wrapper' is a function whose body performs a direct read keyed by one of its own parameters
(e.g. config.get_env(name, default), scanner._env_int(name, default)). Calls to a wrapper name
(bare or attribute, anywhere in scope) with a literal first arg are 'wrapper reads'.
Keys: literal str -> the name; f-string/concat/other -> '<dynamic:...>'.
Writes (os.environ[K] = v, os.environ.update) are recorded separately and not counted as reads.
"""
import ast, os, sys, json
from collections import defaultdict
root, outp, subs = sys.argv[1], sys.argv[2], sys.argv[3:]
SKIPD = {"tests", "__pycache__", ".claude-flow", ".swarm", "graphify-out", ".venv", "node_modules", "migrations", ".claude"}
files = []
for s in subs:
    for dp, dn, fn in os.walk(os.path.join(root, s)):
        dn[:] = [d for d in dn if d not in SKIPD]
        for f in fn:
            if f.endswith(".py") and not f.startswith("test_") and f != "conftest.py" and not f.endswith("_test.py"):
                files.append(os.path.join(dp, f))
files = sorted(set(files))

def aliases(tree):
    os_names, environ_names, getenv_names = {"os"}, set(), set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            for a in n.names:
                if a.name == "os":
                    os_names.add(a.asname or "os")
        elif isinstance(n, ast.ImportFrom) and n.module == "os":
            for a in n.names:
                if a.name == "environ": environ_names.add(a.asname or "environ")
                if a.name == "getenv": getenv_names.add(a.asname or "getenv")
    return os_names, environ_names, getenv_names

def is_environ(e, osn, envn):
    return (isinstance(e, ast.Attribute) and e.attr == "environ" and isinstance(e.value, ast.Name) and e.value.id in osn) \
        or (isinstance(e, ast.Name) and e.id in envn)

def resolve(k, consts, loops, node):
    """Return list of env names read at this site; ['<dynamic:...>'] if not statically resolvable."""
    if isinstance(k, ast.Constant) and isinstance(k.value, str):
        return [k.value]
    if isinstance(k, ast.Name):
        lp = loops.get(id(node), {})
        if k.id in lp:
            return lp[k.id]
        if k.id in consts:
            return [consts[k.id]]
    return ["<dynamic:" + ast.unparse(k)[:60] + ">"]


def key_of(k):
    if isinstance(k, ast.Constant) and isinstance(k.value, str):
        return k.value
    return "<dynamic:" + ast.unparse(k)[:60] + ">"

def direct_reads(tree, osn, envn, gn):
    """yield (node, keynode, kind)"""
    parents = {}
    for n in ast.walk(tree):
        for c in ast.iter_child_nodes(n):
            parents[c] = n
    for n in ast.walk(tree):
        if isinstance(n, ast.Call):
            f = n.func
            if isinstance(f, ast.Attribute) and f.attr == "getenv" and isinstance(f.value, ast.Name) and f.value.id in osn and n.args:
                yield n, n.args[0], "getenv"
            elif isinstance(f, ast.Name) and f.id in gn and n.args:
                yield n, n.args[0], "getenv"
            elif isinstance(f, ast.Attribute) and f.attr in ("get", "setdefault", "pop") and is_environ(f.value, osn, envn) and n.args:
                yield n, n.args[0], "environ." + f.attr
        elif isinstance(n, ast.Subscript) and is_environ(n.value, osn, envn) and isinstance(n.ctx, ast.Load):
            yield n, n.slice, "environ[]"
        elif isinstance(n, ast.Compare) and len(n.ops) == 1 and isinstance(n.ops[0], (ast.In, ast.NotIn)) and is_environ(n.comparators[0], osn, envn):
            yield n, n.left, "in environ"

trees = {}
for p in files:
    try:
        trees[p] = ast.parse(open(p, encoding="utf-8").read())
    except Exception as e:
        print("PARSE FAIL", p, e, file=sys.stderr)
# pass 1: wrappers (transitive fixpoint: a function that forwards one of its params as the key
# to a direct read OR to an already-known wrapper is itself a wrapper)
wrappers = {}
def _param_keyed_wrapper_call(fn):
    params = [a.arg for a in fn.args.args + fn.args.kwonlyargs + fn.args.posonlyargs]
    for c in ast.walk(fn):
        if isinstance(c, ast.Call) and c.args and isinstance(c.args[0], ast.Name) and c.args[0].id in params:
            f = c.func
            nm = f.attr if isinstance(f, ast.Attribute) else f.id if isinstance(f, ast.Name) else None
            if nm in wrappers and nm != fn.name:
                return True
    return False
changed = True
while changed:
    changed = False
    for p, t in trees.items():
        osn, envn, gn = aliases(t)
        for fn in ast.walk(t):
            if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)) and fn.name not in wrappers:
                params = [a.arg for a in fn.args.args + fn.args.kwonlyargs + fn.args.posonlyargs]
                keyed = [k.id for _, k, _ in direct_reads(fn, osn, envn, gn) if isinstance(k, ast.Name) and k.id in params]
                if not keyed:
                    for c in ast.walk(fn):
                        if isinstance(c, ast.Call) and c.args and isinstance(c.args[0], ast.Name) and c.args[0].id in params:
                            f = c.func
                            nm = f.attr if isinstance(f, ast.Attribute) else f.id if isinstance(f, ast.Name) else None
                            if nm in wrappers and nm != fn.name:
                                keyed = [c.args[0].id]; break
                if keyed:
                    pname = keyed[0]
                    pos = [a.arg for a in fn.args.posonlyargs + fn.args.args]
                    idx = pos.index(pname) if pname in pos else None
                    defaults = dict(zip([a.arg for a in fn.args.args][-len(fn.args.defaults):] if fn.args.defaults else [], fn.args.defaults))
                    for a, d in zip(fn.args.kwonlyargs, fn.args.kw_defaults):
                        if d is not None: defaults[a.arg] = d
                    dflt = defaults.get(pname)
                    wrappers[fn.name] = {"def": f"{os.path.relpath(p, root)}:{fn.lineno}", "param": pname, "index": idx,
                                         "default": dflt.value if isinstance(dflt, ast.Constant) else None}
                    changed = True
# pass 2: reads
sites = []
writes = []
for p, t in trees.items():
    rel = os.path.relpath(p, root)
    osn, envn, gn = aliases(t)
    consts = {}
    for st in t.body:
        if isinstance(st, ast.Assign) and len(st.targets) == 1 and isinstance(st.targets[0], ast.Name) \
                and isinstance(st.value, ast.Constant) and isinstance(st.value.value, str):
            consts[st.targets[0].id] = st.value.value
    loops = {}
    for fl in ast.walk(t):
        if isinstance(fl, (ast.For, ast.comprehension)) and isinstance(fl.target, ast.Name) and isinstance(fl.iter, (ast.Tuple, ast.List)) \
                and all(isinstance(e, ast.Constant) and isinstance(e.value, str) for e in fl.iter.elts):
            body = fl.body if isinstance(fl, ast.For) else []
            for b in (body or [fl]):
                for c in ast.walk(b if body else fl):
                    loops.setdefault(id(c), {})[fl.target.id] = [e.value for e in fl.iter.elts]
    # map node -> enclosing wrapper def (to drop parameter-keyed reads inside wrappers)
    inside = {}
    for fn in ast.walk(t):
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)) and fn.name in wrappers:
            for c in ast.walk(fn):
                inside[id(c)] = fn
    for node, k, kind in direct_reads(t, osn, envn, gn):
        fn = inside.get(id(node))
        if fn is not None and isinstance(k, ast.Name) and k.id in [a.arg for a in fn.args.args + fn.args.kwonlyargs]:
            continue  # the wrapper's own parameter-keyed read; counted at wrapper call sites
        sites.append({"file": rel, "line": node.lineno, "keys": resolve(k, consts, loops, node), "kind": kind})
    for n in ast.walk(t):
        if isinstance(n, ast.Call):
            f = n.func
            nm = f.attr if isinstance(f, ast.Attribute) else f.id if isinstance(f, ast.Name) else None
            if nm in wrappers and not (isinstance(f, ast.Attribute) and nm in ("get", "pop", "setdefault")):
                w = wrappers[nm]
                karg = None
                for kw in n.keywords:
                    if kw.arg == w["param"]: karg = kw.value
                if karg is None and w["index"] is not None and len(n.args) > w["index"]:
                    karg = n.args[w["index"]]
                fn = inside.get(id(n))
                if fn is not None and isinstance(karg, ast.Name) and karg.id in [a.arg for a in fn.args.args + fn.args.kwonlyargs]:
                    continue  # wrapper forwarding its own parameter
                if karg is None:
                    if w["default"] is None:
                        continue
                    karg = ast.Constant(w["default"])
                sites.append({"file": rel, "line": n.lineno, "keys": resolve(karg, consts, loops, n), "kind": "wrapper:" + nm})
        if isinstance(n, ast.Assign):
            for tg in n.targets:
                if isinstance(tg, ast.Subscript) and is_environ(tg.value, osn, envn):
                    writes.append({"file": rel, "line": n.lineno, "key": key_of(tg.slice)})
literal = [s for s in sites if not any(x.startswith("<dynamic") for x in s["keys"])]
dyn = [s for s in sites if any(x.startswith("<dynamic") for x in s["keys"])]
names = sorted({x for s in literal for x in s["keys"]})
by_name = defaultdict(list)
for s in literal:
    for x in s["keys"]:
        by_name[x].append(f'{s["file"]}:{s["line"]}')
res = {"scope": subs, "n_files": len(files), "wrappers_detected": wrappers,
       "distinct_literal_names": len(names), "literal_call_sites": len(literal),
       "dynamic_key_sites": len(dyn), "total_sites_incl_dynamic": len(sites),
       "distinct_names_direct_only": len({x for s in literal if not s['kind'].startswith('wrapper') for x in s['keys']}),
       "call_sites_direct_only": sum(1 for s in literal if not s['kind'].startswith('wrapper')),
       "by_kind": {k: sum(1 for s in sites if s["kind"] == k) for k in sorted({s["kind"] for s in sites})},
       "names": {n: by_name[n] for n in names}, "dynamic_sites": dyn, "writes": writes}
json.dump(res, open(outp, "w"), indent=1)
print(json.dumps({k: v for k, v in res.items() if k not in ("names", "dynamic_sites", "writes")}, indent=1))
