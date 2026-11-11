"""LGW33-2 (offline equivalent, not the live gate): an internal hostname and an RFC1918 address
are handled per the org's IP-leakage action."""
from __future__ import annotations

import random

import pytest

from gateway_v2.domain import Action, Category, StreamingMode
from tests.e2e.gw33_support import plan_of, rule, run, split

_TEXT = "The service runs on db.internal at 10.12.4.9 behind the proxy."


def _plan(mode: StreamingMode, action: Action):  # type: ignore[no-untyped-def]
    return plan_of(mode, rule("out_ip_leak", Category.IP_LEAK, action, "output.ip_leak",
                              template="[internal address]"))


@pytest.mark.parametrize("action", [Action.REDACT, Action.REWRITE])
def test_each_internal_value_is_replaced_per_the_action(action: Action) -> None:
    rng = random.Random(33_211)
    marker = "[internal address]" if action is Action.REWRITE else "[REDACTED]"
    for _ in range(60):
        result = run(_plan(StreamingMode.STRICT_WITHHOLD, action), split(_TEXT, rng))
        assert result.released == f"The service runs on {marker} at {marker} behind the proxy."


def test_block_and_flag_and_allow() -> None:
    rng = random.Random(33_212)
    blocked = run(_plan(StreamingMode.STRICT_WITHHOLD, Action.BLOCK), split(_TEXT, rng))
    assert blocked.released == "" and blocked.codes == ["output_blocked"]
    for mode in StreamingMode:
        flagged = run(_plan(mode, Action.FLAG), split(_TEXT, rng))
        assert flagged.released == _TEXT and flagged.stats.review_required is True  # type: ignore[attr-defined]
    allowed = run(_plan(StreamingMode.STRICT_WITHHOLD, Action.ALLOW), split(_TEXT, rng))
    assert allowed.released == _TEXT and allowed.stats.review_required is False  # type: ignore[attr-defined]


def test_public_and_documentation_addresses_are_left_alone() -> None:
    text = "Google DNS 8.8.8.8, docs 192.0.2.10, tool v1.2.3.4-rc1."
    result = run(_plan(StreamingMode.STRICT_WITHHOLD, Action.BLOCK), [("output_content", text)])
    assert result.released == text and result.codes == []
