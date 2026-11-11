"""GW17b: credential-flow AST gate, with negative controls."""

from __future__ import annotations

from pathlib import Path

from lint.check_credential_flow import scan_source, scan_tree

_PKG = Path(__file__).resolve().parents[2] / "gateway_v2"


def test_shipped_credential_modules_are_clean() -> None:
    assert scan_tree(_PKG) == []


def test_negative_control_a_credential_log_line_is_red() -> None:
    src = (
        "import logging\nLOG = logging.getLogger('x')\n"
        "def f(token):\n    LOG.warning('got %s', token)\n"
    )
    hits = scan_source(src)
    assert hits and "token" in hits[0].detail


def test_negative_control_f_string_exception_and_print_are_red() -> None:
    assert scan_source("def f(secret):\n    raise ValueError(f'bad {secret}')\n")
    assert scan_source("def f(headers):\n    print(headers)\n")


def test_clean_code_passes() -> None:
    assert scan_source("def f(slug):\n    raise ValueError('fixed text')\n") == []


def test_scan_tree_only_reads_credential_modules(tmp_path: Path) -> None:
    (tmp_path / "other.py").write_text("print(token)\n")
    (tmp_path / "mcp_credentials.py").write_text("print(token)\n")
    hits = scan_tree(tmp_path)
    assert [Path(h.path).name for h in hits] == ["mcp_credentials.py"]
