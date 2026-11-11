"""GW11 C8: DispatchAuthorization minting/forgery only in resolve/ (R10.1, R10.2).

Forgery-matrix unit test for ``lint/check_dispatch_provenance.py`` (Task 9),
co-located with the other gate tests (``test_secret_headers.py``,
``test_check_http.py``). One dirty fixture per enumerated forgery form asserts
>=1 hit, achieving the 17/17 forgery-form coverage the design's C8 table names.

Allow-set interaction (verified against the gate source): ``scan_file`` parses +
matches with NO allow filtering, while ``scan_tree`` applies ``_is_allowed`` which
excludes anything under a ``resolve`` top package, the defining domain module, OR
any path with a ``tests`` directory part (``ALLOWED_DIR_PARTS={"tests"}``). A
dirty fixture written under ``tmp_path`` (whose leaf is a ``test_...`` dir — NOT a
bare ``tests`` part) is therefore driven through ``scan_file`` directly for the
per-form assertions; ``scan_tree`` is used only for the clean-tree / allow-set /
real-repo behavior tests.
"""

from __future__ import annotations

from pathlib import Path

import lint.check_dispatch_provenance as _gate_mod
from lint.check_dispatch_provenance import main, scan_file, scan_tree

# ---------------------------------------------------------------------------
# Dirty forgery fixtures (one snippet per form from the C8 table). These
# intentionally reference DispatchAuthorization / _mint / _witness — they are
# test fixture strings, never executed, and are written to a tmp_path file whose
# path contains no ``resolve``/``tests`` segment and is not the defining domain
# module, so scan_file (no allow filtering) matches them.
# ---------------------------------------------------------------------------

_IMPORT_AUTH = "from gateway_v2.domain.dispatch_authorization import DispatchAuthorization\n"
_IMPORT_MINT = "from gateway_v2.domain.dispatch_authorization import _mint\n"
# ``auth = _mint(...)`` makes ``auth`` an auth-bearing local so forms 3/6/7 fire.
_SETUP_AUTH = _IMPORT_MINT + "auth = _mint('plan-1', 'ALLOW')\n"


# The naive kwargs a forger would pass (missing the module-private witness).
_CTOR_KWARGS = "plan_version='p', disposition='d', _witness=None"

# Form 1 — direct construction DispatchAuthorization(...).
FORM_DIRECT = _IMPORT_AUTH + f"x = DispatchAuthorization({_CTOR_KWARGS})\n"

# Form 2a — _mint(...) call.
FORM_MINT = _IMPORT_MINT + "x = _mint('plan-1', 'ALLOW')\n"

# Form 2b — aliased mint: from ... import _mint as m; m(...).
FORM_MINT_ALIAS = (
    "from gateway_v2.domain.dispatch_authorization import _mint as m\n"
    "x = m('plan-1', 'ALLOW')\n"
)

# Form 3 — dataclasses.replace(auth, ...).
FORM_REPLACE = (
    "import dataclasses\n" + _SETUP_AUTH + "y = dataclasses.replace(auth, disposition='BLOCK')\n"
)

# Form 4 — object.__new__(DispatchAuthorization).
FORM_NEW = _IMPORT_AUTH + "z = object.__new__(DispatchAuthorization)\n"

# Form 5a — object.__setattr__(x, "_witness", w).
FORM_SETATTR = "obj = object()\nw = object()\nobject.__setattr__(obj, '_witness', w)\n"

# Form 5b — attribute assign x._witness = w.
FORM_ATTR_ASSIGN = "class C:\n    pass\n\nc = C()\nc._witness = object()\n"

# Form 6a — copy.copy(auth).
FORM_COPY = "import copy\n" + _SETUP_AUTH + "d = copy.copy(auth)\n"

# Form 6b — copy.deepcopy(auth).
FORM_DEEPCOPY = "import copy\n" + _SETUP_AUTH + "d = copy.deepcopy(auth)\n"

# Form 7 — type(auth)(...) reconstruction.
FORM_TYPE_RECON = _SETUP_AUTH + f"r = type(auth)({_CTOR_KWARGS})\n"

# Form 8a — import-alias evasion on the type: from ... import DispatchAuthorization as X.
FORM_ALIAS_X = (
    "from gateway_v2.domain.dispatch_authorization import DispatchAuthorization as X\n"
    f"x = X({_CTOR_KWARGS})\n"
)

# Form 8b — bare module import alias ``as Y`` then Y.DispatchAuthorization(...).
FORM_ALIAS_Y = (
    "import gateway_v2.domain.dispatch_authorization as Y\n"
    f"y = Y.DispatchAuthorization({_CTOR_KWARGS})\n"
)

