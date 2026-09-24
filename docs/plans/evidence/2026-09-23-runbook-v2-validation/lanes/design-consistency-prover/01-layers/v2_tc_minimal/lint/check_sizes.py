"""Fail if any function exceeds 120 lines or any module exceeds 800 lines."""

from __future__ import annotations

import ast
import sys
from dataclasses import dataclass
from pathlib import Path

MAX_FUNCTION_LINES = 120
MAX_MODULE_LINES = 800


@dataclass(frozen=True)
class SizeViolation:
    path: str
    kind: str
    name: str
    lines: int
    start_line: int


def _py_files(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*.py") if "__pycache__" not in p.parts)


def _fn_span(node: ast.FunctionDef | ast.AsyncFunctionDef) -> int:
    end = node.end_lineno if node.end_lineno is not None else node.lineno
    return end - node.lineno + 1


def scan_file(path: Path) -> list[SizeViolation]:
    text = path.read_text(encoding="utf-8")
    hits: list[SizeViolation] = []
    n_lines = len(text.splitlines()) or (1 if text else 0)
    if n_lines > MAX_MODULE_LINES:
        hits.append(
            SizeViolation(str(path), "module", path.name, n_lines, 1),
        )
    try:
        tree = ast.parse(text, filename=str(path))
    except SyntaxError:
        return hits
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        span = _fn_span(node)
        if span > MAX_FUNCTION_LINES:
            hits.append(
                SizeViolation(str(path), "function", node.name, span, node.lineno),
            )
    return hits


def scan_tree(root: Path) -> list[SizeViolation]:
    hits: list[SizeViolation] = []
    for path in _py_files(root):
        hits.extend(scan_file(path))
    return hits


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    root = Path(args[0] if args else "gateway_v2")
    hits = scan_tree(root)
    for hit in hits:
        limit = MAX_FUNCTION_LINES if hit.kind == "function" else MAX_MODULE_LINES
        print(
            f"{hit.path}:{hit.start_line}: {hit.kind} {hit.name} "
            f"is {hit.lines} lines (max {limit})",
        )
    return 1 if hits else 0


if __name__ == "__main__":
    raise SystemExit(main())
