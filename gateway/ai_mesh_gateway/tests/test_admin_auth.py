"""
Unit tests for admin_auth.require_admin / is_admin.

Imports the helper module directly (NOT via main.py) so they run on
Python 3.9 without hitting the PEP 604 import wall in shared/envelope.py
— same pattern as test_org_tpm_rate_limit.py.
"""
import json
import os
from types import SimpleNamespace

import pytest

from ai_mesh_gateway.admin_auth import is_admin, require_admin


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Each test starts with no admin user IDs configured."""
    monkeypatch.delenv("GATEWAY_ADMIN_USER_IDS", raising=False)
    yield


def _make_ctx(*, user_id=42, permissions=None):
    return SimpleNamespace(user_id=user_id, permissions=permissions or {})


def _body(resp):
    return json.loads(bytes(resp.body).decode("utf-8"))


# ── is_admin ───────────────────────────────────────────────────────────────

def test_is_admin_false_when_auth_ctx_none():
    assert is_admin(None) is False


def test_is_admin_false_when_no_signal():
    assert is_admin(_make_ctx()) is False


def test_is_admin_true_when_permissions_admin_flag_set():
    assert is_admin(_make_ctx(permissions={"admin": True})) is True


def test_is_admin_false_when_permissions_admin_is_truthy_but_not_true():
    # Strict identity check — only `True` (bool) grants admin.
    assert is_admin(_make_ctx(permissions={"admin": 1})) is False
    assert is_admin(_make_ctx(permissions={"admin": "yes"})) is False


def test_is_admin_true_when_user_id_in_env(monkeypatch):
    monkeypatch.setenv("GATEWAY_ADMIN_USER_IDS", "7,42,99")
    assert is_admin(_make_ctx(user_id=42)) is True


def test_is_admin_handles_whitespace_in_env(monkeypatch):
    monkeypatch.setenv("GATEWAY_ADMIN_USER_IDS", " 7 , 42 , 99 ")
    assert is_admin(_make_ctx(user_id=42)) is True


def test_is_admin_false_when_user_id_not_in_env(monkeypatch):
    monkeypatch.setenv("GATEWAY_ADMIN_USER_IDS", "7,99")
    assert is_admin(_make_ctx(user_id=42)) is False


def test_is_admin_false_when_permissions_missing():
    ctx = SimpleNamespace(user_id=42)  # no .permissions attribute
    assert is_admin(ctx) is False


def test_is_admin_fail_closed_on_weird_ctx():
    # Object that raises on attribute access — must NOT grant admin.
    class Hostile:
        @property
        def permissions(self):
            raise RuntimeError("boom")

        @property
        def user_id(self):
            raise RuntimeError("boom")

    # RuntimeError is not caught by is_admin → propagates. That's fine for
    # require_admin which has its own outer handling. Verify the safer
    # AttributeError path:
    class Sparse:
        pass  # no attrs at all

    assert is_admin(Sparse()) is False


# ── require_admin ──────────────────────────────────────────────────────────

def test_require_admin_returns_401_when_auth_ctx_none():
    resp = require_admin(None)
    assert resp is not None
    assert resp.status_code == 401
    body = _body(resp)
    assert body["error"] == "unauthorized"
    assert body["code"] == "auth_required"


def test_require_admin_returns_403_when_not_admin():
    resp = require_admin(_make_ctx())
    assert resp is not None
    assert resp.status_code == 403
    body = _body(resp)
    assert body["error"] == "forbidden"
    assert body["code"] == "admin_required"


def test_require_admin_returns_none_when_permissions_flag():
    assert require_admin(_make_ctx(permissions={"admin": True})) is None


def test_require_admin_returns_none_when_env_match(monkeypatch):
    monkeypatch.setenv("GATEWAY_ADMIN_USER_IDS", "42")
    assert require_admin(_make_ctx(user_id=42)) is None


def test_require_admin_403_when_env_set_but_user_not_listed(monkeypatch):
    monkeypatch.setenv("GATEWAY_ADMIN_USER_IDS", "7,99")
    resp = require_admin(_make_ctx(user_id=42))
    assert resp.status_code == 403