# A clean fixture: only benign code, no forgery form.
FORM_CLEAN = (
    '"""benign module."""\n\n\n'
    "def add(a: int, b: int) -> int:\n    return a + b\n\n\n"
    "VALUE = add(1, 2)\n"
)


# Each entry: (test id, snippet, substring expected in >=1 hit's form label).
_DIRTY_FORMS: tuple[tuple[str, str, str], ...] = (
    ("form1-direct-construction", FORM_DIRECT, "DispatchAuthorization construction"),
    ("form2a-mint-call", FORM_MINT, "_mint call"),
    ("form2b-mint-alias", FORM_MINT_ALIAS, "_mint call"),
    ("form3-dataclasses-replace", FORM_REPLACE, "replace() forgery"),
    ("form4-object-new", FORM_NEW, "__new__() forgery"),
    ("form5a-object-setattr-witness", FORM_SETATTR, "object.__setattr__ of _witness"),
    ("form5b-attribute-assign-witness", FORM_ATTR_ASSIGN, "attribute assign of _witness"),
    ("form6a-copy-copy", FORM_COPY, "copy() forgery"),
    ("form6b-copy-deepcopy", FORM_DEEPCOPY, "deepcopy() forgery"),
    ("form7-type-reconstruction", FORM_TYPE_RECON, "type(auth)(...) reconstruction"),
    ("form8a-import-alias-type", FORM_ALIAS_X, "DispatchAuthorization construction"),
    ("form8b-module-alias", FORM_ALIAS_Y, "DispatchAuthorization construction"),
)


