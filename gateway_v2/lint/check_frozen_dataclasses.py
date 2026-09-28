"""AST gate: dataclasses in plan/, detect/, and resolve/ must be frozen and slot-based."""

from __future__ import annotations

import ast
import sys
from dataclasses import dataclass
from pathlib import Path

SCOPED = frozenset({"plan", "detect", "resolve"})


@dataclass(frozen=True)
class FrozenViolation:
    path: str
    name: str
    line: int
    reason: str


def _py_files(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*.py") if "__pycache__" not in p.parts)


def _rel_top(path: Path, root: Path) -> str | None:
    try:
        rel = path.resolve().relative_to(root.resolve())
    except ValueError:
        return None
    return rel.parts[0] if rel.parts else None


def _decorator_name(node: ast.expr) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Call):
        return _decorator_name(node.func)
    return None


def _is_dataclass(node: ast.ClassDef) -> ast.Call | None:
    for deco in node.decorator_list:
        if _decorator_name(deco) != "dataclass":
            continue
        if isinstance(deco, ast.Call):
            return deco
        return ast.Call(func=deco, args=[], keywords=[])
    return None


def _keyword(call: ast.Call, name: str) -> ast.expr | None:
    for kw in call.keywords:
        if kw.arg == name:
            return kw.value
    return None


def _is_true(node: ast.expr | None) -> bool:
    return isinstance(node, ast.Constant) and node.value is True


def scan_tree(root: Path) -> list[FrozenViolation]:
    hits: list[FrozenViolation] = []
    for path in _py_files(root):
        if _rel_top(path, root) not in SCOPED:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            call = _is_dataclass(node)
            if call is None:
                continue
            if not _is_true(_keyword(call, "frozen")):
                hits.append(FrozenViolation(str(path), node.name, node.lineno, "not frozen"))
            if not _is_true(_keyword(call, "slots")):
                hits.append(FrozenViolation(str(path), node.name, node.lineno, "slots not set"))
    return hits


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    root = Path(args[0] if args else "gateway_v2")
    hits = scan_tree(root)
    for hit in hits:
        print(f"{hit.path}:{hit.line}: dataclass {hit.name} {hit.reason}")
    return 1 if hits else 0


if __name__ == "__main__":
    raise SystemExit(main())
