"""CHG-0111: the broker key (X-MCP-Broker-Key) is a SHARED secret, not per-org, so the
path ``org_slug`` is the SOLE tenant selector. DockerManager LOSSILY sanitizes the slug
for the container/volume/network name (re.sub([^a-zA-Z0-9_.-] -> "-").strip("-")), so two
DISTINCT slugs can COLLIDE onto ONE container ("acme/prod" == "acme-prod"; "-acme" ==
"acme"; any all-invalid/empty slug -> "default"), and find_container matches by that name
FIRST — a cross-tenant hazard (org B's RPC would execute in / destroy org A's sandbox).
The broker now rejects (400) any non-canonical org_slug at every /{org_slug}/ route so a
malformed/colliding slug is never resolved to a container.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from auth import BROKER_KEY_HEADER
from sandbox.docker_manager import DockerManager, SandboxDockerConfig
from sandbox.registry import SandboxRegistry
from sandbox.routes import _require_canonical_org_slug, build_sandbox_router

BROKER_KEY = "test-broker-secret"


def _mock_client() -> MagicMock:
    client = MagicMock()
    client.ping.return_value = True
    client.containers.list.return_value = []
    client.networks.get.side_effect = Exception("not found")
    client.networks.create.return_value = MagicMock()
    client.volumes.get.side_effect = Exception("not found")
    return client


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("MCP_BROKER_INTERNAL_KEY", BROKER_KEY)
    registry = SandboxRegistry(clock=lambda: 1_719_660_000.0)
    dm = DockerManager(client=_mock_client(),
                       config=SandboxDockerConfig(memory_mb=2048, cpus=1.0),
                       registry=registry)
    app = FastAPI()
    app.include_router(build_sandbox_router(dm))
    return TestClient(app)


# ── Pure validator: the sanitize-collision source of truth ───────────────────

_COLLIDING = ["acme/prod", "acme prod", "-acme", "acme-", "teñant", "", "///", "!!!",
              "a" * 65, "../evil", "org\tslug", "UPPER/lower"]
_CANONICAL = ["acme", "acme-prod", "acme_corp", "acme.dev", "Acme123", "a", "o-1_2.3",
              "a" * 64]


@pytest.mark.parametrize("slug", _COLLIDING)
def test_validator_rejects_noncanonical(slug):
    with pytest.raises(Exception):  # HTTPException(400)
        _require_canonical_org_slug(slug)


@pytest.mark.parametrize("slug", _CANONICAL)
def test_validator_accepts_canonical(slug):
    # Must NOT raise, and must be exactly what the DockerManager sanitizer produces
    # (1:1 mapping → no collision possible).
    import re
    _require_canonical_org_slug(slug)
    assert re.sub(r"[^a-zA-Z0-9_.-]", "-", slug).strip("-") == slug


def test_two_distinct_slugs_never_share_a_container_name():
    # The concrete cross-tenant collision: BOTH would sanitize to "acme-prod".
    with pytest.raises(Exception):
        _require_canonical_org_slug("acme/prod")   # collides with the real "acme-prod"
    _require_canonical_org_slug("acme-prod")        # the canonical one is fine


# ── Route-level: every /{org_slug}/ entry rejects a colliding slug (400) ──────

def _h():
    return {BROKER_KEY_HEADER: BROKER_KEY}


def test_rpc_rejects_colliding_slug(client):
    # "acme prod" (space) sanitizes to "acme-prod" — collides with the real "acme-prod".
    # (An encoded-SLASH slug like acme%2Fprod is separately rejected by path routing (404)
    # before the handler, so a space is used to exercise the validator itself.)
    r = client.post("/v1/sandbox/acme%20prod/rpc", headers=_h(),
                    json={"server_slug": "s", "method": "tools/call", "jsonrpc_id": 1})
    assert r.status_code == 400
    assert "org_slug" in r.text


def test_ensure_rejects_colliding_slug(client):
    r = client.post("/v1/sandbox/acme%20prod/ensure", headers=_h(), json={})
    assert r.status_code == 400


def test_status_rejects_colliding_slug(client):
    r = client.get("/v1/sandbox/-acme/status", headers=_h())
    assert r.status_code == 400


def test_delete_rejects_colliding_slug(client):
    # A colliding slug must NOT be able to destroy another org's sandbox.
    r = client.delete("/v1/sandbox/acme-", headers=_h())
    assert r.status_code == 400


def test_canonical_slug_still_routes(client, monkeypatch):
    # A valid slug passes the validator (then fails later on mock-docker, not 400).
    monkeypatch.setattr("sandbox.routes.cached_docker_ok", lambda: False)
    r = client.post("/v1/sandbox/acme-prod/rpc", headers=_h(),
                    json={"server_slug": "s", "method": "ping", "jsonrpc_id": 1})
    assert r.status_code != 400  # 503 (docker unavailable) — validation passed


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
