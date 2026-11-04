"""GW33 task 8.6 (L-5): the plan compiler's new import graph is deployable in the state-control image.

`plan/compiler.py` now imports `detect.output.*` and `runtime.resources_output`, and
`state_control/projector.py` reaches them through `plan.compiler`. The `ai-mesh-state-control`
image is built from `gateway_v2/Dockerfile`, which copies the WHOLE `gateway_v2/gateway_v2` package
and `state_control`. This test walks the transitive `gateway_v2` imports of `plan.compiler` with
`ast` (no execution), asserts every one resolves to a file inside the copied package, and that the
only non-`gateway_v2` imports are stdlib (the image installs nothing new for the compiler). The
in-image `python -c "import gateway_v2.plan.compiler"` smoke is a live-gate step (no Docker here).
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

_PKG_ROOT = Path(__file__).resolve().parents[2] / "gateway_v2"
_DOCKERFILE = Path(__file__).resolve().parents[2] / "Dockerfile"


def _module_file(module: str) -> Path | None:
    parts = module.split(".")[1:]
    base = _PKG_ROOT.joinpath(*parts)
    if (base / "__init__.py").is_file():
        return base / "__init__.py"
    if base.with_suffix(".py").is_file():
        return base.with_suffix(".py")
    return None


def _imports(path: Path) -> set[str]:
    out: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            out.add(node.module)
            out.update(f"{node.module}.{alias.name}" for alias in node.names)
        elif isinstance(node, ast.Import):
            out.update(alias.name for alias in node.names)
    return out


def _closure(start: str) -> tuple[set[str], set[str]]:
    seen: set[str] = set()
    external: set[str] = set()
    stack = [start]
    while stack:
        module = stack.pop()
        if module in seen:
            continue
        seen.add(module)
        path = _module_file(module)
        if path is None:
            continue
        for name in _imports(path):
            if name.split(".")[0] == "gateway_v2":
                if _module_file(name) is not None:
                    stack.append(name)
            else:
                external.add(name.split(".")[0])
    return seen, external


def test_the_compiler_import_closure_lives_inside_the_copied_package() -> None:
    seen, external = _closure("gateway_v2.plan.compiler")
    assert "gateway_v2.detect.output.exfil" in seen
    assert "gateway_v2.runtime.resources_output" in seen
    for module in seen:
        assert _module_file(module) is not None, module
    stdlib = set(sys.stdlib_module_names)
    assert external <= stdlib, sorted(external - stdlib)


def test_the_state_control_image_copies_the_whole_gateway_package() -> None:
    text = _DOCKERFILE.read_text(encoding="utf-8")
    assert "source=gateway_v2/gateway_v2,target=/tmp/src/pkg" in text
    assert "cp -R /tmp/src/pkg/. /opt/gateway_v2/gateway_v2/" in text
    assert "source=gateway_v2/state_control,target=/tmp/src/control" in text
