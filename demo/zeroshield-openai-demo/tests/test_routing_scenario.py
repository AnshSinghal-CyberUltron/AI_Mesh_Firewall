"""Unit tests for routing scenario contract in the demo client."""
from __future__ import annotations

from unittest.mock import patch

from app.gateway_client import ZeroShieldClient


def test_build_routing_preferences_standard_maps_to_public():
    prefs = ZeroShieldClient.build_routing_preferences(
        routing_preferences={"enable_routing": True, "data_sensitivity": "standard"},
    )
    assert prefs["enable_routing"] is True
    assert prefs["data_sensitivity"] == "public"


def test_build_routing_preferences_hipaa_adds_compliance_tag():
    prefs = ZeroShieldClient.build_routing_preferences(
        routing_preferences={"data_sensitivity": "hipaa"},
    )
    assert prefs["data_sensitivity"] == "restricted"
    assert "hipaa" in [str(t).lower() for t in prefs.get("compliance_requirements") or []]


def test_build_routing_preferences_preserves_explicit_compliance():
    prefs = ZeroShieldClient.build_routing_preferences(
        routing_preferences={
            "enable_routing": True,
            "data_sensitivity": "restricted",
            "compliance_requirements": ["soc2"],
        },
    )
    assert prefs["data_sensitivity"] == "restricted"
    assert "soc2" in prefs["compliance_requirements"]


def test_scenario_routing_uses_respond_and_echoes_prefs():
    client = ZeroShieldClient.__new__(ZeroShieldClient)
    incoming = {"enable_routing": True, "data_sensitivity": "restricted"}
    with patch.object(
        client,
        "respond",
        return_value={
            "content": "ok",
            "model": "auto",
            "zeroshield": {
                "action": "allow",
                "routing": {
                    "requested_model": "auto",
                    "selected_model": "haiku-cheap",
                    "decision_source": "adjudicator",
                    "routing_reason": "Selected by policy weights.",
                },
            },
        },
    ) as respond:
        out = client.scenario_routing(
            "Route this",
            model="auto",
            routing_preferences=incoming,
        )
    respond.assert_called_once()
    _args, kwargs = respond.call_args
    assert kwargs["routing_preferences"]["enable_routing"] is True
    assert kwargs["routing_preferences"]["data_sensitivity"] == "restricted"
    assert out["routing_preferences"]["data_sensitivity"] == "restricted"
    assert out["status_reason"]["code"] == "allowed"
