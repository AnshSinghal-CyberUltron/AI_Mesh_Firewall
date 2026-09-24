#!/usr/bin/env python3
"""Recompute runbook §10.1.4 main.py / proxy_chat structural metrics via AST.

Usage: mainpy_metrics.py <repo_root> [function_name=proxy_chat] [file=gateway/ai_mesh_gateway/main.py]
Prints a JSON document. Every count is given under explicitly named definitions.
"""
import ast, json, os, sys

root = sys.argv[1]
fname = sys.argv[2] if len(sys.argv) > 2 else "proxy_chat"
rel = sys.argv[3] if len(sys.argv) > 3 else "gateway/ai_mesh_gateway/main.py"
path = os.path.join(root, rel)
src = open(path, "rb").read()
text = src.decode("utf-8")
tree = ast.parse(text)
lines = text.splitlines()
out = {"file": rel, "lines_wc": text.count("\n"), "bytes": len(src),
       "KiB": round(len(src) / 1024, 1), "KB_decimal": round(len(src) / 1000, 1),
       "chars": len(text)}

# ---------------- top-level symbols ----------------
defs = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))]
assigned = set()
for n in tree.body:
    tgts = []
    if isinstance(n, ast.Assign):
        tgts = n.targets
    elif isinstance(n, (ast.AnnAssign, ast.AugAssign)):
        tgts = [n.target]
    for t in tgts:
        for sub in ast.walk(t):
            if isinstance(sub, ast.Name):
                assigned.add(sub.id)
imported = set()
for n in tree.body:
    if isinstance(n, ast.Import):
        for a in n.names:
            imported.add((a.asname or a.name).split(".")[0])
    elif isinstance(n, ast.ImportFrom):
        for a in n.names:
            imported.add(a.asname or a.name)
def_names = [n.name for n in defs]
out["toplevel"] = {
    "def_asyncdef_class_nodes": len(defs),
    "functions": sum(isinstance(n, ast.FunctionDef) for n in defs),
    "async_functions": sum(isinstance(n, ast.AsyncFunctionDef) for n in defs),
    "classes": sum(isinstance(n, ast.ClassDef) for n in defs),
    "unique_def_class_names": len(set(def_names)),
    "duplicate_def_names": sorted({x for x in def_names if def_names.count(x) > 1}),
    "unique_assigned_names": len(assigned),
    "defs_plus_assigned_unique": len(set(def_names) | assigned),
    "defs_plus_assigned_plus_imports_unique": len(set(def_names) | assigned | imported),
    "toplevel_statements_total": len(tree.body),
}

# ---------------- routes ----------------
VERBS = {"get", "post", "put", "delete", "patch", "api_route", "websocket", "options", "head", "route"}
routes = []
handlers_with_route = set()
for n in ast.walk(tree):
    if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
        for d in n.decorator_list:
            if isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute) and d.func.attr in VERBS \
                    and isinstance(d.func.value, ast.Name):
                p = d.args[0].value if d.args and isinstance(d.args[0], ast.Constant) else None
                if p is None:
                    for kw in d.keywords:
                        if kw.arg == "path" and isinstance(kw.value, ast.Constant):
                            p = kw.value.value
                routes.append({"line": d.lineno, "obj": d.func.value.id, "verb": d.func.attr,
                               "path": p, "handler": n.name})
                handlers_with_route.add(n.name)
add_api = []
for n in ast.walk(tree):
    if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr in (
            "add_api_route", "add_route", "add_websocket_route", "mount", "include_router"):
        add_api.append({"line": n.lineno, "call": n.func.attr, "arg0": ast.unparse(n.args[0]) if n.args else None})
# expand the add_api_route loop (for _p, _methods in (...): app.add_api_route(_p, ...))
loop_paths = []
for n in ast.walk(tree):
    if isinstance(n, ast.For) and isinstance(n.iter, ast.Tuple):
        if any(isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute) and c.func.attr == "add_api_route"
               for c in ast.walk(n)):
            for elt in n.iter.elts:
                try:
                    loop_paths.append(ast.literal_eval(elt))
                except Exception:
                    pass
out["routes"] = {
    "route_decorators_in_file": len(routes),
    "route_decorators_on_app": sum(r["obj"] == "app" for r in routes),
    "unique_route_handlers": len(handlers_with_route),
    "unique_paths_decorated": len({r["path"] for r in routes}),
    "unique_verb_path_decorated": len({(r["verb"], r["path"]) for r in routes}),
    "add_api_route_loop_paths": loop_paths,
    "registration_calls": add_api,
    "list": routes,
}

# ---------------- function metrics ----------------
target = [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == fname]
assert len(target) == 1, f"{fname}: {len(target)} defs"
fn = target[0]
first_line = min([fn.lineno] + [d.lineno for d in fn.decorator_list])
m = {"def_line": fn.lineno, "first_decorator_line": first_line, "end_line": fn.end_lineno,
     "lines_def_to_end": fn.end_lineno - fn.lineno + 1,
     "lines_incl_decorators": fn.end_lineno - first_line + 1}
