"""Tests for /metrics scraper authentication."""
from __future__ import annotations

import importlib
import os

import pytest


@pytest.fixture()
def metrics_auth_module():
    import ai_mesh_gateway.metrics_auth as m

    importlib.reload(m)
    return m


def test_verify_metrics_scraper_with_header(metrics_auth_module, monkeypatch):
    monkeypatch.setenv("METRICS_SCRAPER_KEY", "secret-scraper-key")
    assert metrics_auth_module.verify_metrics_scraper(
        {"x-metrics-scraper-key": "secret-scraper-key"}
    )


def test_verify_metrics_scraper_with_bearer(metrics_auth_module, monkeypatch):
    monkeypatch.setenv("METRICS_SCRAPER_KEY", "secret-scraper-key")
    assert metrics_auth_module.verify_metrics_scraper(
        {"authorization": "Bearer secret-scraper-key"}
    )


def test_verify_metrics_scraper_rejects_wrong_key(metrics_auth_module, monkeypatch):
    monkeypatch.setenv("METRICS_SCRAPER_KEY", "secret-scraper-key")
    assert not metrics_auth_module.verify_metrics_scraper(
        {"x-metrics-scraper-key": "wrong"}
    )


def test_verify_metrics_scraper_fail_closed_without_key(metrics_auth_module, monkeypatch):
    monkeypatch.delenv("METRICS_SCRAPER_KEY", raising=False)
    monkeypatch.setenv("METRICS_ALLOW_OPEN", "false")
    assert not metrics_auth_module.verify_metrics_scraper({})


def test_verify_metrics_scraper_allow_open_dev_only(metrics_auth_module, monkeypatch):
    monkeypatch.delenv("METRICS_SCRAPER_KEY", raising=False)
    monkeypatch.setenv("METRICS_ALLOW_OPEN", "true")
    assert metrics_auth_module.verify_metrics_scraper({})
