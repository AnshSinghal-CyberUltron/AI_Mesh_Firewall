import ast, inspect
from gateway_v2.runtime import pools
from gateway_v2.runtime.pools import DummyPool
src = inspect.getsource(pools)
writes = [n.lineno for n in ast.walk(ast.parse(src)) if isinstance(n, (ast.Assign, ast.AugAssign))
          and any(isinstance(t, ast.Attribute) and t.attr == "dropped" for t in (n.targets if isinstance(n, ast.Assign) else [n.target]))]
print("lines in pools.py that write .dropped:", writes)
p = DummyPool(4)
for _ in range(4): p.acquire()
p.resize(1)                       # shrink below held leases
print(f"after shrink 4->1 with 4 held: size={p.size} held={p.held} dropped={p.dropped}")
for _ in range(10): p.release()   # over-release: silently ignored, not counted
print(f"after 10 releases (4 legit): held={p.held} completed={p.completed} dropped={p.dropped}")
