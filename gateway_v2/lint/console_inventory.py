"""GW14b machine-readable inventory of the console contract.

Regenerates the three artifacts the card names from this tree:

* frontend reads of gateway-produced fields
* control-plane event endpoints and the drain defaults
* FirewallConfig fields the console can write

The checked-in JSON is the contract. ``check_console_inventory`` fails when a
fresh scan disagrees with it, which is the consumer half of L14b-4.
"""

from __future__ import annotations

import ast
import json
import re
from collections.abc import Iterable
from pathlib import Path

HEADER_RE = re.compile(r"x-zeroshield-[a-z0-9-]+", re.IGNORECASE)
MEMBER_RE = re.compile(r"(?P<obj>[A-Za-z_$][\w$]*)\??\.([A-Za-z_$][\w$]*)")
BRACKET_RE = re.compile(r"""\[\s*["']([A-Za-z_][\w-]*)["']\s*\]""")
API_RE = re.compile(r"""["'`](/(?:api|v1)/[A-Za-z0-9_./{}<>-]+)["'`]""")
TEMPLATE_RE = re.compile(r"\$\{[^}]+\}")

CONSUMER_MARKERS = (
    "/api/security/",
    "/api/gateways/",
    "/api/notifications",
    "/v1/",
    "zeroshield",
    "pipeline_trace",
    "pipelineTrace",
    "x-zeroshield",
)

VIA_OBJECTS = {
    "zeroshield": "zeroshield",
    "zs": "zeroshield",
    "pipeline_trace": "pipeline_trace",
    "pipelineTrace": "pipeline_trace",
    "trace": "pipeline_trace",
    "stage": "pipeline_trace",
    "stages": "pipeline_trace",
    "metadata": "enforcement_event",
    "meta": "enforcement_event",
    "extra": "enforcement_event",
    "event": "enforcement_event",
}

INVENTORY_ROOT = Path("docs/plans/evidence/2026-10-26-gw14b")
FIELDS_PATH = INVENTORY_ROOT / "frontend-verifier" / "fields.json"
CONSUMERS_PATH = INVENTORY_ROOT / "cp-consumers" / "endpoints.json"
CONFIG_PATH = INVENTORY_ROOT / "config-keys" / "firewallconfig.json"


def repo_root() -> Path:
    """This file lives at ``<repo>/gateway_v2/lint/console_inventory.py``."""
    return Path(__file__).resolve().parents[2]


def _parse(path: Path) -> ast.AST:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _walk(tree: ast.AST, fn_name: str | None) -> Iterable[ast.AST]:
    if fn_name is None:
        return ast.walk(tree)
    functions = [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == fn_name
    ]
    return (child for fn in functions for child in ast.walk(fn))


def dict_keys(tree: ast.AST, fn_name: str | None = None) -> set[str]:
    """String keys a function stores into a dict, including ``.get`` arguments."""
    found: set[str] = set()
    for node in _walk(tree, fn_name):
        if isinstance(node, ast.Dict):
            for key in node.keys:
                if isinstance(key, ast.Constant) and isinstance(key.value, str):
                    found.add(key.value)
        elif isinstance(node, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if (
                    isinstance(target, ast.Subscript)
                    and isinstance(target.slice, ast.Constant)
                    and isinstance(target.slice.value, str)
                ):
                    found.add(target.slice.value)
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr == "get" and node.args:
                arg = node.args[0]
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    found.add(arg.value)
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "dict"
        ):
            for keyword in node.keywords:
                if keyword.arg:
                    found.add(keyword.arg)
    return found


def _py_files(root: Path) -> list[Path]:
    skipped = {"tests", "__pycache__"}
    return sorted(
        path
        for path in root.rglob("*.py")
        if not skipped.intersection(path.parts)
        and not path.name.startswith("test_")
        and path.name != "conftest.py"
    )


def _keys_of(path: Path, fn_name: str | None = None) -> set[str]:
    if not path.is_file():
        raise FileNotFoundError(path)
    return dict_keys(_parse(path), fn_name)


