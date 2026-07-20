"""CHG-0144: the admin-RBAC server-to-server bypass (``_require_admin_role``) must compare
the ``GATEWAY_INTERNAL_API_KEY`` shared secret in CONSTANT TIME (hmac.compare_digest), not
with a plain ``==`` that short-circuits on the first differing byte and leaks the secret via
response timing. Locks the fix + guards against a regression back to ``==``.
"""
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from main import _require_admin_role

_INTERNAL_KEY = "s3cret-internal-key-0123456789abcdef"


def _req(header_val=None):
    headers = {}
    if header_val is not None:
        headers["x-gateway-internal-key"] = header_val
    return SimpleNamespace(headers=headers, state=SimpleNamespace())


def test_correct_internal_key_still_bypasses(monkeypatch):
    monkeypatch.setenv("GATEWAY_INTERNAL_API_KEY", _INTERNAL_KEY)
    # Exact match → server-to-server bypass granted (returns None, no RBAC error).
    assert _require_admin_role(_req(_INTERNAL_KEY)) is None


def test_wrong_internal_key_is_rejected(monkeypatch):
    monkeypatch.setenv("GATEWAY_INTERNAL_API_KEY", _INTERNAL_KEY)
    # Same length, one byte off → NOT a bypass; falls through to require_admin(None) which,
    # with no admin auth context, returns a JSONResponse (401/403), i.e. not None.
    wrong = _INTERNAL_KEY[:-1] + ("X" if _INTERNAL_KEY[-1] != "X" else "Y")
    result = _require_admin_role(_req(wrong))
    assert result is not None
    assert getattr(result, "status_code", None) in (401, 403)


def test_missing_header_does_not_bypass(monkeypatch):
    monkeypatch.setenv("GATEWAY_INTERNAL_API_KEY", _INTERNAL_KEY)
    result = _require_admin_role(_req(None))
    assert result is not None
    assert getattr(result, "status_code", None) in (401, 403)


def test_source_uses_constant_time_compare_not_plain_eq():
    """Source guard: the internal-key check must use hmac.compare_digest and must NOT
    fall back to a plain ``== internal_key`` (the timing side-channel this closes)."""
    src = (Path(__file__).resolve().parents[1] / "main.py").read_text()
    assert "hmac.compare_digest(header_key, internal_key)" in src
    # No plain-equality comparison of the internal key survives.
    assert not re.search(r"header_key\s*==\s*internal_key", src)
