"""GW14b admin-route ledger.

Every console or v1-gateway call in the named admin set, and every gateway-v2
placeholder for that set, must have a ``build`` or ``retire`` line. A retire
line is the signed decision. It is not a success response.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

LEDGER_PATH = Path("docs/plans/evidence/2026-10-26-gw14b/admin-ledger/routes.json")

_PATH_RE = re.compile(
    r"(?:"
    r"/api/admin/gateway/[A-Za-z0-9_/-]+"
    r"|/api/admin/redis/kill-switches/[A-Za-z0-9_/-]+"
    r"|/v1/rag/[A-Za-z0-9_/-]+"
    r"|/v1/vector/[A-Za-z0-9_/-]+"
    r"|/v1/mcp(?:/[A-Za-z0-9_/-]*)?"
    r"|/v1/responses\b"
    r"|/api/gateways/instances/(?:register|telemetry)/"
    r"|/api/mcp-connector/internal/record-event/"
    r"|/gateway/(?:\$\{[^}]+\}|\{[^}]+\})/mcp/(?:\$\{[^}]+\}|\{[^}]+\})/oauth/start"
    r")"
)
_PLACEHOLDER_RE = re.compile(r'\("POST", "([^"]+)"\):\s*_placeholder\(')
_DECISIONS = frozenset({"build", "retire"})


def scan(repo: Path) -> dict[str, list[str]]:
    """Path to the files that mention it. Dynamic segments collapse to ``{id}``."""
    found: dict[str, set[str]] = {}
    roots = (
        repo / "frontend" / "src",
        repo / "gateway" / "ai_mesh_gateway",
        repo / "gateway_v2" / "gateway_v2" / "edge" / "routes.py",
    )
    for root in roots:
        for path in _files(root):
            text = path.read_text(encoding="utf-8", errors="replace")
            rel = path.relative_to(repo).as_posix()
            for match in _PATH_RE.finditer(text):
                if match.end() < len(text) and text[match.end()] == ".":
                    continue
                found.setdefault(_canonical(match.group(0)), set()).add(rel)
            if path.name == "routes.py":
                for match in _PLACEHOLDER_RE.finditer(text):
                    found.setdefault(_canonical(match.group(1)), set()).add(rel)
    return {path: sorted(files) for path, files in sorted(found.items())}


def compare(repo: Path) -> list[str]:
    """Missing, stale, or unsigned routes. A ``build`` on a 501 placeholder fails."""
    ledger_path = repo / LEDGER_PATH
    if not ledger_path.is_file():
        return [f"{LEDGER_PATH.as_posix()} is missing"]
    document = json.loads(ledger_path.read_text(encoding="utf-8"))
    rows = document.get("routes") if isinstance(document, dict) else None
    if not isinstance(rows, list):
        return ["admin ledger routes is not a list"]
    scanned = scan(repo)
    placeholders = _placeholders(repo)
    messages: list[str] = []
    listed: dict[str, dict[str, object]] = {}
    for row in rows:
        if not isinstance(row, dict):
            messages.append("admin ledger row is not an object")
            continue
        path = row.get("path")
        if not isinstance(path, str) or not path:
            messages.append("admin ledger row has no path")
            continue
        listed[path] = row
        messages.extend(_row_errors(path, row, scanned, placeholders))
    for path in sorted(set(scanned) - set(listed)):
        messages.append(f"admin route {path} is not in the ledger")
    return messages


def _row_errors(
    path: str,
    row: dict[str, object],
    scanned: dict[str, list[str]],
    placeholders: set[str],
) -> list[str]:
    messages: list[str] = []
    decision = row.get("decision")
    reason = row.get("reason")
    if decision not in _DECISIONS:
        messages.append(f"{path} decision must be build or retire")
    if not isinstance(reason, str) or not reason.strip():
        messages.append(f"{path} has no reason")
    callers = scanned.get(path)
    if callers is None:
        messages.append(f"{path} is in the ledger but nothing calls it")
    elif row.get("callers") != callers:
        messages.append(f"{path} callers do not match the tree")
    if decision == "build" and path in placeholders:
        messages.append(f"{path} is still a 501 placeholder, so it cannot be build")
    if decision == "retire" and path in placeholders and "501" not in str(reason):
        messages.append(f"{path} retires a 501 and the reason must say so")
    return messages


def _placeholders(repo: Path) -> set[str]:
    path = repo / "gateway_v2" / "gateway_v2" / "edge" / "routes.py"
    text = path.read_text(encoding="utf-8")
    return {_canonical(match.group(1)) for match in _PLACEHOLDER_RE.finditer(text)}


def _canonical(raw: str) -> str:
    text = re.sub(r"\$\{[^}]+\}", "{id}", raw.strip().rstrip("\\"))
    text = re.sub(r"\{[^}]+\}", "{id}", text).rstrip("/")
    oauth = "/gateway/{id}/mcp/{id}/oauth/start"
    if text == oauth:
        return "/gateway/{org}/mcp/{server}/oauth/start"
    return text


def _files(root: Path) -> list[Path]:
    if root.is_file():
        return [root]
    if not root.is_dir():
        return []
    skipped = {"node_modules", "__pycache__", "tests"}
    suffixes = {".py", ".js", ".jsx", ".ts", ".tsx"}
    return sorted(
        path
        for path in root.rglob("*")
        if path.suffix in suffixes
        and not skipped.intersection(path.parts)
        and not path.name.startswith("test_")
        and not path.name.endswith(".test.js")
        and not path.name.endswith(".test.jsx")
    )
