"""Unit tests for ``RequestHolds.begin_request`` (request-id-uniqueness, requirement 2.6)."""
from __future__ import annotations

import pytest

from gateway_v2.edge.request_holds import RequestHolds
from gateway_v2.edge.request_ids import RequestIdUnavailable


def test_first_call_mints_and_notes() -> None:
    holds = RequestHolds()
    calls: list[str] = []

    def mint(org: str) -> str:
        calls.append(org)
        return f"zs-{org}-aa"

    assert holds.begin_request("acme", mint) == "zs-acme-aa"
    assert holds.request_id == "zs-acme-aa"
    assert calls == ["acme"]


def test_second_call_returns_same_id_without_minting() -> None:
    holds = RequestHolds()
    ids = iter(["zs-acme-01", "zs-acme-02"])
    first = holds.begin_request("acme", lambda _o: next(ids))
    second = holds.begin_request("acme", lambda _o: next(ids))
    assert first == second == "zs-acme-01"


def test_failing_mint_propagates_and_notes_nothing() -> None:
    holds = RequestHolds()

    def boom(_org: str) -> str:
        raise RequestIdUnavailable

    with pytest.raises(RequestIdUnavailable):
        holds.begin_request("acme", boom)
    assert holds.request_id == ""


def test_default_mint_is_tenant_scoped() -> None:
    assert RequestHolds().begin_request("acme").startswith("zs-acme-")
