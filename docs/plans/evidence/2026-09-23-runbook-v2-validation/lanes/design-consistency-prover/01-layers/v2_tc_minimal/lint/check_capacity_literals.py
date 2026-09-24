"""AST gate: capacity literals only in runtime/resources.py."""

from __future__ import annotations

import ast
import sys
from dataclasses import dataclass
from pathlib import Path

CAPACITY_KEYWORDS = frozenset(
    {
        "max_connections",
        "max_connection",
        "maxsize",
        "max_size",
        "workers",
        "worker_count",
        "max_workers",
        "queue_depth",
        "queue_size",
        "backlog",
        "pool_size",
        "high_water",
        "high_watermark",
        "high_water_mark",
        "buffer_size",
        "bufsize",
        "stream_buffer",
        "stream_buffer_bytes",
        "worker_connections",
        "connection_budget",
    },
)
CAPACITY_CALLS = frozenset(
    {
        "Semaphore",
        "BoundedSemaphore",
        "ThreadPoolExecutor",
        "Queue",
    },
)
ALLOWED_REL = "runtime/resources.py"


@dataclass(frozen=True)
class CapacityViolation:
    path: str
    symbol: str
    line: int


def _py_files(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*.py") if "__pycache__" not in p.parts)


def _rel(path: Path, root: Path) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve())).replace("\\", "/")
    except ValueError:
        return path.name


def _int_const(node: ast.expr | None) -> int | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, int):
        return node.value
    return None


def _call_name(func: ast.expr) -> str | None:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _target_ids(node: ast.AST) -> list[str]:
    if isinstance(node, ast.Name):
        return [node.id]
    if isinstance(node, ast.Tuple):
        names: list[str] = []
        for elt in node.elts:
            names.extend(_target_ids(elt))
        return names
    return []


def _symbols(tree: ast.AST) -> list[tuple[str, int]]:
    found: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            found.extend(_call_hits(node))
        elif isinstance(node, ast.Assign):
            value = _int_const(node.value)
            if value is None:
                continue
            for target in node.targets:
                for name in _target_ids(target):
                    if name in CAPACITY_KEYWORDS:
                        found.append((f"{name}={value}", node.lineno))
        elif isinstance(node, ast.AnnAssign):
            value = _int_const(node.value)
            if value is None:
                continue
            for name in _target_ids(node.target):
                if name in CAPACITY_KEYWORDS:
                    found.append((f"{name}={value}", node.lineno))
    return found


def _call_hits(node: ast.Call) -> list[tuple[str, int]]:
    hits: list[tuple[str, int]] = []
    name = _call_name(node.func)
    if name in CAPACITY_CALLS and node.args:
        first = _int_const(node.args[0])
        if first is not None:
            hits.append((f"{name}({first})", node.lineno))
    for kw in node.keywords:
        value = _int_const(kw.value)
        if kw.arg in CAPACITY_KEYWORDS and value is not None:
            hits.append((f"{kw.arg}={value}", node.lineno))
    return hits


def scan_tree(root: Path) -> list[CapacityViolation]:
    hits: list[CapacityViolation] = []
    for path in _py_files(root):
        rel = _rel(path, root)
        if rel == ALLOWED_REL or rel.endswith("/" + ALLOWED_REL):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError:
            continue
        for symbol, line in _symbols(tree):
            hits.append(CapacityViolation(str(path), symbol, line))
    return hits


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    root = Path(args[0] if args else "gateway_v2")
    hits = scan_tree(root)
    for hit in hits:
        print(f"{hit.path}:{hit.line}: capacity literal {hit.symbol} outside runtime/resources.py")
    return 1 if hits else 0


if __name__ == "__main__":
    raise SystemExit(main())