body_lines = [l for l in lines[fn.lineno - 1: fn.end_lineno]]
m["nonblank_lines"] = sum(1 for l in body_lines if l.strip())
m["code_lines_excl_blank_comment"] = sum(1 for l in body_lines if l.strip() and not l.strip().startswith("#"))


def walk_excl_nested(node):
    """ast.walk over node's body but do not descend into nested def/lambda/class."""
    stack = list(ast.iter_child_nodes(node))
    while stack:
        c = stack.pop()
        yield c
        if isinstance(c, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            continue
        stack.extend(ast.iter_child_nodes(c))


nested_defs = [n for n in ast.walk(fn) if n is not fn and isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))]
m["nested_defs"] = [{"name": n.name, "line": n.lineno, "end": n.end_lineno} for n in nested_defs]

all_nodes = list(ast.walk(fn))
own_nodes = list(walk_excl_nested(fn))


def cnt(nodes, cls):
    return sum(isinstance(n, cls) for n in nodes)


m["return_stmts_incl_nested_defs"] = cnt(all_nodes, ast.Return)
m["return_stmts_excl_nested_defs"] = cnt(own_nodes, ast.Return)
m["raise_stmts_incl_nested"] = cnt(all_nodes, ast.Raise)
m["yield_incl_nested"] = cnt(all_nodes, (ast.Yield, ast.YieldFrom))

# if vs elif
elif_ids = set()
for n in all_nodes:
    if isinstance(n, ast.If) and len(n.orelse) == 1 and isinstance(n.orelse[0], ast.If):
        o = n.orelse[0]
        # elif iff the source at o.lineno starts with 'elif'
        if lines[o.lineno - 1].lstrip().startswith("elif"):
            elif_ids.add(id(o))
ifs_all = [n for n in all_nodes if isinstance(n, ast.If)]
ifs_own = [n for n in own_nodes if isinstance(n, ast.If)]
m["if_nodes_incl_nested(if+elif)"] = len(ifs_all)
m["if_only_incl_nested"] = sum(id(n) not in elif_ids for n in ifs_all)
m["elif_only_incl_nested"] = sum(id(n) in elif_ids for n in ifs_all)
m["if_nodes_excl_nested(if+elif)"] = len(ifs_own)
m["ifexp_ternaries_incl_nested"] = cnt(all_nodes, ast.IfExp)
m["comprehension_ifs_incl_nested"] = sum(len(c.ifs) for c in all_nodes if isinstance(c, ast.comprehension))
m["source_lines_starting_if_or_elif"] = sum(1 for l in body_lines if l.lstrip().startswith(("if ", "elif ", "if(", "elif(")))

# exception handlers
def is_broad(h):
    t = h.type
    if t is None:
        return "bare"
    names = []
    if isinstance(t, ast.Tuple):
        names = [ast.unparse(e) for e in t.elts]
    else:
        names = [ast.unparse(t)]
    if "BaseException" in names:
        return "BaseException"
    if "Exception" in names:
        return "Exception" if len(names) == 1 else "tuple_with_Exception"
    return None

hs_all = [n for n in all_nodes if isinstance(n, ast.ExceptHandler)]
hs_own = [n for n in own_nodes if isinstance(n, ast.ExceptHandler)]
m["except_handlers_incl_nested"] = len(hs_all)
m["except_handlers_excl_nested"] = len(hs_own)
from collections import Counter
m["broad_kinds_incl_nested"] = dict(Counter(is_broad(h) for h in hs_all if is_broad(h)))
m["broad_kinds_excl_nested"] = dict(Counter(is_broad(h) for h in hs_own if is_broad(h)))
m["handler_types_incl_nested"] = dict(Counter(ast.unparse(h.type) if h.type else "<bare>" for h in hs_all))
m["try_stmts_incl_nested"] = cnt(all_nodes, (ast.Try,) + ((ast.TryStar,) if hasattr(ast, "TryStar") else ()))

# nesting depth
BLOCK = (ast.If, ast.For, ast.AsyncFor, ast.While, ast.Try, ast.With, ast.AsyncWith,
         ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef) + ((ast.TryStar,) if hasattr(ast, "TryStar") else ()) \
        + ((ast.Match,) if hasattr(ast, "Match") else ())


