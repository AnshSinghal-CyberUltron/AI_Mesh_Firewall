#!/usr/bin/env python3
"""List the N largest functions in a module (AST) under three span definitions, and route handlers.
Usage: fn_sizes.py <file> [N=10]"""
import ast, sys, json
path = sys.argv[1]; N = int(sys.argv[2]) if len(sys.argv) > 2 else 10
text = open(path, encoding="utf-8").read(); tree = ast.parse(text)
top_starts = sorted(n.lineno for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)))
VERBS = {"get", "post", "put", "delete", "patch", "api_route", "websocket", "options", "head", "route"}
rows = []
for n in ast.walk(tree):
    if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
        first = min([n.lineno] + [d.lineno for d in n.decorator_list])
        nxt = [l for l in top_starts if l > n.lineno]
        is_top = n in tree.body
        routes = []
        for d in n.decorator_list:
            if isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute) and d.func.attr in VERBS:
                p = d.args[0].value if d.args and isinstance(d.args[0], ast.Constant) else None
                routes.append(f"{ast.unparse(d.func.value)}.{d.func.attr}({p!r})")
        rows.append({"name": n.name, "def_line": n.lineno, "end_line": n.end_lineno, "toplevel": is_top,
                     "lines_def_to_end": n.end_lineno - n.lineno + 1,
                     "lines_incl_decorators": n.end_lineno - first + 1,
                     "next_toplevel_def_minus_def": (nxt[0] - n.lineno) if (is_top and nxt) else None,
                     "routes": routes})
rows.sort(key=lambda r: -r["lines_def_to_end"])
handlers = [r for r in rows if r["routes"]]
print(json.dumps({"file": path, "top_by_def_to_end": rows[:N],
                  "n_route_handlers": len(handlers), "n_route_decorators": sum(len(r["routes"]) for r in handlers),
                  "route_handlers": sorted(handlers, key=lambda r: r["def_line"])}, indent=1))
