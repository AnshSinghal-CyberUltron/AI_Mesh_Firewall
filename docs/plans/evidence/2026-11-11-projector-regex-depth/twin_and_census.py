"""Evidence: (1) the gateway_v2 and control `group_depth` twins agree; (2) seed/fixture census.

Run from the repo root with the gateway_v2 venv: `python docs/plans/evidence/<dir>/twin_and_census.py`.
Control's twin is loaded from its source with `ast` (no Django import), so the two
implementations are compared directly on the same inputs.
"""
from __future__ import annotations

import ast
import json
import pathlib
import random
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "gateway_v2"))
from gateway_v2.detect.conditions import MAX_GROUP_DEPTH, group_depth as gw_depth  # noqa: E402


def _control_twin():
    source = (ROOT / "control/ai_mesh_control/policy/serializers.py").read_text()
    tree = ast.parse(source)
    keep = {"MAX_GROUP_DEPTH", "_INLINE_FLAGS_RE", "group_depth", "_class_end", "_skip_past"}
    nodes = [
        n for n in tree.body
        if (isinstance(n, ast.FunctionDef) and n.name in keep)
        or (isinstance(n, ast.Assign) and any(getattr(t, "id", "") in keep for t in n.targets))
    ]
    namespace: dict[str, object] = {"re": re}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), "control-twin", "exec"), namespace)
    return namespace["MAX_GROUP_DEPTH"], namespace["group_depth"]


def _vectors(path: str, name: str) -> str:
    source = (ROOT / path).read_text()
    start = source.index(f"\n{name}")  # the assignment at a line start, not the docstring
    opening = source.index("= (", start) + 2
    return source[opening:source.index("\n)\n", start) + 2]


ctl_max, ctl_depth = _control_twin()
assert ctl_max == MAX_GROUP_DEPTH == 32
gw_vectors = _vectors("gateway_v2/tests/detect/test_condition_group_depth.py", "GROUP_DEPTH_VECTORS")
ctl_vectors = _vectors(
    "control/ai_mesh_control/policy/tests/test_regex_group_depth.py", "GROUP_DEPTH_VECTORS"
)
strip = lambda text: re.sub(r"\s+", "", text)  # noqa: E731
print("vectors identical in both suites (ignoring whitespace):", strip(gw_vectors) == strip(ctl_vectors))

rng = random.Random(0x7717)
alphabet = "()[]^\\#\n ax?:|*+-{}2=!<>PnL"
openers = ["(", "(?:", "(?#", "(?x)", "(?x:", "[", "[^", "[]", "\\", "#", "\n"]
disagree = 0
total = 0
for _ in range(100_000):
    size = rng.randint(0, 60)
    text = "".join(
        rng.choice(openers) if rng.random() < 0.3 else rng.choice(alphabet) for _ in range(size)
    )
    total += 1
    if gw_depth(text) != ctl_depth(text):
        disagree += 1
        if disagree < 5:
            print("DISAGREE", repr(text))
print(f"twin comparison: {total} random strings, {disagree} disagreements")

# --- census: every string literal in seed catalogs, fixtures and migrations --------------------
roots = [
    ROOT / "control/ai_mesh_control",
    ROOT / "gateway_v2",
    ROOT / "gateway",
    ROOT / "shared",
    ROOT / "examples",
    ROOT / "demo",
]
strings = 0
regexish = 0
over = []
deepest = (0, "")
files = 0
for base in roots:
    if not base.exists():
        continue
    for path in base.rglob("*"):
        if path.suffix not in {".py", ".json", ".yaml", ".yml"} or "node_modules" in path.parts:
            continue
        if path.name in {"test_condition_group_depth.py", "test_regex_group_depth.py",
                         "test_projector_poisoned_tenant.py",
                         "test_rewrite_template_refused_condition.py"}:
            continue  # this PR's own deep vectors
        files += 1
        text = path.read_text(errors="replace")
        literals: list[str] = []
        if path.suffix == ".py":
            try:
                tree = ast.parse(text)
            except SyntaxError:
                continue
            literals = [n.value for n in ast.walk(tree)
                        if isinstance(n, ast.Constant) and isinstance(n.value, str)]
        elif path.suffix == ".json":
            try:
                stack = [json.loads(text)]
            except ValueError:
                continue
            while stack:
                item = stack.pop()
                if isinstance(item, str):
                    literals.append(item)
                elif isinstance(item, dict):
                    stack.extend(item.values())
                elif isinstance(item, list):
                    stack.extend(item)
        else:
            literals = text.splitlines()
        for literal in literals:
            strings += 1
            if "(" not in literal:
                continue
            regexish += 1
            depth = gw_depth(literal)
            if depth > deepest[0]:
                deepest = (depth, f"{path.relative_to(ROOT)}: {literal[:80]!r}")
            if depth > MAX_GROUP_DEPTH:
                over.append(f"{path.relative_to(ROOT)}: depth {depth}: {literal[:80]!r}")
print(f"census: {files} files, {strings} string literals, {regexish} containing '('")
print(f"census: deepest literal has group depth {deepest[0]} ({deepest[1]})")
print(f"census: literals deeper than {MAX_GROUP_DEPTH}: {len(over)}")
for line in over[:20]:
    print("  ", line)
