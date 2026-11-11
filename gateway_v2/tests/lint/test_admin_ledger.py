"""A new admin call fails the ledger, and a 501 cannot be signed as built."""

from __future__ import annotations

from lint.admin_ledger import _row_errors, compare
from lint.console_inventory import repo_root


def test_checked_in_admin_ledger_matches_the_tree() -> None:
    assert compare(repo_root()) == []


def test_a_new_caller_is_not_in_the_ledger(monkeypatch) -> None:
    import lint.admin_ledger as ledger

    real = ledger.scan

    def extra(repo):
        found = real(repo)
        found["/v1/rag/query"] = [*found["/v1/rag/query"], "frontend/src/new.jsx"]
        return found

    monkeypatch.setattr(ledger, "scan", extra)
    messages = ledger.compare(repo_root())
    assert any("callers do not match" in message for message in messages)


def test_a_placeholder_cannot_be_signed_as_built() -> None:
    messages = _row_errors(
        "/v1/mcp",
        {
            "decision": "build",
            "reason": "ship it",
            "callers": ["gateway_v2/gateway_v2/edge/routes.py"],
        },
        {"/v1/mcp": ["gateway_v2/gateway_v2/edge/routes.py"]},
        {"/v1/mcp"},
    )
    assert any("cannot be build" in message for message in messages)


def test_retiring_a_placeholder_names_the_501() -> None:
    messages = _row_errors(
        "/v1/mcp",
        {
            "decision": "retire",
            "reason": "later",
            "callers": ["gateway_v2/gateway_v2/edge/routes.py"],
        },
        {"/v1/mcp": ["gateway_v2/gateway_v2/edge/routes.py"]},
        {"/v1/mcp"},
    )
    assert any("501" in message for message in messages)
