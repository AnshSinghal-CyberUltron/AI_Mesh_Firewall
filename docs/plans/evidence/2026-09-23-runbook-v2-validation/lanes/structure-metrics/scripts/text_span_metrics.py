#!/usr/bin/env python3
"""Grep-style (text) metrics for a function, to test whether runbook numbers came from a
line-range method rather than an AST. Two ranges:
  AST   = def line .. ast end_lineno
  GREP  = def line .. (next top-level 'def'/'async def'/'class' line - 1)
Usage: text_span_metrics.py <file> <funcname>
"""
import ast, re, sys, json
path, name = sys.argv[1], sys.argv[2]
text = open(path, encoding="utf-8").read()
lines = text.splitlines()
tree = ast.parse(text)
fn = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name][0]
top = sorted(n.lineno for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)))
nxt = [l for l in top if l > fn.lineno][0]
ranges = {"AST": (fn.lineno, fn.end_lineno), "GREP_to_next_toplevel_def": (fn.lineno, nxt - 1)}
res = {"def_line": fn.lineno, "ast_end": fn.end_lineno, "next_toplevel_def_line": nxt,
       "span_next_def_minus_def": nxt - fn.lineno}
for rn, (a, b) in ranges.items():
    seg = lines[a - 1:b]
    r = {"n_lines": len(seg)}
    r["lines_starting_if_elif"] = sum(1 for l in seg if re.match(r"\s*(if|elif)\b", l))
    r["lines_starting_if"] = sum(1 for l in seg if re.match(r"\s*if\b", l))
    r["lines_starting_elif"] = sum(1 for l in seg if re.match(r"\s*elif\b", l))
    r["lines_starting_return"] = sum(1 for l in seg if re.match(r"\s*return\b", l))
    r["lines_starting_except"] = sum(1 for l in seg if re.match(r"\s*except\b", l))
    r["lines_except_Exception_any"] = sum(1 for l in seg if re.match(r"\s*except\s+Exception\b", l))
    r["lines_except_Exception_colon"] = sum(1 for l in seg if re.match(r"\s*except\s+Exception\s*:", l))
    r["lines_except_Exception_as"] = sum(1 for l in seg if re.match(r"\s*except\s+Exception\s+as\b", l))
    r["lines_containing_'except Exception'"] = sum(1 for l in seg if "except Exception" in l)
    r["lines_body_subscript_assign"] = sum(1 for l in seg if re.match(r"\s*body\[[^\]]+\]\s*=[^=]", l))
    r["lines_body_pop_setdefault_update"] = sum(1 for l in seg if re.search(r"\bbody\.(pop|setdefault|update|clear)\(", l))
    r["lines_stage_metrics_subscript_assign"] = sum(1 for l in seg if re.match(r"\s*stage_metrics\[[^\]]+\]\s*=[^=]", l))
    # indentation depth (spaces/4) of any non-blank, non-comment line, relative to the def line
    base = len(lines[a - 1]) - len(lines[a - 1].lstrip())
    ind = [((len(l) - len(l.lstrip())) - base) // 4 for l in seg if l.strip() and not l.strip().startswith("#")]
    r["max_indent_levels_any_line(def=0)"] = max(ind)
    r["max_indent_line_no"] = a + [i for i, l in enumerate(seg) if l.strip() and not l.strip().startswith("#") and ((len(l) - len(l.lstrip())) - base) // 4 == max(ind)][0]
    kw = re.compile(r"\s*(if|elif|else|for|async for|while|try|except|finally|with|async with|def|async def|match|case)\b.*:\s*(#.*)?$")
    ind_kw = [((len(l) - len(l.lstrip())) - base) // 4 for l in seg if kw.match(l)]
    r["max_indent_of_block_opening_lines(def=0)"] = max(ind_kw)
    res[rn] = r
print(json.dumps(res, indent=1))
