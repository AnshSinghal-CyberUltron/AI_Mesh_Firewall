"""Prototype replacement for lint/check_capacity_literals.py (proposed fix, evidence only).

Differences from the shipped gate:
  * capacity names matched by whole-word tokens, case-insensitive (MAX_CONNECTIONS, DEFAULT_THREAD_POOL_SIZE)
  * numeric expressions constant-folded (2 ** 6, 64 * 1024)
  * module/class constants, dict entries and parameter defaults resolved at the use site
  * capacity callees matched by name pattern, any numeric arg/kwarg flagged (Semaphore(value=), LifoQueue(64))
  * the only allowance is the EXACT package path runtime/resources.py (no suffix match)
Values 0 and 1 are not flagged (mutex / sentinel semantics)."""
from __future__ import annotations
import ast, re, sys
from pathlib import Path

WORDS = {"pool", "pools", "max", "maxsize", "maxlen", "limit", "limits", "conn", "conns", "connection",
         "connections", "worker", "workers", "queue", "buffer", "bufsize", "depth", "concurrency",
         "keepalive", "backlog", "capacity", "thread", "threads", "semaphore", "size", "lease", "budget",
         "watermark", "water", "processes", "value"}
CALLEE = re.compile(r"(Semaphore|BoundedSemaphore|Queue|PoolExecutor|Pool|deque|CapacityLimiter|TCPConnector|Limits|ConnectionPool)$")
ALLOWED = "runtime/resources.py"

def words(name: str) -> set[str]:
    return {w.lower() for w in re.findall(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])|\d+", name)} | {p.lower() for p in name.split("_") if p}

def fold(node: ast.AST | None, env: dict[str, float]) -> float | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
        return node.value
    if isinstance(node, ast.Name) and node.id in env:
        return env[node.id]
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
        v = fold(node.operand, env); return None if v is None else (-v if isinstance(node.op, ast.USub) else v)
    if isinstance(node, ast.BinOp):
        a, b = fold(node.left, env), fold(node.right, env)
        if a is None or b is None: return None
        ops = {ast.Add: a + b, ast.Sub: a - b, ast.Mult: a * b, ast.Pow: a ** b if abs(b) < 64 else None,
               ast.FloorDiv: a // b if b else None, ast.Div: a / b if b else None, ast.LShift: int(a) << int(b) if 0 <= b < 64 else None}
        return ops.get(type(node.op))
    return None

def cap(name: str | None) -> bool:
    return bool(name) and bool(words(name) & WORDS)

def scan_file(path: Path, rel: str) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    hits: list[str] = []
    env: dict[str, float] = {}
    for st in tree.body:  # module constants (resolved at use sites)
        tgt = st.targets[0] if isinstance(st, ast.Assign) and len(st.targets) == 1 else (st.target if isinstance(st, ast.AnnAssign) else None)
        if isinstance(tgt, ast.Name) and (v := fold(st.value, env)) is not None:
            env[tgt.id] = v
    def flag(line: int, what: str, v: float) -> None:
        if v not in (0, 1): hits.append(f"{rel}:{line}: capacity literal {what}={v:g}")
    def visit(node: ast.AST, scope: dict[str, float]) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            a = node.args; params = a.posonlyargs + a.args
            local = dict(scope)
            for p, d in list(zip(params[len(params) - len(a.defaults):], a.defaults)) + [(k, d) for k, d in zip(a.kwonlyargs, a.kw_defaults) if d is not None]:
                if (v := fold(d, scope)) is not None:
                    local[p.arg] = v
                    if cap(p.arg): flag(d.lineno, f"default {p.arg}", v)
            for ch in node.body: visit(ch, local)
            return
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            tgts = node.targets if isinstance(node, ast.Assign) else [node.target]
            for t in tgts:
                if isinstance(t, ast.Name) and cap(t.id) and (v := fold(node.value, scope)) is not None:
                    flag(node.lineno, t.id, v)
        if isinstance(node, ast.Dict):
            for k, val in zip(node.keys, node.values):
                if isinstance(k, ast.Constant) and isinstance(k.value, str) and cap(k.value) and (v := fold(val, scope)) is not None:
                    flag(node.lineno, f"dict[{k.value!r}]", v)
        if isinstance(node, ast.Call):
            f = node.func; fname = f.id if isinstance(f, ast.Name) else (f.attr if isinstance(f, ast.Attribute) else None)
            capcall = bool(fname and CALLEE.search(fname))
            for i, arg in enumerate(node.args):
                if capcall and (v := fold(arg, scope)) is not None: flag(node.lineno, f"{fname}(arg{i})", v)
            for kw in node.keywords:
                if kw.arg and (capcall or cap(kw.arg)) and (v := fold(kw.value, scope)) is not None:
                    flag(node.lineno, f"{fname}({kw.arg})", v)
        for ch in ast.iter_child_nodes(node): visit(ch, scope)
    for st in tree.body: visit(st, env)
    return sorted(set(hits))

def scan_tree(root: Path) -> list[str]:
    out: list[str] = []
    for p in sorted(root.rglob("*.py")):
        rel = p.relative_to(root).as_posix()
        if rel == ALLOWED or "__pycache__" in rel: continue
        out += scan_file(p, rel)
    return out

if __name__ == "__main__":
    hits = scan_tree(Path(sys.argv[1]))
    print("\n".join(hits) if hits else "OK: no capacity literals")
    raise SystemExit(1 if hits else 0)
