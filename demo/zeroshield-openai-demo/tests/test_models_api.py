"""API contract for org-allowed model listing."""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.server import app


def test_models_endpoint_lists_org_allowed_models():
    client = TestClient(app)
    res = client.get("/api/models")
    assert res.status_code == 200
    body = res.json()
    assert body.get("default_model") == "auto"
    assert isinstance(body.get("models"), list)
    assert body.get("count") == len(body["models"])
    for item in body["models"]:
        assert "id" in item
