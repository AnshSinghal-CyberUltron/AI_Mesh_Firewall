"""LGW00-5: HTTPException / JSONResponse / status_code=4xx only in edge/ and resolve/."""

from __future__ import annotations

from pathlib import Path

from lint.check_http_outside_edge_resolve import scan_tree


def _write(root: Path, rel: str, text: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


HTTP_SNIPPET = '''"""bad."""
from fastapi import HTTPException

def boom() -> None:
    raise HTTPException(status_code=403)
'''


def test_http_exception_in_detect_fails_naming_file(tmp_path: Path) -> None:
    _write(tmp_path, "detect/poison.py", HTTP_SNIPPET)
    hits = scan_tree(tmp_path)
    assert hits, "expected AST gate to fire"
    assert any("detect/poison.py" in h.path or h.path.endswith("poison.py") for h in hits)
    assert any("HTTPException" in h.symbol for h in hits)


def test_json_response_in_admit_fails(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "admit/out.py",
        "from starlette.responses import JSONResponse\n\ndef f():\n    return JSONResponse({})\n",
    )
    hits = scan_tree(tmp_path)
    assert any("JSONResponse" in h.symbol for h in hits)


def test_status_code_4xx_in_plan_fails(tmp_path: Path) -> None:
    _write(tmp_path, "plan/x.py", "def f():\n    return {'status_code': 429}\n")
    # Keyword form is the runbook AST target.
    _write(tmp_path, "plan/kw.py", "def f():\n    return dict(status_code=401)\n")
    hits = scan_tree(tmp_path)
    assert any(h.path.endswith("kw.py") for h in hits)


def test_http_in_edge_and_resolve_is_allowed(tmp_path: Path) -> None:
    _write(tmp_path, "edge/errors.py", HTTP_SNIPPET)
    _write(tmp_path, "resolve/decision.py", HTTP_SNIPPET)
    assert scan_tree(tmp_path) == []
