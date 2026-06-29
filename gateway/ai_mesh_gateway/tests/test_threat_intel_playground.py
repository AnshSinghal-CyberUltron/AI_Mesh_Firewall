"""Threat-intel skip rules for isolation playground keys."""
from types import SimpleNamespace

from ai_mesh_gateway.playground_auth import is_live_test_project_id, should_skip_threat_intel


def test_skip_by_project_id():
    ctx = SimpleNamespace(
        project_id="isolation-playground-acme",
        permissions={},
        risk_score=0.99,
    )
    assert should_skip_threat_intel(ctx) is True


def test_skip_by_playground_permission():
    ctx = SimpleNamespace(
        project_id="other-project",
        permissions={"playground": True},
        risk_score=0.99,
    )
    assert should_skip_threat_intel(ctx) is True


def test_skip_for_attack_simulator_key():
    ctx = SimpleNamespace(
        project_id="simulator-acme",
        permissions={},
        risk_score=1.0,
    )
    assert should_skip_threat_intel(ctx) is True


def test_no_skip_for_production_key():
    ctx = SimpleNamespace(
        project_id="mcp-default-acme",
        permissions={},
        risk_score=0.99,
    )
    assert should_skip_threat_intel(ctx) is False


def test_is_live_test_project_id():
    assert is_live_test_project_id("simulator-zeroshield") is True
    assert is_live_test_project_id("isolation-playground-zeroshield") is True
    assert is_live_test_project_id("prod-api") is False


def test_no_skip_when_auth_missing():
    assert should_skip_threat_intel(None) is False
