"""
Phase 1 §1.1 — unit tests for the per-org TPM rate-limit helper.

Tests target ``rate_limit_enforcement.enforce_org_tpm_rate_limit`` directly
to avoid importing main.py (which transitively pulls Py3.10+ syntax).
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from ai_mesh_gateway.rate_limit_enforcement import enforce_org_tpm_rate_limit


@pytest.fixture
def auth_ctx():
    return SimpleNamespace(
        org_slug="acme", user_id=42, project_id=7, prefix="ak_test",
    )


@pytest.fixture
def metrics():
    return {"blocked": 0}


@pytest.fixture
def emit():
    return MagicMock()


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def test_noop_when_limiter_unset(auth_ctx, metrics, emit):
    res = _run(enforce_org_tpm_rate_limit(
        auth_ctx, rate_limiter=None, config_sync=MagicMock(),
        metrics=metrics, emit_telemetry=emit, event_type="x",
    ))
    assert res is None and metrics["blocked"] == 0 and not emit.called


def test_noop_when_auth_ctx_none(metrics, emit):
    res = _run(enforce_org_tpm_rate_limit(
        None, rate_limiter=MagicMock(), config_sync=MagicMock(),
        metrics=metrics, emit_telemetry=emit, event_type="x",
    ))
    assert res is None


def test_noop_when_org_slug_empty(metrics, emit):
    ctx = SimpleNamespace(org_slug="")
    res = _run(enforce_org_tpm_rate_limit(
        ctx, rate_limiter=MagicMock(), config_sync=MagicMock(),
        metrics=metrics, emit_telemetry=emit, event_type="x",
    ))
    assert res is None


def test_noop_when_org_limit_zero(auth_ctx, metrics, emit):
    cfg = MagicMock(); cfg.get_config.return_value = {"org_tpm_limit": 0}
    limiter = MagicMock(); limiter.check_org_rate_limit = AsyncMock()
    res = _run(enforce_org_tpm_rate_limit(
        auth_ctx, rate_limiter=limiter, config_sync=cfg,
        metrics=metrics, emit_telemetry=emit, event_type="x",
    ))
    assert res is None
    limiter.check_org_rate_limit.assert_not_awaited()


def test_noop_when_config_lookup_fails(auth_ctx, metrics, emit):
    cfg = MagicMock(); cfg.get_config.side_effect = RuntimeError("redis")
    limiter = MagicMock(); limiter.check_org_rate_limit = AsyncMock()
    res = _run(enforce_org_tpm_rate_limit(
        auth_ctx, rate_limiter=limiter, config_sync=cfg,
        metrics=metrics, emit_telemetry=emit, event_type="x",
    ))
    # cfg failure -> treated as no limit configured -> pass through
    assert res is None


def test_allows_under_ceiling(auth_ctx, metrics, emit):
    cfg = MagicMock(); cfg.get_config.return_value = {"org_tpm_limit": 10_000}
    limiter = MagicMock()
    limiter.check_org_rate_limit = AsyncMock(return_value=(True, 500))
    res = _run(enforce_org_tpm_rate_limit(
        auth_ctx, rate_limiter=limiter, config_sync=cfg,
        metrics=metrics, emit_telemetry=emit, event_type="x",
        estimated_tokens=42,
    ))
    assert res is None and metrics["blocked"] == 0
    limiter.check_org_rate_limit.assert_awaited_once_with("acme", 10_000, 42)


def test_blocks_over_ceiling_with_429(auth_ctx, metrics, emit):
    cfg = MagicMock(); cfg.get_config.return_value = {"org_tpm_limit": 10_000}
    limiter = MagicMock()
    limiter.check_org_rate_limit = AsyncMock(return_value=(False, 10_500))
    res = _run(enforce_org_tpm_rate_limit(
        auth_ctx, rate_limiter=limiter, config_sync=cfg,
        metrics=metrics, emit_telemetry=emit,
        event_type="embedding_blocked", model="text-embedding-3-small",
    ))
    assert res is not None
    assert res.status_code == 429
    assert res.headers.get("Retry-After") == "60"
    assert metrics["blocked"] == 1
    emit.assert_called_once()
    kwargs = emit.call_args.kwargs
    assert kwargs["event_type"] == "embedding_blocked"
    assert kwargs["action"] == "block"
    assert kwargs["threat_type"] == "rate_limit_org_tpm"
    assert kwargs["metadata"]["org_slug"] == "acme"
    assert kwargs["metadata"]["module"] == "1.1"


def test_fails_closed_on_limiter_exception(auth_ctx, metrics, emit):
    cfg = MagicMock(); cfg.get_config.return_value = {"org_tpm_limit": 10_000}
    limiter = MagicMock()
    limiter.check_org_rate_limit = AsyncMock(side_effect=RuntimeError("redis down"))
    res = _run(enforce_org_tpm_rate_limit(
        auth_ctx, rate_limiter=limiter, config_sync=cfg,
        metrics=metrics, emit_telemetry=emit, event_type="x",
    ))
    # FAIL-CLOSED: limiter error -> 429, not pass-through
    assert res is not None
    assert res.status_code == 429
    assert metrics["blocked"] == 1


def test_telemetry_exception_does_not_break_response(auth_ctx, metrics):
    cfg = MagicMock(); cfg.get_config.return_value = {"org_tpm_limit": 100}
    limiter = MagicMock()
    limiter.check_org_rate_limit = AsyncMock(return_value=(False, 200))
    emit = MagicMock(side_effect=RuntimeError("telemetry down"))
    res = _run(enforce_org_tpm_rate_limit(
        auth_ctx, rate_limiter=limiter, config_sync=cfg,
        metrics=metrics, emit_telemetry=emit, event_type="x",
    ))
    assert res is not None and res.status_code == 429
