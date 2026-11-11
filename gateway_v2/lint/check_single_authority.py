"""AST gate: the resolver is the single authority (GW33 C2, requirement 5.3).

Two rules over every module OUTSIDE ``gateway_v2/resolve/`` and ``gateway_v2/domain/``:

* Rule A: a function (or a module-level dict literal) that maps an ``Action.<MEMBER>`` to a
  ``Disposition.<MEMBER>`` in ANY shape (if-chain, dict literal, ``match``) is a second
  Action-to-Disposition site.
* Rule B: a ``Transformation(...)`` call whose ``kind`` is the string literal ``"rewrite"`` or
  ``"redact"`` (keyword or first positional) is a second site choosing how bytes are edited.

The frozen allow-list names the two legitimate non-resolver sites with a reason each. A stale entry
(file or function gone) is itself a violation, so the list cannot rot. Runnable as
``python -m lint.check_single_authority gateway_v2``; exits 1 on any hit and prints
``path:line: symbol``.
"""
from __future__ import annotations

import ast
import sys
from dataclasses import dataclass
from pathlib import Path

#: Top packages that ARE the authority (or define the vocabulary).
EXEMPT_TOP = frozenset({"resolve", "domain"})
#: ``(relative_path, qualified_function_name, reason)``.
ALLOW_LIST: tuple[tuple[str, str, str], ...] = (
    (
        "egress/output_guard.py",
        "MinimalOutputResolver.decide",
        "minimal in-process harness resolver keyed off the holdback trade-off table",
    ),
    (
        "egress/holdback_tradeoff.py",
        "with_remainder_redactions",
        "force-release remainder redaction folded into the resolver's decision (R6.3)",
    ),
)
_LITERAL_KINDS = frozenset({"rewrite", "redact"})


@dataclass(frozen=True)
class AuthorityViolation:
    path: str
    symbol: str
    line: int


def _py_files(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*.py") if "__pycache__" not in p.parts)


def _member(node: ast.AST, owner: str) -> bool:
    return (
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == owner
    )


def _contains_member(node: ast.AST | None, owner: str) -> bool:
    return node is not None and any(_member(child, owner) for child in ast.walk(node))


def _function_hits_rule_a(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    if not any(_member(n, "Action") for n in ast.walk(fn)):
        return False
    for node in ast.walk(fn):
        if isinstance(node, (ast.Return, ast.Assign, ast.AnnAssign)) and _contains_member(
            node.value, "Disposition",
        ):
            return True
        if isinstance(node, ast.Dict) and any(
            _contains_member(v, "Disposition") for v in node.values
        ):
            return True
    return False


def _kind_literal(call: ast.Call) -> str | None:
    for kw in call.keywords:
        if kw.arg == "kind" and isinstance(kw.value, ast.Constant):
            return str(kw.value.value)
    if call.args and isinstance(call.args[0], ast.Constant):
        return str(call.args[0].value)
    return None


def _callee(call: ast.Call) -> str | None:
    if isinstance(call.func, ast.Name):
        return call.func.id
    if isinstance(call.func, ast.Attribute):
        return call.func.attr
    return None


def _qualified_functions(tree: ast.AST) -> list[tuple[str, ast.FunctionDef | ast.AsyncFunctionDef]]:
    found: list[tuple[str, ast.FunctionDef | ast.AsyncFunctionDef]] = []

    def visit(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                visit(child, f"{prefix}{child.name}.")
            elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                found.append((f"{prefix}{child.name}", child))
                visit(child, f"{prefix}{child.name}.")
            else:
                visit(child, prefix)

    visit(tree, "")
    return found


def _scan_file(
    path: Path,
    rel: str,
    allow: frozenset[tuple[str, str]],
) -> list[AuthorityViolation]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except SyntaxError:
        return []
    hits: list[AuthorityViolation] = []
    functions = _qualified_functions(tree)
    inside: set[int] = set()
    for qualified, fn in functions:
        for node in ast.walk(fn):
            inside.add(id(node))
        if (rel, qualified) in allow:
            continue
        if _function_hits_rule_a(fn):
            hits.append(AuthorityViolation(str(path), f"rule A: {qualified}", fn.lineno))
        for node in ast.walk(fn):
            if (
                isinstance(node, ast.Call)
                and _callee(node) == "Transformation"
                and _kind_literal(node) in _LITERAL_KINDS
                and not _nested_allowed(qualified, fn, node, functions, rel, allow)
            ):
                hits.append(AuthorityViolation(str(path), f"rule B: {qualified}", node.lineno))
    for node in ast.walk(tree):
        if id(node) in inside:
            continue
        if isinstance(node, ast.Dict) and any(_member(k, "Action") for k in node.keys if k):
            if any(_contains_member(v, "Disposition") for v in node.values):
                hits.append(AuthorityViolation(str(path), "rule A: <module>", node.lineno))
        if (
            isinstance(node, ast.Call)
            and _callee(node) == "Transformation"
            and _kind_literal(node) in _LITERAL_KINDS
        ):
            hits.append(AuthorityViolation(str(path), "rule B: <module>", node.lineno))
    return hits


def _nested_allowed(
    qualified: str,
    fn: ast.FunctionDef | ast.AsyncFunctionDef,
    call: ast.Call,
    functions: list[tuple[str, ast.FunctionDef | ast.AsyncFunctionDef]],
    rel: str,
    allow: frozenset[tuple[str, str]],
) -> bool:
    """A call inside a nested function belongs to the INNERMOST function, which may be allowed."""
    for other_name, other in functions:
        if other is fn or other_name == qualified:
            continue
        if (rel, other_name) in allow and any(n is call for n in ast.walk(other)):
            return True
    return False


def scan_tree(
    root: Path,
    *,
    allow_list: tuple[tuple[str, str, str], ...] = ALLOW_LIST,
    check_stale: bool = True,
) -> list[AuthorityViolation]:
    allow = frozenset((rel, name) for rel, name, _reason in allow_list)
    hits: list[AuthorityViolation] = []
    for path in _py_files(root):
        rel = path.resolve().relative_to(root.resolve()).as_posix()
        if rel.split("/", 1)[0] in EXEMPT_TOP:
            continue
        hits.extend(_scan_file(path, rel, allow))
    if check_stale:
        hits.extend(_stale_entries(root, allow_list))
    return hits


def _stale_entries(
    root: Path,
    allow_list: tuple[tuple[str, str, str], ...],
) -> list[AuthorityViolation]:
    stale: list[AuthorityViolation] = []
    for rel, name, _reason in allow_list:
        path = root / rel
        names: set[str] = set()
        if path.is_file():
            try:
                names = {q for q, _fn in _qualified_functions(ast.parse(path.read_text("utf-8")))}
            except SyntaxError:
                names = set()
        if name not in names:
            stale.append(AuthorityViolation(str(path), f"stale allow-list entry: {name}", 1))
    return stale


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    root = Path(args[0] if args else "gateway_v2")
    hits = scan_tree(root)
    for hit in hits:
        print(f"{hit.path}:{hit.line}: {hit.symbol}")
    return 1 if hits else 0


if __name__ == "__main__":
    raise SystemExit(main())
