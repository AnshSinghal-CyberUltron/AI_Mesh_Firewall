#!/usr/bin/env python3
"""AST-count pytest tests in given files: test functions/methods, parametrize decorators, xfail markers.
Usage: ast_count_tests.py <file>..."""
import ast, sys, json
tot_funcs = 0; tot_param = 0
for p in sys.argv[1:]:
    t = ast.parse(open(p, encoding="utf-8").read())
    funcs = []
    for n in t.body:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name.startswith("test"):
            funcs.append(n)
        if isinstance(n, ast.ClassDef) and n.name.startswith("Test"):
            for m in n.body:
                if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef)) and m.name.startswith("test"):
                    funcs.append(m)
    param = []
    xf = []
    for f in funcs:
        for d in f.decorator_list:
            s = ast.unparse(d)
            if "parametrize" in s:
                param.append((f.name, f.lineno, s[:160]))
            if "xfail" in s:
                xf.append((f.name, d.lineno, s[:300]))
    # module-level pytestmark
    marks = [ast.unparse(n)[:200] for n in t.body if isinstance(n, ast.Assign) and any(getattr(tt, "id", "") == "pytestmark" for tt in n.targets)]
    print(json.dumps({"file": p.split("/")[-1], "test_funcs": len(funcs), "parametrize_decorators": param,
                      "xfail_decorators": xf, "pytestmark": marks}, indent=1))
    tot_funcs += len(funcs)
print("TOTAL test functions/methods (AST):", tot_funcs)
