"""AST gate: HTTPException, JSONResponse, status_code=4xx only in edge/ and resolve/."""

from __future__ import annotations

import ast
import sys
from dataclasses import dataclass
from pathlib import Path

FORBIDDEN = frozenset({"HTTPException", "JSONResponse"})
ALLOWED_TOP = frozenset({"edge", "resolve"})


@dataclass(frozen=True)
class HttpViolation:
    path: str
    symbol: str
    line: int


def _rel_top(path: Path, root: Path) -> str | None:
    try:
        rel = path.resolve().relative_to(root.resolve())
    except ValueError:
        return None
    return rel.parts[0] if rel.parts else None


def _py_files(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*.py") if "__pycache__" not in p.parts)


def _const_4xx(node: ast.expr) -> bool:
    if not isinstance(node, ast.Constant) or not isinstance(node.value, int):
        return False
    return 400 <= node.value <= 499


def _symbols(tree: ast.AST) -> list[tuple[str, int]]:
    found: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id in FORBIDDEN:
            found.append((node.id, node.lineno))
        elif isinstance(node, ast.Attribute) and node.attr in FORBIDDEN:
            found.append((node.attr, node.lineno))
        elif isinstance(node, ast.Call):
            for kw in node.keywords:
                if kw.arg == "status_code" and _const_4xx(kw.value):
                    found.append(("status_code=4xx", node.lineno))
    return found


def scan_tree(root: Path) -> list[HttpViolation]:
    hits: list[HttpViolation] = []
    for path in _py_files(root):
        top = _rel_top(path, root)
        if top in ALLOWED_TOP:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError:
            continue
        for symbol, line in _symbols(tree):
            hits.append(HttpViolation(str(path), symbol, line))
    return hits


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    root = Path(args[0] if args else "gateway_v2")
    hits = scan_tree(root)
    for hit in hits:
        print(f"{hit.path}:{hit.line}: forbidden {hit.symbol} outside edge/ and resolve/")
    return 1 if hits else 0


if __name__ == "__main__":
    raise SystemExit(main())