def producer_keys(repo: Path) -> dict[str, list[str]]:
    """Key universes the September ``gw_keys.py`` scan defined, on this tree."""
    gateway = repo / "gateway" / "ai_mesh_gateway"
    collected: dict[str, set[str]] = {
        "G_gateway": set(),
        "G_trace": set(),
        "G_telemetry": set(),
        "C_ee": set(),
    }
    for path in _py_files(gateway):
        try:
            collected["G_gateway"] |= dict_keys(_parse(path))
        except SyntaxError:
            continue
    collected["G_trace"] = _keys_of(gateway / "pipeline_trace.py") | _keys_of(
        gateway / "trace_projection.py",
    )
    collected["G_telemetry"] = _keys_of(gateway / "telemetry.py", "build_telemetry_event")
    tasks = repo / "control" / "ai_mesh_control" / "core" / "tasks.py"
    collected["C_ee"] = _keys_of(tasks, "_build_enforcement_metadata")
    return {name: sorted(values) for name, values in collected.items()}


def _frontend_sources(repo: Path) -> list[Path]:
    root = repo / "frontend" / "src"
    suffixes = {".js", ".jsx", ".ts", ".tsx"}
    return sorted(
        path
        for path in root.rglob("*")
        if path.suffix in suffixes
        and "node_modules" not in path.parts
        and not path.name.endswith(".test.js")
        and not path.name.endswith(".test.jsx")
        and not path.name.endswith(".spec.js")
    )


def _via(obj: str | None, field: str) -> str:
    if obj is not None and obj in VIA_OBJECTS:
        return VIA_OBJECTS[obj]
    if field.lower().startswith("x-zeroshield-"):
        return "header"
    return "unscoped"


def _record(
    reads: dict[tuple[str, str], set[str]],
    field: str,
    via: str,
    rel: str,
    allowed: set[str],
) -> None:
    if field not in allowed and via != "header":
        return
    reads.setdefault((field, via), set()).add(rel)


def frontend_reads(repo: Path, keys: dict[str, list[str]]) -> list[dict[str, object]]:
    """Property and header reads in console files that talk to the gateway or the event API."""
    allowed = set(keys["G_trace"]) | set(keys["G_telemetry"]) | set(keys["C_ee"])
    reads: dict[tuple[str, str], set[str]] = {}
    for path in _frontend_sources(repo):
        text = path.read_text(encoding="utf-8", errors="replace")
        if not any(marker in text for marker in CONSUMER_MARKERS):
            continue
        rel = path.relative_to(repo).as_posix()
        normalized = TEMPLATE_RE.sub("*", text)
        for match in HEADER_RE.finditer(normalized):
            _record(reads, match.group(0).lower(), "header", rel, allowed)
        for match in MEMBER_RE.finditer(normalized):
            _record(reads, match.group(2), _via(match.group("obj"), match.group(2)), rel, allowed)
        for match in BRACKET_RE.finditer(normalized):
            _record(reads, match.group(1), "unscoped", rel, allowed)
    rows: list[dict[str, object]] = []
    for (field, via), files in sorted(reads.items()):
        rows.append({"field": field, "via": via, "files": sorted(files)})
    return rows


def _class_named(repo: Path, name: str) -> tuple[Path, ast.ClassDef]:
    control = repo / "control" / "ai_mesh_control"
    for path in _py_files(control):
        try:
            tree = _parse(path)
        except SyntaxError:
            continue
        for node in tree.body if isinstance(tree, ast.Module) else []:
            if isinstance(node, ast.ClassDef) and node.name == name:
                return path, node
    raise LookupError(name)