def _write_fixture(tmp_path: Path, name: str, snippet: str) -> Path:
    """Write a snippet to a tmp file under a ``dispatch`` subdir (no resolve/tests part)."""
    path = tmp_path / "dispatch" / f"{name}.py"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(snippet, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Per-form matrix: scan_file yields >=1 hit for every dirty forgery form.
# Driven through scan_file (not scan_tree) because the fixture lives under
# tmp_path and scan_file applies no allow filtering (verified in the gate).
# ---------------------------------------------------------------------------


def test_every_forgery_form_is_a_hit(tmp_path: Path) -> None:
    """One dirty fixture per enumerated forgery form -> >=1 hit, 17/17 coverage.
    # Feature: provider-dispatch-authorization-gw11, Property 18
    # Validates: Requirements 10.2"""
    for name, snippet, expected_form in _DIRTY_FORMS:
        fixture = _write_fixture(tmp_path, name, snippet)
        hits = scan_file(fixture)
        assert hits != [], f"{name}: expected >=1 forgery hit, got none"
        assert any(expected_form in form for form, _line in hits), (
            f"{name}: expected a {expected_form!r} hit, got {hits}"
        )


# ---------------------------------------------------------------------------
# Property 18 — the gate flags every forgery outside resolve/ (above) AND
# passes a clean tree.
# ---------------------------------------------------------------------------


def test_clean_fixture_yields_zero_hits(tmp_path: Path) -> None:
    """A fixture with only benign code -> scan_file returns []. Property 18.
    # Feature: provider-dispatch-authorization-gw11, Property 18
    # Validates: Requirements 10.1, 10.2"""
    fixture = _write_fixture(tmp_path, "clean", FORM_CLEAN)
    assert scan_file(fixture) == []


def test_scan_tree_passes_a_clean_tree(tmp_path: Path) -> None:
    """scan_tree over a tree with only benign code is clean. Property 18.
    # Feature: provider-dispatch-authorization-gw11, Property 18
    # Validates: Requirements 10.1, 10.2"""
    _write_fixture(tmp_path, "clean", FORM_CLEAN)
    assert scan_tree(tmp_path) == []


def test_scan_tree_excludes_tests_dir_part(tmp_path: Path) -> None:
    """A forgery under a ``tests`` directory part is excluded by the allow-set
    (ALLOWED_DIR_PARTS={"tests"}); scan_tree therefore does not flag it, while
    scan_file on the same file still sees the hit. Property 18 (allow-set).
    # Feature: provider-dispatch-authorization-gw11, Property 18
    # Validates: Requirements 10.1, 10.2"""
    under_tests = tmp_path / "tests" / "fixture_forgery.py"
    under_tests.parent.mkdir(parents=True, exist_ok=True)
    under_tests.write_text(FORM_DIRECT, encoding="utf-8")
    # scan_tree honors the allow-set -> no tree-wide hit.
    assert scan_tree(tmp_path) == []
    # but scan_file applies no allow filtering -> the fixture itself is a hit.
    assert scan_file(under_tests) != []


def test_scan_tree_excludes_resolve_top_package(tmp_path: Path) -> None:
    """The single legal mint site (resolve/) is allowed even with a direct mint.
    # Feature: provider-dispatch-authorization-gw11, Property 18
    # Validates: Requirements 10.1, 10.2"""
    legal = tmp_path / "resolve" / "authorize.py"
    legal.parent.mkdir(parents=True, exist_ok=True)
    legal.write_text(FORM_MINT, encoding="utf-8")
    assert scan_tree(tmp_path) == []


def test_scan_tree_flags_forgery_outside_resolve(tmp_path: Path) -> None:
    """A forgery under a non-allowed top package IS flagged by scan_tree.
    # Feature: provider-dispatch-authorization-gw11, Property 18
    # Validates: Requirements 10.1, 10.2"""
    _write_fixture(tmp_path, "bad", FORM_DIRECT)
    hits = scan_tree(tmp_path)
    assert hits, "expected scan_tree to flag the forgery outside resolve/"
    assert any("bad.py" in h.path for h in hits)
    assert any("DispatchAuthorization construction" in h.form for h in hits)


# ---------------------------------------------------------------------------
# main() exit-code contract: 0 on a clean dir, 1 on a dirty dir. Both dirs are
# built under a ``dispatch`` subdir so no ``tests``/``resolve`` segment leaks in.
# ---------------------------------------------------------------------------


def test_main_returns_zero_on_clean_dir(tmp_path: Path) -> None:
    """main(["<clean-dir>"]) == 0. Property 18 (gate exit).
    # Feature: provider-dispatch-authorization-gw11, Property 18
    # Validates: Requirements 10.1, 10.2"""
    clean_dir = tmp_path / "clean_tree"
    (clean_dir / "dispatch").mkdir(parents=True)
    (clean_dir / "dispatch" / "ok.py").write_text(FORM_CLEAN, encoding="utf-8")
    assert main([str(clean_dir)]) == 0


def test_main_returns_one_on_dirty_dir(tmp_path: Path) -> None:
    """main(["<dirty-dir>"]) == 1. Property 18 (gate exit).
    # Feature: provider-dispatch-authorization-gw11, Property 18
    # Validates: Requirements 10.1, 10.2"""
    dirty_dir = tmp_path / "dirty_tree"
    (dirty_dir / "dispatch").mkdir(parents=True)
    (dirty_dir / "dispatch" / "bad.py").write_text(FORM_DIRECT, encoding="utf-8")
    assert main([str(dirty_dir)]) == 1


# ---------------------------------------------------------------------------
# Real-tree allow-set: the shipped gateway_v2 tree exits clean under scan_tree
# (R10.1). This is the exact tree the CI structural-gates job scans
# (``python -m lint.check_dispatch_provenance gateway_v2``), and the gate's own
# ``main()`` default; the gate's allow-set top packages (resolve, domain/...)
# are gateway_v2-relative.
# ---------------------------------------------------------------------------


def _is_gated_root(path: Path) -> bool:
    """The gate's allow-set anchors: a ``resolve/`` top package and the defining
    ``domain/dispatch_authorization.py`` module both live directly under it."""
    anchor = path / "domain" / "dispatch_authorization.py"
    return (path / "resolve").is_dir() and anchor.exists()


def _gated_source_root() -> Path | None:
    """Locate the inner source tree the gate actually scans.

    It is the directory whose children are the gateway_v2-relative top packages
    (``resolve/`` — the single legal mint top package — and the defining
    ``domain/dispatch_authorization.py`` module, exactly the allow-set entries).
    This is the tree the CI structural-gates job targets with
    ``python -m lint.check_dispatch_provenance gateway_v2`` (cwd gateway_v2) and
    the gate's own ``main()`` default. The gate module lives in a sibling
    ``lint/`` package, so we search from its location: the inner ``gateway_v2``
    source package next to ``lint/``, falling back to a parents walk.
    """
    base = Path(_gate_mod.__file__).resolve().parent.parent  # the outer gateway_v2 (cwd)
    candidate = base / "gateway_v2"
    if _is_gated_root(candidate):
        return candidate
    if _is_gated_root(base):
        return base
    for parent in Path(__file__).resolve().parents:
        if _is_gated_root(parent):
            return parent
    return None


def test_real_gated_tree_is_clean() -> None:
    """scan_tree over the real gated source tree yields zero hits — the allow-set
    exempts resolve/ (mint site) and domain/dispatch_authorization.py (defining
    module). This is the exact tree the CI structural-gates job scans. Runs only
    when the tree is locatable (CI is the authoritative fallback). Property 18.
    # Feature: provider-dispatch-authorization-gw11, Property 18
    # Validates: Requirements 10.1, 10.2"""
    tree = _gated_source_root()
    if tree is None:
        import pytest

        pytest.skip("gated source tree not locatable; CI structural-gates job is authoritative")
    assert scan_tree(tree) == []
