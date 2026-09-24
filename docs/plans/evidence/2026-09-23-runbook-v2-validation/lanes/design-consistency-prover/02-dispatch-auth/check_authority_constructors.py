"""AST gate (proposed): only gateway_v2/resolve/ may bring a Decision or DispatchAuthorization
into existence.

Outside resolve/, a protected class name may appear ONLY:
  * in an import statement,
  * inside a type annotation (arg/return/AnnAssign annotation),
  * as the class argument of isinstance()/issubclass().
Any other load of the name (call, alias assignment, partial(), cast(), getattr string) is a
violation. Independently, the generic forging primitives are banned outside resolve/:
dataclasses.replace, copy.replace/copy/deepcopy, object.__new__/__setattr__, pickle.load(s),
type(x)(...) and x.__class__(...).
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

PROTECTED = frozenset({"Decision", "DispatchAuthorization"})
ALLOWED_TOP = frozenset({"resolve"})
FORGE_PRIMITIVES = frozenset(
    {
        ("dataclasses", "replace"),
        ("copy", "replace"),
        ("copy", "copy"),
        ("copy", "deepcopy"),
        ("object", "__new__"),
        ("object", "__setattr__"),
        ("pickle", "loads"),
        ("pickle", "load"),
    }
)


def _annotation_nodes(tree: ast.AST) -> set[int]:
    ids: set[int] = set()

    def mark(node: ast.AST | None) -> None:
        if node is not None:
            ids.update(id(n) for n in ast.walk(node))

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            mark(node.returns)
            a = node.args
            for arg in (*a.posonlyargs, *a.args, *a.kwonlyargs, a.vararg, a.kwarg):
                if arg is not None:
                    mark(arg.annotation)
        elif isinstance(node, ast.AnnAssign):
            mark(node.annotation)
    return ids


def _isinstance_class_args(tree: ast.AST) -> set[int]:
    ids: set[int] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in {"isinstance", "issubclass"}
            and len(node.args) == 2
        ):
            ids.update(id(n) for n in ast.walk(node.args[1]))
    return ids


def _aliases(tree: ast.AST) -> tuple[dict[str, str], dict[str, tuple[str, str]]]:
    names: dict[str, str] = {}
    prims: dict[str, tuple[str, str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for a in node.names:
                local = a.asname or a.name
                if a.name in PROTECTED:
                    names[local] = a.name
                if (node.module or "", a.name) in FORGE_PRIMITIVES:
                    prims[local] = (node.module or "", a.name)
    return names, prims


def scan_source(text: str, path: str) -> list[str]:
    tree = ast.parse(text, filename=path)
    ann, isa = _annotation_nodes(tree), _isinstance_class_args(tree)
    names, prims = _aliases(tree)
    hits: list[str] = []
    for node in ast.walk(tree):
        nid = id(node)
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load) and node.id in names:
            if nid not in ann and nid not in isa:
                hits.append(f"{path}:{node.lineno}: non-annotation use of {names[node.id]}")
        elif isinstance(node, ast.Attribute) and node.attr in PROTECTED:
            if nid not in ann and nid not in isa:
                hits.append(f"{path}:{node.lineno}: non-annotation use of {node.attr}")
        elif isinstance(node, ast.Call):
            f = node.func
            if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name):
                if (f.value.id, f.attr) in FORGE_PRIMITIVES:
                    hits.append(f"{path}:{node.lineno}: forging primitive {f.value.id}.{f.attr}")
            if isinstance(f, ast.Name) and f.id in prims:
                mod, fn = prims[f.id]
                hits.append(f"{path}:{node.lineno}: forging primitive {mod}.{fn}")
            if isinstance(f, ast.Call) and isinstance(f.func, ast.Name) and f.func.id == "type":
                hits.append(f"{path}:{node.lineno}: type(x)(...) re-construction")
            if isinstance(f, ast.Attribute) and f.attr == "__class__":
                hits.append(f"{path}:{node.lineno}: x.__class__(...) re-construction")
            if isinstance(f, ast.Name) and f.id == "getattr":
                for arg in node.args[1:2]:
                    if isinstance(arg, ast.Constant) and arg.value in PROTECTED:
                        hits.append(f"{path}:{node.lineno}: getattr(..., {arg.value!r})")
    return hits


def scan_tree(root: Path) -> list[str]:
    hits: list[str] = []
    for path in sorted(root.rglob("*.py")):
        rel = path.relative_to(root)
        if rel.parts and rel.parts[0] in ALLOWED_TOP:
            continue
        hits.extend(scan_source(path.read_text(encoding="utf-8"), str(rel)))
    return hits


if __name__ == "__main__":
    found = scan_tree(Path(sys.argv[1]))
    print("\n".join(found) if found else "OK: no Decision/DispatchAuthorization provenance violations")
    raise SystemExit(1 if found else 0)
