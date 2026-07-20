"""CP23: the gateway must classify a control /tools/call non-200 as decision=block
when it is an ENFORCEMENT denial (policy/guard), and only as decision=error for a
genuine fault. Fixes the CP22 anomaly where enforcement 403s (tool_not_registered,
etc.) were miscounted as backend_error_http_403 errors.
"""
import pytest

from ai_mesh_gateway.mcp_proxy import _classify_backend_failure


@pytest.mark.parametrize("status,data,expected_decision,expected_reason", [
    # Enforcement denials → block, with the enforcement reason preserved.
    (403, {"error": "Tool is not registered for this server", "reason": "tool_not_registered"},
     "block", "tool_not_registered"),
    (403, {"error": "Tool is disabled", "reason": "tool_disabled"}, "block", "tool_disabled"),
    (400, {"error": "Schema validation failed", "reason": "invalid_arguments"},
     "block", "invalid_arguments"),
    # Policy block: free-text reason, identified by the stable error marker.
    (403, {"error": "Tool call blocked by policy", "reason": "credentials not allowed"},
     "block", "credentials not allowed"),
    (403, {"error": "Tool call blocked by policy", "reason": ""},
     "block", "blocked_by_policy"),
])
def test_enforcement_denial_is_block(status, data, expected_decision, expected_reason):
    decision, reason, enforced_at = _classify_backend_failure(status, data)
    assert decision == expected_decision
    assert reason == expected_reason
    assert enforced_at == "backend"


@pytest.mark.parametrize("status,data", [
    (404, {"error": "Server not found for this organization.", "server_slug": "x"}),
    (400, {"error": "Missing 'name' field"}),
    (400, {"error": "Request body must be a JSON object."}),
    (500, {"error": "boom"}),
    (502, {}),
    (503, None),
])
def test_genuine_fault_stays_error(status, data):
    decision, reason, enforced_at = _classify_backend_failure(status, data)
    assert decision == "error"
    assert reason == f"backend_error_http_{status}"
    assert enforced_at == "gateway"


def test_5xx_with_spurious_reason_is_still_error():
    # A 5xx is a server fault even if the body happens to carry a reason.
    decision, reason, _ = _classify_backend_failure(500, {"reason": "tool_disabled"})
    assert decision == "error"
    assert reason == "backend_error_http_500"
