"""Broker /health — Docker connectivity probe for deployers."""

from __future__ import annotations

from unittest.mock import patch

from fastapi.testclient import TestClient

import main


def test_health_includes_docker_ok_true_when_ping_succeeds():
    with patch.object(main.docker_manager, "ping", return_value=True):
        with TestClient(main.app) as client:
            resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["service"] == "mcp-broker"
    assert body["docker_ok"] is True


def test_health_includes_docker_ok_false_when_ping_fails():
    with patch.object(main.docker_manager, "ping", return_value=False):
        with TestClient(main.app) as client:
            resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["docker_ok"] is False


def test_sandbox_router_mounted_on_app():
    with TestClient(main.app) as client:
        resp = client.get("/v1/sandbox/probe-org/status")
    # Protected route — 401 proves router is wired (not 404).
    assert resp.status_code == 401
