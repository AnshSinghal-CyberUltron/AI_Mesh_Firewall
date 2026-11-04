"""AST gate: a credential never reaches a log call, a print, or an exception message (GW17b R14.6).

Scope: the modules that handle upstream credentials (file name contains ``credential``,
``mcp_cred`` or ``mcp_upstream``). In those files, any logging call (``LOG``/``log``/``logger``/
``logging``/``print`` ``.debug/.info/.warning/.error/.exception/.critical/.log``) or ``raise``
whose argument expression mentions a secret-bearing name is a violation. The check is
syntactic and deliberately blunt: it names the identifier, never a value.
"""

from __future__ import annotations

import ast
import sys
from dataclasses import dataclass
from pathlib import Path

SECRET_NAMES = frozenset(
    {
        "token", "secret", "password", "credential", "cred", "refresh_token", "access_token",
        "client_secret", "material", "rendered", "headers", "pairs", "verifier", "code_verifier",
        "authorization",
    }
)
_LOGGERS = frozenset({"LOG", "log", "logger", "logging", "print"})
_SCOPE_MARKERS = ("credential", "mcp_cred", "mcp_upstream")


@dataclass(frozen=True)
class CredentialFlowViolation:
    path: str
    line: int
    detail: str


def _in_scope(path: Path) -> bool:
    return any(marker in path.name for marker in _SCOPE_MARKERS)


def _names(node: ast.AST) -> set[str]:
    found: set[str] = set()
    for sub in ast.walk(node):
        if isinstance(sub, ast.Name):
            found.add(sub.id)
        elif isinstance(sub, ast.Attribute):
            found.add(sub.attr)
        elif isinstance(sub, ast.keyword) and sub.arg:
            found.add(sub.arg)
    return found & SECRET_NAMES


def _is_log_call(node: ast.Call) -> bool:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id in _LOGGERS
    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
        return func.value.id in _LOGGERS
    return False


def scan_source(source: str, filename: str = "<string>") -> list[CredentialFlowViolation]:
    tree = ast.parse(source, filename=filename)
    hits: list[CredentialFlowViolation] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and _is_log_call(node):
            args = [*node.args, *node.keywords]
            leaked = set().union(*(_names(arg) for arg in args)) if args else set()
            if leaked:
                detail = f"log call names {sorted(leaked)}"
                hits.append(CredentialFlowViolation(filename, node.lineno, detail))
        elif isinstance(node, ast.Raise) and node.exc is not None:
            leaked = _names(node.exc)
            if leaked:
                hits.append(
                    CredentialFlowViolation(
                        filename, node.lineno, f"raise message names {sorted(leaked)}"
                    )
                )
    return hits


def scan_tree(root: Path) -> list[CredentialFlowViolation]:
    hits: list[CredentialFlowViolation] = []
    for path in sorted(root.rglob("*.py")):
        if "__pycache__" in path.parts or not _in_scope(path):
            continue
        try:
            hits.extend(scan_source(path.read_text(encoding="utf-8"), str(path)))
        except SyntaxError:
            continue
    return hits


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    hits = scan_tree(Path(args[0] if args else "gateway_v2"))
    for hit in hits:
        print(f"{hit.path}:{hit.line}: credential flow: {hit.detail}")
    return 1 if hits else 0


if __name__ == "__main__":
    raise SystemExit(main())
