"""GW17c Property 11 and the field-name contract across control, projector and `principal_of`."""

from __future__ import annotations

import json
import random
from typing import Any

import pytest

from gateway_v2.admit.identity import principal_of
from gateway_v2.domain.identity import DENY_ALL_TOOLS, Principal
from gateway_v2.domain.state import SignedRecord, StateKind, StateOp, StoreDataUnavailable, Version
from gateway_v2.runtime.resources_mcp import mcp_policy_bounds
from gateway_v2.runtime.state_sig import body_hash, canonical_body
from state_control.projector import _key_body, desired_state
from state_control.source import MemorySource, SourceKey, SourceView

ITERATIONS = 200
_BOUNDS = mcp_policy_bounds()


def _record(body: dict[str, Any]) -> SignedRecord:
    raw = canonical_body(body)
    return SignedRecord(
        kind=StateKind.KEY,
        key="k" * 64,
        version=Version(1, 1),
        feed_seq=7,
        content_hash=body_hash(raw),
        deleted=False,
        op=StateOp.PUT,
        signature="s",
        body=raw,
    )


def _names(rng: random.Random) -> tuple[str, ...]:
    count = rng.randint(0, 6)
    return tuple({f"tool_{rng.randint(0, 99)}" for _ in range(count)})


def test_field_names_are_pinned() -> None:
    body = _key_body("kid", "acme", ("a",), 3)
    assert set(body) == {
        "key_id",
        "org_id",
        "rate_per_s",
        "burst",
        "mcp_allowed_tools",
        "mcp_max_tool_calls",
    }
    assert set(Principal.__dataclass_fields__) >= {"mcp_allowed_tools", "mcp_max_tool_calls"}


def test_default_key_body_bytes_do_not_churn() -> None:
    assert _key_body("kid", "acme") == {
        "key_id": "kid",
        "org_id": "acme",
        "rate_per_s": 0.0,
        "burst": 0.0,
    }


# Feature: gw32-mcp-transport, Property 11: principal round trip
def test_property_11_round_trip() -> None:
    rng = random.Random(1102)
    for _ in range(ITERATIONS):
        allowed, cap = _names(rng), rng.choice([0, 1, 5, _BOUNDS.cap_max])
        principal = principal_of(_record(dict(_key_body("kid", "acme", allowed, cap))))
        assert principal is not None
        assert principal.mcp_allowed_tools == frozenset(allowed)
        assert principal.mcp_max_tool_calls == cap


def test_absent_fields_are_unrestricted() -> None:
    principal = principal_of(_record({"key_id": "k", "org_id": "o", "rate_per_s": 0, "burst": 0}))
    assert principal is not None
    assert principal.mcp_allowed_tools == frozenset() and principal.mcp_max_tool_calls == 0


@pytest.mark.parametrize(
    "field,value",
    [
        ("mcp_allowed_tools", "tool"),
        ("mcp_allowed_tools", None),
        ("mcp_allowed_tools", []),
        ("mcp_allowed_tools", [1]),
        ("mcp_allowed_tools", ["a", "a"]),
        ("mcp_allowed_tools", ["bad\x01name"]),
        ("mcp_allowed_tools", [""]),
        ("mcp_allowed_tools", ["x" * (_BOUNDS.tool_name_max + 1)]),
        ("mcp_allowed_tools", [f"t{i}" for i in range(_BOUNDS.tools_max + 1)]),
        ("mcp_max_tool_calls", True),
        ("mcp_max_tool_calls", "5"),
        ("mcp_max_tool_calls", None),
        ("mcp_max_tool_calls", 1.5),
        ("mcp_max_tool_calls", -1),
        ("mcp_max_tool_calls", _BOUNDS.cap_max + 1),
    ],
)
def test_malformed_policy_never_resolves_unrestricted(field: str, value: object) -> None:
    body: dict[str, Any] = {"key_id": "k", "org_id": "o", "rate_per_s": 0, "burst": 0, field: value}
    with pytest.raises(StoreDataUnavailable):
        principal_of(_record(body))


def test_deny_all_sentinel_is_accepted() -> None:
    principal = principal_of(_record(dict(_key_body("k", "o", (DENY_ALL_TOOLS,), 0))))
    assert principal is not None and principal.mcp_allowed_tools == frozenset({DENY_ALL_TOOLS})


def _view(**policy: Any) -> SourceView:
    source = MemorySource(clock=lambda: 1.0)
    source.org("acme")
    source.key("a" * 64, key_id="kid", org_slug="acme", **policy)
    return source.read()


def test_projector_flag_off_emits_default_bytes() -> None:
    view = _view(mcp_allowed_tools=("x",), mcp_max_tool_calls=4)
    body = desired_state(view).keys["a" * 64]
    assert "mcp_allowed_tools" not in body and "mcp_max_tool_calls" not in body


def test_projector_flag_on_emits_policy_and_changes_the_body() -> None:
    view = _view(mcp_allowed_tools=("y", "x", "x"), mcp_max_tool_calls=4)
    on = desired_state(view, mcp_key_policy=True).keys["a" * 64]
    assert on["mcp_allowed_tools"] == ["x", "y"] and on["mcp_max_tool_calls"] == 4
    off = desired_state(view).keys["a" * 64]
    assert canonical_body(on) != canonical_body(off)  # a policy-only change is a new record


def test_projector_over_bound_is_stricter_never_looser() -> None:
    many = tuple(f"t{i}" for i in range(_BOUNDS.tools_max + 1))
    view = _view(mcp_allowed_tools=many, mcp_max_tool_calls=_BOUNDS.cap_max + 5)
    desired = desired_state(view, mcp_key_policy=True)
    body = desired.keys["a" * 64]
    assert body["mcp_allowed_tools"] == [DENY_ALL_TOOLS]
    assert body["mcp_max_tool_calls"] == _BOUNDS.cap_max
    assert any("kid" in note for note in desired.notes)


def test_projector_malformed_control_value_is_deny_all() -> None:
    view = _view(mcp_allowed_tools=None)
    body = desired_state(view, mcp_key_policy=True).keys["a" * 64]
    assert body["mcp_allowed_tools"] == [DENY_ALL_TOOLS]
    assert json.dumps(body)  # serialisable


def test_old_reader_ignores_new_fields() -> None:
    """Rolling deploy: a pre-change `principal_of` read only the four names."""
    body = dict(_key_body("kid", "acme", ("x",), 3))
    legacy = Principal(
        key_id=str(body["key_id"]),
        org_id=str(body["org_id"]),
        rate_per_s=0.0,
        burst=0.0,
        feed_seq=1,
    )
    assert legacy.mcp_allowed_tools == frozenset() and legacy.mcp_max_tool_calls == 0
    assert SourceKey("h", "k", "o", True).mcp_allowed_tools == ()
