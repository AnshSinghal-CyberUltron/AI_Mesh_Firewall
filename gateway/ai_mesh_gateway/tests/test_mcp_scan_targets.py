"""Tests for MCP scan target extraction and redaction binding."""

from mcp_scan_targets import collect_key_values, extract_and_bind


def test_collect_key_values_nested():
    payload = {"arguments": {"email": "a@b.com"}, "meta": {"email": "c@d.com"}}
    values = collect_key_values(payload, "email")
    assert "a@b.com" in values
    assert "c@d.com" in values


def test_extract_and_bind_key_path_redacts_single_field():
    payload = {"arguments": {"email": "secret@example.com", "note": "ok"}}
    state, targets = extract_and_bind(
        payload,
        target_mode="key_path",
        key_path="email",
    )
    assert len(targets) == 1
    text, setter, label = targets[0]
    assert "secret" in text
    setter("[REDACTED]")
    assert state[0]["arguments"]["email"] == "[REDACTED]"
    assert state[0]["arguments"]["note"] == "ok"


def test_extract_and_bind_entire_payload():
    payload = {"message": "hello"}
    state, targets = extract_and_bind(payload, target_mode="entire", key_path="")
    assert len(targets) == 1
    _, setter, label = targets[0]
    assert label == "entire"
    setter('{"message": "redacted"}')
    assert state[0]["message"] == "redacted"
