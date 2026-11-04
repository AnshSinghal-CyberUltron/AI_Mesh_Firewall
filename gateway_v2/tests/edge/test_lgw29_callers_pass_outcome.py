# Validates: 6.3a(d) (every caller of the shared helpers passes the new required arguments)
"""AST tripwire over ``gateway_v2/edge``: no stream or output runs without a binding."""

from __future__ import annotations

import ast
from pathlib import Path

EDGE = Path(__file__).resolve().parents[2] / "gateway_v2" / "edge"
ALLOWED_LIFECYCLE_CALLERS = {("routes.py", "handle"), ("openai_responses.py", "handle")}


def _calls(name: str) -> list[tuple[str, str, ast.Call]]:
    found: list[tuple[str, str, ast.Call]] = []
    for path in sorted(EDGE.glob("*.py")):
        tree = ast.parse(path.read_text())
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for node in ast.walk(fn):
                if (
                    isinstance(node, ast.Call)
                    and getattr(node.func, "attr", getattr(node.func, "id", "")) == name
                ):
                    found.append((path.name, fn.name, node))
    return found


def _kw(call: ast.Call) -> set[str]:
    return {k.arg for k in call.keywords if k.arg}


def test_stream_response_and_collect_and_render_always_pass_outcome() -> None:
    for name in ("_stream_response", "collect_and_render"):
        calls = _calls(name)
        assert calls, name
        for file, fn, call in calls:
            assert "outcome" in _kw(call), (file, fn, name)


def test_collect_and_enforce_and_output_channels_pass_org_and_routed_model() -> None:
    enforce = _calls("collect_and_enforce")
    assert enforce and all({"org_id", "routed_model"} <= _kw(c) for _, _, c in enforce)
    channels = _calls("_collect_output_channels")
    assert channels and all(len(c.args) >= 6 for _, _, c in channels)


def test_run_lifecycle_has_exactly_the_two_known_callers() -> None:
    callers = {(file, fn) for file, fn, _ in _calls("run_lifecycle")}
    assert callers == ALLOWED_LIFECYCLE_CALLERS


def test_the_old_private_default_is_gone() -> None:
    sources = "".join(p.read_text() for p in EDGE.glob("*.py"))
    assert "_NoControlSource" not in sources.replace("RefusingControlSource", "")