def depth_ast(node, d, elif_flat, deepest):
    """d = depth of node's children statements. elif_flat: elif does not add a level."""
    for field in ("body", "orelse", "finalbody", "handlers", "cases"):
        for c in getattr(node, field, []) or []:
            if isinstance(c, ast.ExceptHandler) or (hasattr(ast, "match_case") and isinstance(c, ast.match_case)):
                # handler body is at same depth as try body
                depth_ast(c, d, elif_flat, deepest)
                continue
            if isinstance(c, ast.stmt):
                deepest[0] = max(deepest[0], d)
                if deepest[0] == d and d > deepest[2]:
                    deepest[2] = d; deepest[1] = c.lineno
            if isinstance(c, BLOCK):
                if elif_flat and field == "orelse" and isinstance(node, ast.If) and id(c) in elif_ids:
                    # elif: its body is at the same depth as the parent if's body
                    depth_ast(c, d - 0, elif_flat, deepest) if False else None
                    # children of elif are at depth d (same as sibling body of parent if)
                    for f2 in ("body", "orelse"):
                        pass
                    depth_ast_elif(c, d, elif_flat, deepest)
                else:
                    depth_ast(c, d + 1, elif_flat, deepest)


def depth_ast_elif(c, d, elif_flat, deepest):
    # c is an If reached via elif from a parent whose body is at depth d.
    # c's body statements are at depth d (same as parent's body), c's orelse likewise.
    for field in ("body", "orelse"):
        for s in getattr(c, field):
            deepest[0] = max(deepest[0], d)
            if isinstance(s, BLOCK):
                if field == "orelse" and isinstance(s, ast.If) and id(s) in elif_ids:
                    depth_ast_elif(s, d, elif_flat, deepest)
                else:
                    depth_ast(s, d + 1, elif_flat, deepest)


res = {}
for flat in (True, False):
    deepest = [0, None, 0]
    depth_ast(fn, 1, flat, deepest)
    res["elif_flat" if flat else "elif_nested"] = {"max_block_depth": deepest[0], "at_line": deepest[1]}
# indentation-based: col_offset of statements relative to fn body indentation
base = fn.body[0].col_offset
maxind = 0; at = None
for n in all_nodes:
    if isinstance(n, ast.stmt) and n is not fn:
        lvl = (n.col_offset - base) // 4 + 1
        if lvl > maxind:
            maxind, at = lvl, n.lineno
res["indent_levels_of_statements(body=1)"] = {"max": maxind, "at_line": at, "note": "function body = level 1"}
res["indent_levels_minus_body(body=0)"] = {"max": maxind - 1, "at_line": at}
m["nesting"] = res

# body mutations
MUT = {"pop", "update", "setdefault", "clear", "popitem", "append", "extend", "insert", "remove", "sort", "reverse", "__setitem__", "__delitem__"}


def root_name(e):
    while isinstance(e, (ast.Subscript, ast.Attribute)):
        e = e.value
    return e.id if isinstance(e, ast.Name) else None


def is_direct(e, name):
    return isinstance(e, ast.Subscript) and isinstance(e.value, ast.Name) and e.value.id == name


def mutations(name, nodes):
    direct, nested, rebinds = [], [], []
    for n in nodes:
        if isinstance(n, (ast.Assign, ast.AugAssign, ast.AnnAssign, ast.Delete)):
            tgts = n.targets if isinstance(n, (ast.Assign, ast.Delete)) else [n.target]
            for t in tgts:
                for tt in (t.elts if isinstance(t, (ast.Tuple, ast.List)) else [t]):
                    if isinstance(tt, ast.Name) and tt.id == name and not isinstance(n, ast.Delete):
                        if isinstance(n, ast.AugAssign):
                            direct.append((n.lineno, type(n).__name__, ast.unparse(n)[:120]))
                        else:
                            rebinds.append((n.lineno, ast.unparse(n)[:120]))
                    elif isinstance(tt, (ast.Subscript, ast.Attribute)) and root_name(tt) == name:
                        rec = (n.lineno, type(n).__name__, ast.unparse(n)[:120])
                        (direct if is_direct(tt, name) else nested).append(rec)
        elif isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr in MUT:
            obj = n.func.value
            if root_name(obj) == name:
                rec = (n.lineno, "call." + n.func.attr, ast.unparse(n)[:120])
                (direct if isinstance(obj, ast.Name) else nested).append(rec)
    return direct, nested, rebinds


for var in ("body", "stage_metrics"):
    for scope, nodes in (("incl_nested", all_nodes), ("excl_nested", own_nodes)):
        d, nst, rb = mutations(var, nodes)
        m[f"{var}_mutations_direct_{scope}"] = len(d)
        m[f"{var}_mutations_nested_path_{scope}"] = len(nst)
        m[f"{var}_rebinds_{scope}"] = len(rb)
        if scope == "incl_nested":
            m[f"{var}_direct_list"] = sorted(d)
            m[f"{var}_nested_list"] = sorted(nst)
            m[f"{var}_rebind_list"] = sorted(rb)
    m[f"{var}_source_lines_containing_name"] = sum(1 for l in body_lines if var in l)

# stage_metrics across the whole file
d, nst, rb = mutations("stage_metrics", list(ast.walk(tree)))
m["stage_metrics_writes_whole_file_direct"] = len(d)
m["stage_metrics_writes_whole_file_nested"] = len(nst)
out["function"] = {fname: m}
print(json.dumps(out, indent=1, default=str))
