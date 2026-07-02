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


# CHG-0046 — a non-string keyed/dot-path target must get a REAL setter, not a no-op.
# Before, a detected secret/PII in a number/list/object value was reported redacted
# (result_redacted=True) but egressed RAW, and the E12 floor was bypassed.


def test_key_path_numeric_value_setter_is_not_noop():
    # A numeric SSN keyed value: the target text carries the digits (so it IS
    # scanned), and the setter must now actually replace it (was a no-op → leak).
    payload = {"account": {"ssn": 123456789, "note": "ok"}}
    state, targets = extract_and_bind(payload, target_mode="key_path", key_path="ssn")
    assert len(targets) == 1
    text, setter, _ = targets[0]
    assert "123456789" in text  # numeric value is present in the scan text
    setter("[REDACTED]")
    assert state[0]["account"]["ssn"] == "[REDACTED]"  # real mutation, not no-op
    assert state[0]["account"]["note"] == "ok"


def test_key_path_list_value_setter_is_not_noop():
    # A list of emails keyed value (non-string) must be maskable in place.
    payload = {"contacts": ["a@b.com", "c@d.com"]}
    state, targets = extract_and_bind(payload, target_mode="key_path", key_path="contacts")
    assert len(targets) == 1
    text, setter, _ = targets[0]
    assert "a@b.com" in text
    setter('["[REDACTED]", "[REDACTED]"]')
    assert state[0]["contacts"] == '["[REDACTED]", "[REDACTED]"]'  # replaced, not raw


def test_dot_path_numeric_value_setter_is_not_noop():
    # Dot-path resolving to a non-string value must also mutate (was a no-op).
    payload = {"account": {"ssn": 123456789}}
    state, targets = extract_and_bind(
        payload, target_mode="key_path", key_path="account.ssn"
    )
    assert len(targets) == 1
    text, setter, _ = targets[0]
    assert "123456789" in text
    setter("[REDACTED]")
    assert state[0]["account"]["ssn"] == "[REDACTED]"  # real dot-path mutation
