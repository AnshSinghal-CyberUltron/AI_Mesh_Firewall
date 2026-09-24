#!/usr/bin/env python3
"""List Tier-2 enablement resolver definitions, resolver call sites, and Tier-2 invocation sites.
Usage: tier2_sites.py <pkg_dir>"""
import ast, os, sys, json
pkg = sys.argv[1]
files = []
for dp, dn, fn in os.walk(pkg):
    dn[:] = [d for d in dn if d not in ("tests", "__pycache__", ".claude-flow", ".swarm", "graphify-out")]
    for f in fn:
        if f.endswith(".py") and f != "conftest.py" and not f.startswith("test_"):
            files.append(os.path.join(dp, f))
defs, resolver_calls, invocations, gates = [], [], [], []
for p in sorted(files):
    rel = os.path.relpath(p, pkg)
    src = open(p, encoding="utf-8").read(); t = ast.parse(src); L = src.splitlines()
    for n in ast.walk(t):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and "tier2" in n.name.lower() and \
                any(k in n.name.lower() for k in ("resolve", "allowed", "enabled")):
            defs.append([rel, n.lineno, n.name, ast.unparse(n.body[-1])[:80]])
        if isinstance(n, ast.Call):
            f = n.func
            nm = f.attr if isinstance(f, ast.Attribute) else f.id if isinstance(f, ast.Name) else ""
            if nm in ("resolve_tier2_enabled", "resolve_rag_tier2_enabled", "resolve_mcp_tier2_enabled",
                      "_resolve_mcp_tier2_enabled", "_org_tier2_allowed"):
                resolver_calls.append([rel, n.lineno, nm, ast.unparse(n)[:100]])
            if nm in ("scan_prompt_with_tier2", "scan_output_with_tier2"):
                invocations.append([rel, n.lineno, nm, "org_tier2_override=" + next((ast.unparse(k.value) for k in n.keywords if k.arg == "org_tier2_override"), "<none>")])
            if nm == "getattr" and len(n.args) >= 2 and isinstance(n.args[1], ast.Constant) and n.args[1].value in ("scan_prompt_with_tier2", "scan_output_with_tier2"):
                invocations.append([rel, n.lineno, "getattr->" + n.args[1].value, "dynamic lookup (called later)"])
            # other gates: _enabled("output_tier2_enabled", ...), .get("enabled") on tier2_ctrl, os.getenv("ENABLE_TIER2")
            if nm == "_enabled" and n.args and isinstance(n.args[0], ast.Constant) and "tier2" in str(n.args[0].value):
                gates.append([rel, n.lineno, ast.unparse(n)])
            if nm == "get" and isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) and f.value.id == "tier2_ctrl" \
                    and n.args and isinstance(n.args[0], ast.Constant) and n.args[0].value == "enabled":
                gates.append([rel, n.lineno, ast.unparse(n)])
            if nm in ("getenv", "get") and n.args and isinstance(n.args[0], ast.Constant) and n.args[0].value == "ENABLE_TIER2":
                gates.append([rel, n.lineno, ast.unparse(n)])
        if isinstance(n, ast.Compare) and "tier2_enabled" in ast.unparse(n) and "org_tier2_override" in ast.unparse(n):
            pass
    for i, l in enumerate(L, 1):
        if "org_tier2_override is None and not self.tier2_enabled" in l:
            gates.append([rel, i, l.strip()])
print(json.dumps({"resolver_defs": defs, "resolver_calls": resolver_calls,
                  "tier2_invocation_sites": invocations, "other_gates": gates,
                  "counts": {"resolver_defs": len(defs), "resolver_calls": len(resolver_calls),
                             "invocation_sites_direct": sum(1 for x in invocations if not x[2].startswith("getattr")),
                             "invocation_sites_incl_getattr": len(invocations), "other_gates": len(gates)}}, indent=1))