def _route_rows(repo: Path) -> list[dict[str, object]]:
    urls = repo / "control" / "ai_mesh_control" / "policy" / "security_urls.py"
    tree = _parse(urls)
    rows: list[dict[str, object]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
            continue
        if node.func.id != "path":
            continue
        if not node.args or not isinstance(node.args[0], ast.Constant):
            continue
        route = node.args[0].value
        if not isinstance(route, str) or len(node.args) < 2:
            continue
        view = node.args[1]
        view_name = ""
        view_func = view.func if isinstance(view, ast.Call) else None
        if (
            isinstance(view_func, ast.Attribute)
            and isinstance(view_func.value, ast.Name)
        ):
            view_name = view_func.value.id
        if not view_name:
            continue
        path, cls = _class_named(repo, view_name)
        rows.append(
            {
                "path": "/api/security/" + route,
                "view": view_name,
                "file": path.relative_to(repo).as_posix(),
                "line": cls.lineno,
                "metadata_keys": sorted(dict_keys(cls)),
            },
        )
    return rows


def _silent_defaults(repo: Path) -> list[dict[str, object]]:
    """The three fills GW14b says the v2 adapter must not inherit."""
    path = repo / "control" / "ai_mesh_control" / "core" / "tasks.py"
    tree = _parse(path)
    found: list[dict[str, object]] = []
    for node in _walk(tree, "_build_enforcement_metadata"):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr != "get" or len(node.args) < 2:
            continue
        key, default = node.args[0], node.args[1]
        if not isinstance(key, ast.Constant) or not isinstance(key.value, str):
            continue
        if not isinstance(default, ast.Constant):
            continue
        if key.value == "event_type" and default.value == "request":
            found.append({"field": key.value, "default": default.value, "line": node.lineno})
        if key.value == "risk_score" and default.value == 0:
            found.append({"field": key.value, "default": default.value, "line": node.lineno})
    for node in _walk(tree, "_build_enforcement_metadata"):
        if isinstance(node, ast.Constant) and node.value == "1.1":
            found.append({"field": "module_id", "default": "1.1", "line": node.lineno})
    unique: list[dict[str, object]] = []
    seen: set[tuple[str, str, int]] = set()
    for row in found:
        line = row["line"]
        if not isinstance(line, int):
            continue
        ident = (str(row["field"]), str(row["default"]), line)
        if ident in seen:
            continue
        seen.add(ident)
        unique.append(row)
    return unique


def control_plane_consumers(repo: Path) -> dict[str, object]:
    calls: dict[str, set[str]] = {}
    for path in _frontend_sources(repo):
        text = TEMPLATE_RE.sub("*", path.read_text(encoding="utf-8", errors="replace"))
        rel = path.relative_to(repo).as_posix()
        for match in API_RE.finditer(text):
            calls.setdefault(match.group(1), set()).add(rel)
    return {
        "frontend_api_calls": [
            {"path": path, "files": sorted(files)} for path, files in sorted(calls.items())
        ],
        "event_endpoints": _route_rows(repo),
        "drain": {
            "function": "_build_enforcement_metadata",
            "file": "control/ai_mesh_control/core/tasks.py",
            "silent_defaults": _silent_defaults(repo),
        },
    }


def _field_calls(class_node: ast.ClassDef) -> list[tuple[str, str, int]]:
    rows: list[tuple[str, str, int]] = []
    for stmt in class_node.body:
        if not isinstance(stmt, ast.Assign) or not isinstance(stmt.value, ast.Call):
            continue
        func = stmt.value.func
        if isinstance(func, ast.Attribute):
            fname = func.attr
        elif isinstance(func, ast.Name):
            fname = func.id
        else:
            fname = ""
        field_types = {"ForeignKey", "OneToOneField", "ManyToManyField"}
        if not fname.endswith("Field") and fname not in field_types:
            continue
        for target in stmt.targets:
            if isinstance(target, ast.Name):
                rows.append((target.id, fname, stmt.lineno))
    return rows


def _quoted(name: str, corpus: str) -> bool:
    return f'"{name}"' in corpus or f"'{name}'" in corpus


def _corpus(root: Path) -> str:
    parts: list[str] = []
    for path in _py_files(root):
        try:
            parts.append(path.read_text(encoding="utf-8"))
        except UnicodeError:
            continue
    return "\n".join(parts)


def _module(path: Path) -> ast.Module:
    tree = _parse(path)
    if not isinstance(tree, ast.Module):
        raise LookupError(path)
    return tree


def _class(module: ast.Module, name: str) -> ast.ClassDef:
    for node in module.body:
        if isinstance(node, ast.ClassDef) and node.name == name:
            return node
    raise LookupError(name)


def _string_tuple(value: ast.AST) -> tuple[str, ...]:
    evaluated = ast.literal_eval(value)
    if not isinstance(evaluated, tuple):
        raise TypeError("expected a tuple literal")
    return tuple(str(part) for part in evaluated)


def firewall_config(repo: Path) -> dict[str, object]:
    models = repo / "control" / "ai_mesh_control" / "core" / "models.py"
    model = _class(_module(models), "FirewallConfig")
    declared = _field_calls(model)
    serializer = _class(
        _module(repo / "control" / "ai_mesh_control" / "core" / "firewall_config_serializer.py"),
        "FirewallConfigSerializer",
    )
    exclude: tuple[str, ...] = ()
    read_only: tuple[str, ...] = ()
    for stmt in serializer.body:
        if isinstance(stmt, ast.ClassDef) and stmt.name == "Meta":
            for item in stmt.body:
                if not isinstance(item, ast.Assign) or not item.targets:
                    continue
                target = item.targets[0]
                if not isinstance(target, ast.Name):
                    continue
                if target.id == "exclude":
                    exclude = _string_tuple(item.value)
                if target.id == "read_only_fields":
                    read_only = _string_tuple(item.value)
    extra = [name for name, _kind, _line in _field_calls(serializer)]
    model_names = [name for name, _kind, _line in declared]
    exposed = [name for name in model_names if name not in exclude] + [
        name for name in extra if name not in model_names
    ]
    payload_keys = sorted(dict_keys(model, "build_gateway_payload"))
    v1 = _corpus(repo / "gateway" / "ai_mesh_gateway")
    v2 = _corpus(repo / "gateway_v2" / "gateway_v2")
    fields = [
        {
            "name": name,
            "kind": kind,
            "line": line,
            "exposed": name in exposed or name in extra,
            "read_only": name in read_only,
            "quoted_in_v1_gateway": _quoted(name, v1),
            "quoted_in_gateway_v2": _quoted(name, v2),
        }
        for name, kind, line in declared
    ]
    return {
        "redis_key": "firewall:config:{org_slug}",
        "model_field_count": len(declared),
        "serializer_exclude": list(exclude),
        "serializer_read_only": list(read_only),
        "serializer_declared": extra,
        "exposed_count": len(exposed),
        "exposed_fields": exposed,
        "payload_keys": payload_keys,
        "fields": fields,
    }


def build(repo: Path | None = None) -> dict[str, object]:
    root = repo or repo_root()
    keys = producer_keys(root)
    return {
        "fields": {
            "producer_key_counts": {name: len(values) for name, values in keys.items()},
            "reads": frontend_reads(root, keys),
        },
        "cp_consumers": control_plane_consumers(root),
        "config_keys": firewall_config(root),
    }


def dump(document: object) -> str:
    return json.dumps(document, indent=1, sort_keys=True) + "\n"


def _read_key(row: dict[str, object]) -> tuple[str, str, tuple[str, ...]]:
    files = row["files"]
    if not isinstance(files, list):
        raise TypeError("reads[].files must be a list")
    return (str(row["field"]), str(row["via"]), tuple(str(item) for item in files))


def diff_reads(committed: list[dict[str, object]], fresh: list[dict[str, object]]) -> list[str]:
    """L14b-4 consumer half: a frontend read of a gateway field must be in the inventory."""
    committed_keys = {_read_key(row) for row in committed}
    fresh_keys = {_read_key(row) for row in fresh}
    messages: list[str] = []
    for field, via, files in sorted(fresh_keys - committed_keys):
        where = files[0] if files else "?"
        messages.append(f"frontend read {field} via {via} in {where} is not in the inventory")
    for field, via, files in sorted(committed_keys - fresh_keys):
        where = files[0] if files else "?"
        messages.append(f"inventory lists {field} via {via} but {where} no longer reads it")
    return messages
