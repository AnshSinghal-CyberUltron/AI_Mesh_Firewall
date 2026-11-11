"""AST gate: DispatchAuthorization minting/forgery only in resolve/ (C8, R10.1-R10.3).

Mirrors ``check_http_outside_edge_resolve.py``. Flags every forgery form of a
``DispatchAuthorization`` (minting, reconstruction, witness mutation, alias evasion)
anywhere OUTSIDE the ``resolve`` top package — the single legal mint site
(``resolve/authorize.py``) — and outside the defining domain module
(``domain/dispatch_authorization.py``), which legitimately constructs the type in its
module-private ``_mint`` and names ``_WITNESS``.

The gate is the primary STATIC guarantee behind the C11 "not constructible outside
resolve/" contract; the runtime witness-identity assertion at the provider entry is the
dynamic complement. It covers the prototype's 17-form forgery class with a focused set of
AST matchers over these categories (one dirty fixture per form in the gate's own unit
test, Task 9):

  1. Direct construction ``DispatchAuthorization(...)``.
  2. ``_mint(...)`` call (incl. ``from ... import _mint as X``).
  3. ``dataclasses.replace(auth, ...)`` / ``replace(auth, ...)``.
  4. ``object.__new__(DispatchAuthorization)``.
  5. ``object.__setattr__(x, "_witness", ...)`` and attribute assign ``x._witness = ...``.
  6. ``copy.copy(...)`` / ``copy.deepcopy(...)`` of a tracked name.
  7. ``type(auth)(...)`` reconstruction.
  8. Import-alias evasion: ``from ... import DispatchAuthorization as X`` / ``import ... as Y``
     — the alias is tracked per file and treated as the canonical name throughout.

Per-file import aliases are resolved first (categories 1/2/8), then calls/assignments are
matched against the tracked name set plus the canonical names. Runnable as
``python -m lint.check_dispatch_provenance gateway_v2`` with ``raise SystemExit(main())``;
exits non-zero on any hit.
"""

from __future__ import annotations

import ast
import sys
from dataclasses import dataclass
from pathlib import Path

#: The sealed type and its module-private mint — the two names a forgery targets.
AUTH_NAME = "DispatchAuthorization"
MINT_NAME = "_mint"
WITNESS_FIELD = "_witness"

#: Only ``resolve/`` may mint. The defining domain module constructs the type inside
#: ``_mint`` and names ``_WITNESS``, so it is allowed by its repo-relative path.
ALLOWED_TOP = frozenset({"resolve"})
ALLOWED_RELPATHS = frozenset({"domain/dispatch_authorization.py"})

#: The gate's own forgery fixtures (Task 9) intentionally contain every forgery form so
#: the unit test can point ``scan_tree`` at them; they must NOT trip the tree-wide run.
#: Excluded like the secret gate's allowlist. The behavioural suite's recorder/negative
#: snippets live under ``tests/`` too.
ALLOWED_DIR_PARTS = frozenset({"tests"})

#: Reconstruction / forgery call targets matched by callee name.
_RECONSTRUCTORS = frozenset({"replace", "__new__", "__setattr__", "copy", "deepcopy"})


@dataclass(frozen=True)
class ProvenanceViolation:
    path: str
    form: str
    line: int


def _rel(path: Path, root: Path) -> Path | None:
    try:
        return path.resolve().relative_to(root.resolve())
    except ValueError:
        return None


def _rel_top(path: Path, root: Path) -> str | None:
    rel = _rel(path, root)
    if rel is None:
        return None
    return rel.parts[0] if rel.parts else None


