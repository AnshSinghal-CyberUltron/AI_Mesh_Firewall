"""Probe A: runbook §10.1.1 P1, P2, P3, P6, P7 against the shipped enforcement.py.

Usage: probe_enforcement.py <ROOT>   (ROOT = baseline export or repo HEAD)
Imports the real module exactly as production does (top-level ``enforcement``
with PYTHONPATH=<ROOT>/shared:<ROOT>/gateway:<ROOT>/gateway/ai_mesh_gateway).
"""
import ast
import inspect
import sys

ROOT = sys.argv[1]
import enforcement as E  # noqa: E402

print("ROOT =", ROOT)
print("enforcement imported from:", E.__file__)
assert E.__file__.startswith(ROOT), "wrong module picked up"

print("\n== signatures ==")
for fn in (E.resolve_enforcement, E.max_action, E.enforce_output, E.resolve_and_enforce):
    print(f"{fn.__name__}{inspect.signature(fn)}")

print("\n== P1: resolve_enforcement(rec, org_policy_action='allow') ==")
for rec in ("block", "flag", "monitor"):
    out = E.resolve_enforcement(rec, org_policy_action="allow")
    print(f"resolve_enforcement({rec!r}, org_policy_action='allow') -> {out!r}")
# same probe through the canonical input entry point main.py actually calls
d = E.resolve_and_enforce(scanner_recommendation="block", scanner_action="block",
                          scanner_threat_type="dos", scanner_confidence=1.0,
                          org_policy_action="allow", enforcement_mode="block")
print("resolve_and_enforce(scanner=block/dos, org_policy_action='allow') ->", repr(d.action))

print("\n== P2: max_action + docstring ==")
print("max_action('allow','block','allow') ->", repr(E.max_action("allow", "block", "allow")))
src_lines = open(E.__file__).read().splitlines()
for n in range(7, 12):
    print(f"enforcement.py:{n}: {src_lines[n-1]}")
for n in range(114, 120):
    print(f"enforcement.py:{n}: {src_lines[n-1]}")

print("\n== P3: enforce_output(verdict_action='block', scan_degraded=...) ==")
for deg in (True, False):
    d = E.enforce_output(verdict_action="block", scan_degraded=deg)
    print(f"enforce_output(verdict_action='block', scan_degraded={deg}) -> "
          f"type={type(d).__name__} action={d.action!r} degraded={d.degraded} blocked_by={d.blocked_by!r}")

print("\n== P6: full matrix verdict_action x scan_degraded ==")
print(f"{'verdict_action':<15}{'scan_degraded':<15}resolved")
for va in ("block", "flag", "allow"):
    for deg in (False, True):
        print(f"{va:<15}{str(deg):<15}{E.enforce_output(verdict_action=va, scan_degraded=deg).action}")
# extra cells the runbook did not list
for va in ("redact", "rewrite", None):
    for deg in (False, True):
        print(f"{str(va):<15}{str(deg):<15}{E.enforce_output(verdict_action=va, scan_degraded=deg).action}  (extra)")

print("\n== P6 line claim: degraded return precedes the first read of verdict_action ==")
tree = ast.parse(open(E.__file__).read())
fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "enforce_output")
try_node = next(n for n in fn.body if isinstance(n, ast.Try))
for stmt in try_node.body:
    if isinstance(stmt, ast.If) and isinstance(stmt.test, ast.Name) and stmt.test.id == "scan_degraded":
        ret = [s for s in stmt.body if isinstance(s, ast.Return)][0]
        print(f"`if scan_degraded:` block spans lines {stmt.lineno}-{stmt.end_lineno}; its return at {ret.lineno}-{ret.end_lineno}")
reads = sorted(n.lineno for n in ast.walk(fn) if isinstance(n, ast.Name) and n.id == "verdict_action" and isinstance(n.ctx, ast.Load))
print("lines that READ verdict_action inside enforce_output:", reads)
print(f"enforcement.py:{reads[0]}: {src_lines[reads[0]-1].strip()}")

print("\n== P7: enforce_output(verdict_action='block', enforcement_mode=...) ==")
for mode in ("monitor", "tag", "observe", "block"):
    d = E.enforce_output(verdict_action="block", enforcement_mode=mode)
    print(f"enforce_output(verdict_action='block', enforcement_mode={mode!r}) -> {d.action!r}")
params = [a.arg for a in fn.args.kwonlyargs]
loaded = {n.id for n in ast.walk(fn) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}
print("enforce_output kw-only params never read in its body:", [p for p in params if p not in loaded])
# contrast: input path honours monitor
print("input-path contrast resolve_enforcement('block', enforcement_mode='monitor') ->",
      repr(E.resolve_enforcement("block", enforcement_mode="monitor")))
