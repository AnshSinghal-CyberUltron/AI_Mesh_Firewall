"""Tests for EmbeddingVault failure handling (M-16).

Covers the auth-vs-transient error classification, the once-per-cooldown
ERROR logging, the error counters, and the EMBEDDING_VAULT_STRICT
fail-closed mode (defaulting to the historical fail-open behavior).
"""
from __future__ import annotations

import logging

import pytest

import embedding_vault
from embedding_vault import EmbeddingVault, VaultVerdict, _is_auth_error

VAULT_LOGGER = "gateway.embedding_vault"


class FakeAuthStatusError(Exception):
    """Mimics litellm/openai errors that carry an HTTP status code."""

    status_code = 401


class FakeForbiddenError(Exception):
    status_code = 403


class AuthenticationError(Exception):
    """Classified by exception class name."""


@pytest.fixture()
def vault(monkeypatch):
    """An enabled vault whose schema setup is bypassed (no real Postgres)."""
    monkeypatch.delenv("EMBEDDING_VAULT_STRICT", raising=False)
    v = EmbeddingVault(pg_dsn="postgresql://fake:fake@127.0.0.1:1/fake")
    v._initialized = True  # skip _ensure_schema (would need a live DB)
    return v


@pytest.fixture()
def counter_snapshot():
    """Snapshot module-local counters so tests assert deltas, not absolutes."""
    return dict(embedding_vault.VAULT_ERROR_COUNTERS)


def _counter_delta(snapshot: dict, kind: str) -> int:
    return embedding_vault.VAULT_ERROR_COUNTERS.get(kind, 0) - snapshot.get(kind, 0)


def _error_records(caplog):
    return [
        r
        for r in caplog.records
        if r.name == VAULT_LOGGER and r.levelno == logging.ERROR
    ]


def _warning_records(caplog):
    return [
        r
        for r in caplog.records
        if r.name == VAULT_LOGGER and r.levelno == logging.WARNING
    ]


# ── classification ──────────────────────────────────────────────────────────


def test_is_auth_error_by_status_code():
    assert _is_auth_error(FakeAuthStatusError("nope"))
    assert _is_auth_error(FakeForbiddenError("nope"))


def test_is_auth_error_by_class_name():
    assert _is_auth_error(AuthenticationError("something went wrong"))


def test_is_auth_error_by_message():
    assert _is_auth_error(Exception("FATAL: password authentication failed for user"))
    assert _is_auth_error(Exception("Invalid API key provided"))
    assert _is_auth_error(Exception("server returned 403 for request"))


def test_transient_errors_not_classified_as_auth():
    assert not _is_auth_error(ConnectionError("connection timed out"))
    assert not _is_auth_error(Exception("upstream returned 503 service unavailable"))
    assert not _is_auth_error(TimeoutError("read timeout after 5s"))


# ── transient errors: fail open + WARNING ───────────────────────────────────


def test_transient_error_allows_with_warning(vault, monkeypatch, caplog, counter_snapshot):
    monkeypatch.setattr(
        vault, "_embed", lambda text: (_ for _ in ()).throw(ConnectionError("connection timed out"))
    )
    with caplog.at_level(logging.DEBUG, logger=VAULT_LOGGER):
        verdict = vault.check_sync("hello world")

    assert isinstance(verdict, VaultVerdict)
    assert verdict.is_match is False  # fail-open allow
    assert "timed out" in verdict.error
    assert len(_warning_records(caplog)) == 1
    assert len(_error_records(caplog)) == 0
    assert _counter_delta(counter_snapshot, "transient") == 1
    assert _counter_delta(counter_snapshot, "auth") == 0


# ── auth errors: fail open by default + ERROR + counter ─────────────────────


def test_auth_error_allows_logs_error_and_counts(vault, monkeypatch, caplog, counter_snapshot):
    monkeypatch.setattr(
        vault, "_embed", lambda text: (_ for _ in ()).throw(FakeAuthStatusError("invalid api key"))
    )
    with caplog.at_level(logging.DEBUG, logger=VAULT_LOGGER):
        verdict = vault.check_sync("hello world")

    assert verdict.is_match is False  # default remains fail-open
    assert "invalid api key" in verdict.error
    errors = _error_records(caplog)
    assert len(errors) == 1
    assert "auth/config failure" in errors[0].getMessage()
    assert _counter_delta(counter_snapshot, "auth") == 1
    assert _counter_delta(counter_snapshot, "transient") == 0


