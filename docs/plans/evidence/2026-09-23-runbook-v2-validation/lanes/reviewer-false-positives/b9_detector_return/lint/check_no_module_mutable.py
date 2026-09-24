"""AST gate: reject module-level list/dict/set literals and list()/dict()/set()."""

from __future__ import annotations

import ast
import sys
from dataclasses import dataclass
from pathlib import Path

_MUTABLE_CALLS = frozenset({"list", "dict", "set"})


@dataclass(frozen=True)
class MutableViolation:
    path: str
    name: str
    line: int


def _py_files(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*.py") if "__pycache__" not in p.parts)


def _is_type_checking(test: ast.AST) -> bool:
    if isinstance(test, ast.Name) and test.id == "TYPE_CHECKING":
        return True
    return isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"


def _is_mutable(value: ast.AST | None) -> bool:
    if value is None:
        return False
    if isinstance(value, (ast.List, ast.Dict, ast.Set, ast.ListComp, ast.DictComp, ast.SetComp)):
        return True
    if isinstance(value, ast.Call) and isinstance(value.func, ast.Name):
        return value.func.id in _MUTABLE_CALLS
    return False


def _target_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return type(node).__name__


def _walk_mod(stmts: list[ast.stmt], path: str, hits: list[MutableViolation]) -> None:
    for stmt in stmts:
        if isinstance(stmt, ast.If):
            if _is_type_checking(stmt.test):
                continue
            _walk_mod(stmt.body + stmt.orelse, path, hits)
            continue
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        if isinstance(stmt, ast.Assign) and _is_mutable(stmt.value):
            name = _target_name(stmt.targets[0]) if stmt.targets else "?"
            hits.append(MutableViolation(path, name, stmt.lineno))
        elif isinstance(stmt, ast.AnnAssign) and _is_mutable(stmt.value):
            hits.append(MutableViolation(path, _target_name(stmt.target), stmt.lineno))


def scan_file(path: Path) -> list[MutableViolation]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except SyntaxError:
        return []
    hits: list[MutableViolation] = []
    _walk_mod(tree.body, str(path), hits)
    return hits


def scan_tree(root: Path) -> list[MutableViolation]:
    hits: list[MutableViolation] = []
    for path in _py_files(root):
        hits.extend(scan_file(path))
    return hits


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    root = Path(args[0] if args else "gateway_v2")
    hits = scan_tree(root)
    for hit in hits:
        print(f"{hit.path}:{hit.line}: module-level mutable {hit.name}")
    return 1 if hits else 0


if __name__ == "__main__":
    raise SystemExit(main())
