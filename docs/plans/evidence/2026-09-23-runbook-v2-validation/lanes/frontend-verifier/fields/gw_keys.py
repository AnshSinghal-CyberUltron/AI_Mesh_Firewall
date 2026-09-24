"""Universe of string keys the v1 gateway (and the control-plane translation layer) can emit.

G_gateway  : every str constant used as a dict-literal key, a subscript-store key (x["k"] = v),
             or a keyword name in dict(k=...) in gateway/ai_mesh_gateway/**/*.py (non-test).
G_trace    : same, restricted to pipeline_trace.py + trace_projection.py (the pipeline_trace payload).
G_telemetry: dict-literal keys returned by telemetry.build_telemetry_event.
C_ee       : keys written by control core/tasks.py::_build_enforcement_metadata (EE.metadata).
C_feed     : dict-literal keys built in control policy/security_views.py (threat-feed/KPI responses).
"""
import ast, json, pathlib, sys
REPO = pathlib.Path("/home/contact_cyberultron_com/AI_Mesh_Firewall")
GW = REPO / "gateway/ai_mesh_gateway"

def keys_in(tree, fn_name=None):
    out = set()
    nodes = ast.walk(tree)
    if fn_name:
        fns = [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == fn_name]
        nodes = (m for f in fns for m in ast.walk(f))
    for n in nodes:
        if isinstance(n, ast.Dict):
            for k in n.keys:
                if isinstance(k, ast.Constant) and isinstance(k.value, str):
                    out.add(k.value)
        elif isinstance(n, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
            tgts = n.targets if isinstance(n, ast.Assign) else [n.target]
            for t in tgts:
                if isinstance(t, ast.Subscript) and isinstance(t.slice, ast.Constant) and isinstance(t.slice.value, str):
                    out.add(t.slice.value)
        elif isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "dict":
            for kw in n.keywords:
                if kw.arg:
                    out.add(kw.arg)
    return out

def parse(p):
    return ast.parse(p.read_text(encoding="utf-8"), filename=str(p))

gw_files = [p for p in GW.rglob("*.py") if "/tests/" not in str(p) and not p.name.startswith("test_") and p.name != "conftest.py"]
G_gateway = set()
for p in gw_files:
    try:
        G_gateway |= keys_in(parse(p))
    except SyntaxError as e:
        print("SKIP", p, e, file=sys.stderr)
G_trace = keys_in(parse(GW / "pipeline_trace.py")) | keys_in(parse(GW / "trace_projection.py"))
G_telemetry = keys_in(parse(GW / "telemetry.py"), "build_telemetry_event")
C_ee = keys_in(parse(REPO / "control/ai_mesh_control/core/tasks.py"), "_build_enforcement_metadata")
C_feed = keys_in(parse(REPO / "control/ai_mesh_control/policy/security_views.py"))
out = {k: sorted(v) for k, v in dict(G_gateway=G_gateway, G_trace=G_trace, G_telemetry=G_telemetry, C_ee=C_ee, C_feed=C_feed).items()}
json.dump(out, open(sys.argv[1], "w"), indent=1)
print("gateway non-test files:", len(gw_files))
for k, v in out.items():
    print(f"{k:12s} {len(v)}")
