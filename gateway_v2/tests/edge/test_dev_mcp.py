# Validates: Requirements 3.6, 6.2, 7.1
"""DEV PROFILE MCP wiring: all-or-nothing, and no secret in the boot log."""

from __future__ import annotations

import pytest

from gateway_v2.edge.dev_components import DevProfileError
from gateway_v2.edge.dev_mcp import build_dev_mcp
from tests.edge.test_responses_route import _contract

_FULL = {
    "AMF_DEV_MCP_BROKER_URL": "http://mcp-broker:8311",
    "AMF_DEV_MCP_BROKER_KEY": "broker-secret-value",
    "AMF_DEV_CONTROL_URL": "http://control:8100",
    "AMF_DEV_GATEWAY_INTERNAL_KEY": "internal-secret-value",
    "AMF_DEV_MCP_ORG_MAP": "dev-org=zeroshield",
}


def test_unset_keeps_the_501() -> None:
    deps, log = build_dev_mcp({}, _contract(), redis_client=None, clock=lambda: 0.0)
    assert deps is None
    assert "501" in log


def test_partial_configuration_refuses_to_boot() -> None:
    partial = dict(_FULL)
    del partial["AMF_DEV_MCP_ORG_MAP"]
    with pytest.raises(DevProfileError, match="AMF_DEV_MCP_ORG_MAP"):
        build_dev_mcp(partial, _contract(), redis_client=None, clock=lambda: 0.0)


def test_full_configuration_builds_and_redacts_secrets() -> None:
    deps, log = build_dev_mcp(_FULL, _contract(), redis_client=None, clock=lambda: 0.0)
    assert deps is not None
    assert deps.org_map.control_slug("dev-org") == "zeroshield"
    assert "npx" in deps.stdio_allowed
    assert deps.allow_hosts == frozenset()
    assert "broker-secret-value" not in log and "internal-secret-value" not in log


@pytest.mark.parametrize(
    ("name", "value"),
    (("AMF_DEV_MCP_BROKER_URL", "mcp-broker:8311"), ("AMF_DEV_MCP_ORG_MAP", "dev-org")),
)
def test_bad_values_refuse_to_boot(name: str, value: str) -> None:
    with pytest.raises(DevProfileError):
        build_dev_mcp({**_FULL, name: value}, _contract(), redis_client=None, clock=lambda: 0.0)
