"""AST gate: no work proportional to the number of tenants in the data plane (§2.1 rule 1).

R2-02 is the most expensive defect round 2 found: a periodic whole-set refresh took C4 p99 from
12 ms at 3 tenants to 145-153 ms at 10,000 and 405-459 ms at 25,000. Every individual cause was
a reasonable-looking line of code:

    HVALS   {rv2}:ks          re-verify every kill-switch record, twice a second
    HGETALL {rv2}:plan_index   re-read every tenant's index entry, once a second
    for org in store.known()   iterate the estate to pre-warm last-known-good

None of those is wrong in isolation, which is why a reviewer let them through twice (round 1's
C28 found the same shape and it came back). So this gate forbids the SHAPES rather than relying
on anyone noticing the cost again.

Two rules, both exact:

1. **Whole-collection store commands** are forbidden in the data plane. A command that returns a
   container's entire contents cannot be O(changes), whatever the caller intends. The one
   exception is allowlisted BY (file, command) and justified in `ALLOWED`: the kill-switch cold
   start reads the published ENGAGED set, which is O(engaged) and normally empty, and reading it
   is precisely what avoids reading the kind.

2. **`known()` must not be called from product code.** It returns every tenant, sorted. It is
   there for diagnostics and tests; the moment a refresh path calls it, R2-02 is back.

The control plane is NOT scanned: the re-hydrator's repair path and its deep verification are
O(records) by definition, and they run off the serving loop. That asymmetry is the design, and
`state_control` carries its own tests asserting the write path cannot reach `publish_kind`.
"""

from __future__ import annotations

import ast
import sys
from dataclasses import dataclass
from pathlib import Path

WHOLE_COLLECTION_COMMANDS = frozenset(
    {
        "hgetall",
        "hvals",
        "hkeys",
        "smembers",
        "sscan",
        "hscan",
        "zscan",
        "scan",
        "scan_iter",
        "keys",
        "zrange",
        "zrangebylex",
        "zrevrangebylex",
        "lrange",
        "getall",
    },
)

ESTATE_ENUMERATORS = frozenset({"known"})

ALLOWED = frozenset(
    {
        # The kill-switch cold start reads the published engaged set: O(engaged), not O(records).
        # Reading it is what makes a fresh worker cheap instead of O(tenants) (gate G-04).
        ("runtime/store_valkey.py", "smembers"),
    },
)


@dataclass(frozen=True)
class TenantScaleViolation:
    path: str
    symbol: str
    line: int
    reason: str


def _py_files(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*.py") if "__pycache__" not in p.parts)


def _rel(path: Path, root: Path) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve())).replace("\\", "/")
    except ValueError:
        return path.name


def _method_name(node: ast.Call) -> str | None:
    """Only ATTRIBUTE calls — `client.hgetall(...)`, not a local named `keys(...)`."""
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return None


def _defines(tree: ast.AST, name: str) -> bool:
    """A module that DEFINES the method is not calling it."""
    return any(
        isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name
        for node in ast.walk(tree)
    )


def _hits(tree: ast.AST, rel: str) -> list[tuple[str, int, str]]:
    found: list[tuple[str, int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = _method_name(node)
        if name is None:
            continue
        if name in WHOLE_COLLECTION_COMMANDS and (rel, name) not in ALLOWED:
            found.append(
                (
                    name,
                    node.lineno,
                    "whole-collection store read: cannot be O(changes)",
                ),
            )
        elif name in ESTATE_ENUMERATORS and not _defines(tree, name):
            found.append(
                (
                    f"{name}()",
                    node.lineno,
                    "enumerates every tenant: diagnostics and tests only",
                ),
            )
    return found


def scan_tree(root: Path) -> list[TenantScaleViolation]:
    violations: list[TenantScaleViolation] = []
    for path in _py_files(root):
        rel = _rel(path, root)
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError:
            continue
        for symbol, line, reason in _hits(tree, rel):
            violations.append(TenantScaleViolation(str(path), symbol, line, reason))
    return violations


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    root = Path(args[0] if args else "gateway_v2")
    violations = scan_tree(root)
    for hit in violations:
        print(f"{hit.path}:{hit.line}: {hit.symbol}: {hit.reason}")
    if violations:
        print(
            f"{len(violations)} tenant-scale violation(s). "
            "See §2.1 rule 1 and R2-02: no work proportional to tenants, keys or records "
            "on a serving path.",
        )
    return 1 if violations else 0


if __name__ == "__main__":
    raise SystemExit(main())
