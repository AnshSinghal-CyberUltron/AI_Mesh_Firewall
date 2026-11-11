"""LGW11-1 static half: omitting the authorization is a BUILD-TIME type error.

# Validates: Requirements 2.2, 2.3 (LGW11-1 static half)

This is the *static* complement to the runtime provenance assertion exercised by the
behavioral suite (``test_provider_dispatch_authorization_gw11.py``). The design (R2 /
LGW11-1) states that ``AuthorizedProviderEntry.dispatch`` takes the
:class:`~gateway_v2.domain.dispatch_authorization.DispatchAuthorization` as a REQUIRED
positional parameter so a call omitting it fails ``mypy --strict`` — the one guarantee a
runtime check cannot give, because the forged-auth path never gets a chance to run.

This harness is its OWN small mechanism, distinct from the behavioral tests: it compiles a
tiny snippet through the SAME ``mypy --strict`` the repo uses (preferring the project
venv's ``mypy`` binary, falling back to ``sys.executable -m mypy``, mirroring the
``lint-imports`` precedent in ``tests/gates/test_import_linter.py``) and asserts the
compile result, rather than asserting a Python runtime behaviour.

* NEGATIVE: a snippet that calls ``.dispatch(...)`` OMITTING ``auth`` must fail with a
  ``[call-arg]`` / "Missing positional argument "auth"" error and a non-zero exit (R2.2).
* POSITIVE control: the SAME snippet with ``auth`` supplied as the leading positional
  argument type-checks clean (exit 0). Every other argument is identical and cast to the
  declared type, so the only variable between the two snippets is the presence of ``auth``
  — proving it is the missing authorization that fails, not a broken fixture (R2.3).

The test skips cleanly when ``mypy`` is not importable so it never errors spuriously in a
stripped environment; in the project venv ``mypy`` IS available and the assertions run.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

# The gateway_v2 package root (…/gateway_v2) — the cwd mypy runs under so the snippet's
# ``from gateway_v2.dispatch.authorized import AuthorizedProviderEntry`` resolves exactly
# as the repo's own ``mypy --strict`` invocation resolves it (package at ./gateway_v2).
_PACKAGE_ROOT = Path(__file__).resolve().parents[2]

# A single shared snippet body parametrised by the one line under test: the ``.dispatch``
# call either omits ``auth`` (negative) or passes it as the leading positional (positive).
# Every other argument is identical and cast to the declared parameter type, so mypy's
# ONLY complaint in the negative case is the missing ``auth`` positional — not a type
# mismatch on some other argument. ``{dispatch_call}`` is substituted per case.
_SNIPPET = '''\
from __future__ import annotations

from typing import cast

from gateway_v2.dispatch.authorized import AuthorizedProviderEntry
from gateway_v2.dispatch.catalogue import ProviderCatalogue
from gateway_v2.dispatch.transform import GatewayRequest
from gateway_v2.domain import Decision, ExecutionPlan
from gateway_v2.domain.dispatch_authorization import DispatchAuthorization


def _call(entry: AuthorizedProviderEntry, auth: DispatchAuthorization) -> None:
    {dispatch_call}
'''

# NEGATIVE: the required leading positional ``auth`` is omitted. All keyword arguments are
# present and correctly typed, so the sole error mypy can raise is the missing ``auth``.
_NEGATIVE_CALL = """entry.dispatch(
        decision=cast("Decision", object()),
        request=cast("GatewayRequest", object()),
        plan=cast("ExecutionPlan", object()),
        catalogue=cast("ProviderCatalogue", object()),
        request_id="rid",
    )"""

# POSITIVE control: identical, with ``auth`` supplied as the leading positional argument.
_POSITIVE_CALL = """entry.dispatch(
        auth,
        decision=cast("Decision", object()),
        request=cast("GatewayRequest", object()),
        plan=cast("ExecutionPlan", object()),
        catalogue=cast("ProviderCatalogue", object()),
        request_id="rid",
    )"""


def _mypy_available() -> bool:
    """True iff ``mypy`` is importable in this environment (otherwise skip, don't error)."""
    return importlib.util.find_spec("mypy") is not None


def _run_mypy(snippet_path: Path) -> subprocess.CompletedProcess[str]:
    """Compile one snippet file through ``mypy --strict`` the way the repo runs it.

    Prefers the venv's ``mypy`` binary next to the running interpreter; falls back to
    ``sys.executable -m mypy`` (the exact precedent set by ``tests/gates/test_import_linter.py``
    for ``lint-imports``). Runs from the ``gateway_v2`` package root so the snippet's
    ``gateway_v2`` import resolves against the in-tree package, and threads that same root
    through ``MYPYPATH`` so a stripped ``PYTHONPATH`` cannot hide the package.
    """
    env = {**os.environ, "MYPYPATH": str(_PACKAGE_ROOT)}
    mypy_bin = Path(sys.prefix) / "bin" / "mypy"
    base = [str(mypy_bin)] if mypy_bin.is_file() else [sys.executable, "-m", "mypy"]
    return subprocess.run(
        [*base, "--strict", "--no-error-summary", str(snippet_path)],
        cwd=str(_PACKAGE_ROOT),
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def _write_snippet(tmp_path: Path, name: str, dispatch_call: str) -> Path:
    path = tmp_path / name
    path.write_text(_SNIPPET.format(dispatch_call=dispatch_call), encoding="utf-8")
    return path


@pytest.mark.skipif(not _mypy_available(), reason="mypy not available in this environment")
def test_lgw11_1_dispatch_without_auth_is_a_static_type_error(tmp_path: Path) -> None:
    """NEGATIVE (R2.2): omitting ``auth`` fails ``mypy --strict`` with a ``call-arg`` error.

    A dispatch that drops the authorization is not a runtime fault to be caught late — it
    is a BUILD-TIME type error, so a provider can never be reached without an
    authorization in correctly-typed code. mypy must exit non-zero and name both the
    ``call-arg`` category (or the "Missing positional argument" phrasing) AND the ``auth``
    parameter, so the failure is unambiguously the missing authorization.
    """
    snippet = _write_snippet(tmp_path, "neg_dispatch.py", _NEGATIVE_CALL)
    result = _run_mypy(snippet)
    dumped = (result.stdout + result.stderr).lower()

    assert result.returncode != 0, (
        "mypy --strict unexpectedly accepted a dispatch that omits the required `auth` "
        f"authorization:\n{result.stdout}{result.stderr}"
    )
    assert "call-arg" in dumped or "missing positional argument" in dumped, (
        f"expected a missing-positional-argument / call-arg error, got:\n"
        f"{result.stdout}{result.stderr}"
    )
    assert "auth" in dumped, (
        f"the type error must name the missing `auth` parameter, got:\n"
        f"{result.stdout}{result.stderr}"
    )


@pytest.mark.skipif(not _mypy_available(), reason="mypy not available in this environment")
def test_lgw11_1_dispatch_with_auth_type_checks_clean(tmp_path: Path) -> None:
    """POSITIVE control (R2.3): the SAME call WITH ``auth`` supplied type-checks clean.

    This is the control that proves the negative test is detecting the missing ``auth``
    and not merely a broken snippet: the only difference from the negative snippet is the
    leading ``auth`` positional argument. With it present, ``mypy --strict`` exits 0 and
    reports no error — so the negative case's non-zero exit is attributable solely to the
    absent authorization.
    """
    snippet = _write_snippet(tmp_path, "pos_dispatch.py", _POSITIVE_CALL)
    result = _run_mypy(snippet)

    assert result.returncode == 0, (
        "the type-correct dispatch (auth supplied) should pass mypy --strict cleanly, "
        f"so the negative case's failure is attributable only to the missing `auth`:\n"
        f"{result.stdout}{result.stderr}"
    )
    assert result.stdout.strip() == "", (
        f"expected no mypy errors on the positive control, got:\n{result.stdout}"
    )