def test_auth_error_counter_visible_on_metrics_surface(vault, monkeypatch):
    metrics = pytest.importorskip("metrics")
    if not getattr(metrics, "_PROM_AVAILABLE", False):
        pytest.skip("prometheus_client not available")
    monkeypatch.setattr(
        vault, "_embed", lambda text: (_ for _ in ()).throw(FakeAuthStatusError("invalid api key"))
    )
    vault.check_sync("hello world")

    body, _ = metrics.render_latest()
    text = body.decode("utf-8")
    assert "amf_gateway_embedding_vault_errors_total" in text
    assert 'kind="auth"' in text


def test_auth_error_logs_error_once_per_cooldown(vault, monkeypatch, caplog):
    vault._auth_log_cooldown_s = 300.0
    monkeypatch.setattr(
        vault, "_embed", lambda text: (_ for _ in ()).throw(FakeAuthStatusError("invalid api key"))
    )
    with caplog.at_level(logging.DEBUG, logger=VAULT_LOGGER):
        vault.check_sync("first request")
        vault.check_sync("second request")
        vault.check_sync("third request")

    assert len(_error_records(caplog)) == 1  # suppressed within cooldown
    debug_suppressed = [
        r
        for r in caplog.records
        if r.name == VAULT_LOGGER
        and r.levelno == logging.DEBUG
        and "suppressed" in r.getMessage()
    ]
    assert len(debug_suppressed) == 2


def test_auth_error_logs_again_after_cooldown_expires(vault, monkeypatch, caplog):
    vault._auth_log_cooldown_s = 0.0  # cooldown immediately expires
    monkeypatch.setattr(
        vault, "_embed", lambda text: (_ for _ in ()).throw(FakeAuthStatusError("invalid api key"))
    )
    with caplog.at_level(logging.DEBUG, logger=VAULT_LOGGER):
        vault.check_sync("first request")
        vault.check_sync("second request")

    assert len(_error_records(caplog)) == 2


# ── strict mode: fail closed on auth failures only ──────────────────────────


def test_strict_mode_denies_on_auth_failure(vault, monkeypatch, counter_snapshot):
    monkeypatch.setenv("EMBEDDING_VAULT_STRICT", "true")
    monkeypatch.setattr(
        vault, "_embed", lambda text: (_ for _ in ()).throw(FakeAuthStatusError("invalid api key"))
    )
    verdict = vault.check_sync("hello world")

    assert verdict.is_match is True  # fail-closed deny
    assert verdict.confidence == 1.0
    assert verdict.matches[0].attack_id == "vault-auth-unavailable"
    assert verdict.matches[0].attack_type == "vault_unavailable"
    assert "strict mode" in verdict.error
    assert _counter_delta(counter_snapshot, "auth") == 1


def test_strict_mode_transient_error_still_allows(vault, monkeypatch, counter_snapshot):
    monkeypatch.setenv("EMBEDDING_VAULT_STRICT", "1")
    monkeypatch.setattr(
        vault, "_embed", lambda text: (_ for _ in ()).throw(TimeoutError("read timeout"))
    )
    verdict = vault.check_sync("hello world")

    assert verdict.is_match is False  # transient stays fail-open even in strict
    assert _counter_delta(counter_snapshot, "transient") == 1


def test_strict_mode_off_by_default(vault, monkeypatch):
    monkeypatch.delenv("EMBEDDING_VAULT_STRICT", raising=False)
    monkeypatch.setattr(
        vault, "_embed", lambda text: (_ for _ in ()).throw(FakeForbiddenError("forbidden"))
    )
    verdict = vault.check_sync("hello world")
    assert verdict.is_match is False  # unchanged default behavior


def test_strict_mode_falsy_values_fail_open(vault, monkeypatch):
    for value in ("0", "false", "no", "", "off"):
        monkeypatch.setenv("EMBEDDING_VAULT_STRICT", value)
        monkeypatch.setattr(
            vault, "_embed", lambda text: (_ for _ in ()).throw(FakeAuthStatusError("nope"))
        )
        verdict = vault.check_sync("hello world")
        assert verdict.is_match is False, f"value={value!r} should fail open"


# ── async path mirrors sync semantics ────────────────────────────────────────


@pytest.mark.asyncio
async def test_async_check_strict_mode_denies(vault, monkeypatch):
    monkeypatch.setenv("EMBEDDING_VAULT_STRICT", "true")
    monkeypatch.setattr(
        vault, "_embed", lambda text: (_ for _ in ()).throw(FakeAuthStatusError("invalid api key"))
    )
    verdict = await vault.check("hello world")
    assert verdict.is_match is True
    assert verdict.matches[0].attack_id == "vault-auth-unavailable"
