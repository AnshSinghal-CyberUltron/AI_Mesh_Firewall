import ast, sys
from pathlib import Path
root = Path(sys.argv[1]) / "gateway_v2" / "runtime"
for f in sorted(root.glob("*.py")):
    src = f.read_text().splitlines(); tree = ast.parse("\n".join(src))
    nums = [(n.lineno, n.value) for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)) and not isinstance(n.value, bool)]
    envs = sorted({(n.lineno, a.value) for n in ast.walk(tree) if isinstance(n, ast.Call)
                   for a in n.args if isinstance(a, ast.Constant) and isinstance(a.value, str) and a.value.isupper() and ("AMF_" in a.value or "RESOURCE_" in a.value or a.value == "WEB_CONCURRENCY")})
    if not nums and not envs: continue
    print(f"== runtime/{f.name}")
    for ln, v in sorted(set(nums)): print(f"   L{ln:<4d} literal {v!r:<12}  | {src[ln-1].strip()[:95]}")
    for ln, v in envs: print(f"   L{ln:<4d} env     {v:<24}| {src[ln-1].strip()[:80]}")