def _py_files(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*.py") if "__pycache__" not in p.parts)


def _is_allowed(path: Path, root: Path) -> bool:
    """resolve/ (mint site), the defining domain module, and test fixtures are allowed."""
    if _rel_top(path, root) in ALLOWED_TOP:
        return True
    rel = _rel(path, root)
    if rel is None:
        return False
    if rel.as_posix() in ALLOWED_RELPATHS:
        return True
    return bool(ALLOWED_DIR_PARTS.intersection(rel.parts))


def _attr_chain(node: ast.expr) -> str | None:
    """Dotted attribute/name chain as a string, e.g. ``dataclasses.replace`` or ``copy``."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _attr_chain(node.value)
        return f"{base}.{node.attr}" if base is not None else node.attr
    return None


def _import_tracked_names(tree: ast.AST) -> tuple[frozenset[str], frozenset[str]]:
    """Per-file local names bound to ``DispatchAuthorization`` and to ``_mint`` by import.

    Covers import-alias evasion (form 8): ``from ... import DispatchAuthorization as X``
    binds ``X`` to the auth name; ``from ... import _mint as Y`` binds ``Y`` to the mint
    name. A bare ``import gateway_v2.domain.dispatch_authorization as M`` binds ``M`` as a
    module alias so ``M.DispatchAuthorization`` / ``M._mint`` attribute access is caught.
    """
    auth: set[str] = {AUTH_NAME}
    mint: set[str] = {MINT_NAME}
    module_aliases: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                local = alias.asname or alias.name
                if alias.name == AUTH_NAME:
                    auth.add(local)
                elif alias.name == MINT_NAME:
                    mint.add(local)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.endswith("dispatch_authorization") and alias.asname:
                    module_aliases.add(alias.asname)
    # ``M.DispatchAuthorization`` / ``M._mint`` via a module alias.
    for mod in module_aliases:
        auth.add(f"{mod}.{AUTH_NAME}")
        mint.add(f"{mod}.{MINT_NAME}")
    return frozenset(auth), frozenset(mint)


def _auth_bearing_vars(
    tree: ast.AST,
    auth: frozenset[str],
    mint: frozenset[str],
) -> frozenset[str]:
    """Local variables that hold an authorization, so forms 3/6/7 are catchable.

    A simple-``Name`` target assigned from a Call whose callee is a tracked
    construction / mint / reconstruction (``DispatchAuthorization(...)``, ``_mint(...)``,
    ``replace``/``copy``/``deepcopy``/``__new__``, or ``type(auth)(...)``) is treated as
    carrying an authorization. This lets ``auth = _mint(...)`` then
    ``dataclasses.replace(auth, ...)`` / ``copy.deepcopy(auth)`` / ``type(auth)(...)`` be
    flagged without full type inference. Function parameters named like an auth are also
    included so a fixture taking ``auth: DispatchAuthorization`` is covered.
    """
    bearing: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name) and _attr_chain(node.annotation) in auth:
                bearing.add(node.target.id)
        elif isinstance(node, ast.Assign):
            value = node.value
            if not isinstance(value, ast.Call):
                continue
            callee = _attr_chain(value.func)
            leaf = callee.rsplit(".", 1)[-1] if callee else None
            produces_auth = (
                callee in auth
                or callee in mint
                or (leaf in _RECONSTRUCTORS)
                or _is_type_of_tracked(value.func, auth)
            )
            if not produces_auth:
                continue
            for target in node.targets:
                if isinstance(target, ast.Name):
                    bearing.add(target.id)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for arg in (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs):
                if arg.annotation is not None and _attr_chain(arg.annotation) in auth:
                    bearing.add(arg.arg)
    return frozenset(bearing)


def _is_type_of_tracked(node: ast.expr, auth: frozenset[str]) -> bool:
    """``type(auth)`` where the argument resolves to a tracked auth name (form 7)."""
    if not isinstance(node, ast.Call):
        return False
    if _attr_chain(node.func) != "type" or len(node.args) != 1:
        return False
    return _attr_chain(node.args[0]) in auth


def _arg_mentions_tracked(call: ast.Call, tracked: frozenset[str]) -> bool:
    """Any positional/keyword argument resolves to a tracked auth name or bearing var."""
    args = list(call.args) + [kw.value for kw in call.keywords]
    return any(_attr_chain(a) in tracked for a in args)


def _symbols(
    tree: ast.AST,
    auth: frozenset[str],
    mint: frozenset[str],
    bearing: frozenset[str],
) -> list[tuple[str, int]]:
    # Names that stand for an authorization VALUE: the type name itself and any local
    # variable shown to hold one (so ``replace(auth, ...)`` / ``type(auth)()`` fire).
    auth_values = auth | bearing
    found: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            callee = _attr_chain(node.func)
            # Form 1: direct construction DispatchAuthorization(...) (incl. aliases).
            if callee in auth:
                found.append(("DispatchAuthorization construction", node.lineno))
            # Form 2: _mint(...) call (incl. aliases).
            elif callee in mint:
                found.append(("_mint call", node.lineno))
            # Form 7: type(auth)(...) reconstruction.
            elif _is_type_of_tracked(node.func, auth_values):
                found.append(("type(auth)(...) reconstruction", node.lineno))
            # Forms 3/4/6: reconstruction helpers on a tracked auth value.
            elif callee is not None:
                leaf = callee.rsplit(".", 1)[-1]
                if leaf == "__setattr__" and _setattr_targets_witness(node):
                    found.append(("object.__setattr__ of _witness", node.lineno))
                elif leaf in _RECONSTRUCTORS and _arg_mentions_tracked(node, auth_values):
                    found.append((f"{leaf}() forgery of DispatchAuthorization", node.lineno))
        # Form 5: attribute assignment x._witness = ...
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if _is_witness_attr(target):
                    found.append(("attribute assign of _witness", node.lineno))
        elif isinstance(node, ast.AnnAssign):
            if _is_witness_attr(node.target):
                found.append(("attribute assign of _witness", node.lineno))
    return found


def _is_witness_attr(node: ast.expr) -> bool:
    return isinstance(node, ast.Attribute) and node.attr == WITNESS_FIELD


def _setattr_targets_witness(call: ast.Call) -> bool:
    """``object.__setattr__(x, "_witness", ...)`` — second arg is the field name."""
    if len(call.args) < 2:
        return False
    name_arg = call.args[1]
    return isinstance(name_arg, ast.Constant) and name_arg.value == WITNESS_FIELD


def scan_file(path: Path) -> list[tuple[str, int]]:
    """Scan a single file for forgery forms (test fixtures point here directly)."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError):
        return []
    auth, mint = _import_tracked_names(tree)
    bearing = _auth_bearing_vars(tree, auth, mint)
    return _symbols(tree, auth, mint, bearing)


def scan_tree(root: Path) -> list[ProvenanceViolation]:
    hits: list[ProvenanceViolation] = []
    for path in _py_files(root):
        if _is_allowed(path, root):
            continue
        for form, line in scan_file(path):
            hits.append(ProvenanceViolation(str(path), form, line))
    return hits


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    root = Path(args[0] if args else "gateway_v2")
    hits = scan_tree(root)
    for hit in hits:
        print(f"{hit.path}:{hit.line}: forbidden {hit.form} outside resolve/")
    return 1 if hits else 0


if __name__ == "__main__":
    raise SystemExit(main())
