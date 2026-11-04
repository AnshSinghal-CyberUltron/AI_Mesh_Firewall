"""GW27 Task 6: phase order, response ceiling and the required ``org_limits`` field."""
from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from gateway_v2.edge.routes import ChatRoute
from tests.edge import _gw27_harness as h


class _SpyBudget:
    """Counts budget evaluations; admits everything."""

    def __init__(self) -> None:
        self.evaluated = 0
        self.refunded = 0

    async def evaluate(self, cost: int, **_kwargs: object) -> object:
        from gateway_v2.admit.grant import Admitted

        self.evaluated += 1
        return Admitted(owner_id="dev-org", request_id="r", admitted_at=0.0)

    def refund(self, cost: int, *, request_id: str, owner: str) -> None:
        self.refunded += 1


def test_the_rate_check_runs_before_the_budget(tmp_path: Path) -> None:
    provider, spy = h.Recording(), _SpyBudget()
    controls = {"rate": {"enabled": True, "requests_per_minute": 1, "burst": 1}}
    app, _ = h.build(tmp_path, controls, provider, quota=spy)
    assert h.run(app, h.CHAT)[0] == 200
    assert spy.evaluated == 1
    assert h.run(app, h.CHAT)[0] == 429
    assert spy.evaluated == 1  # a rate-limited request never touched the budget
    assert provider.opens == 1


@pytest.mark.parametrize("call", [h.CHAT, h.CHAT_STREAM], ids=["non-stream", "stream"])
def test_the_org_response_ceiling_reaches_the_upstream_body(
    tmp_path: Path, call: tuple[str, dict[str, object]]
) -> None:
    provider = h.Recording()
    app, _ = h.build(tmp_path, {"max_response_tokens": 77}, provider)
    assert h.run(app, call)[0] == 200
    assert provider.openai_bodies[-1]["max_tokens"] == 77


def test_the_contract_default_applies_without_a_plan_ceiling(tmp_path: Path) -> None:
    provider = h.Recording()
    app, _ = h.build(tmp_path, None, provider)
    unset = ("/v1/chat/completions", {"model": "echo-1", "messages": h.CHAT[1]["messages"]})
    assert h.run(app, unset)[0] == 200
    expected = min(
        h.contract().default_response_token_ceiling(), h.contract().max_output_tokens()
    )
    assert provider.openai_bodies[-1]["max_tokens"] == expected


def test_a_route_built_without_org_limits_is_a_type_error() -> None:
    required = [
        f.name for f in dataclasses.fields(ChatRoute)
        if f.default is dataclasses.MISSING and f.default_factory is dataclasses.MISSING
    ]
    assert "org_limits" in required
    with pytest.raises(TypeError):
        ChatRoute(**{name: None for name in required if name != "org_limits"})  # type: ignore[arg-type]
