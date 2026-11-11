"""GW27 Task 6: each billable surface refuses with a 429 and zero provider calls."""
from __future__ import annotations

import time
from pathlib import Path

import pytest

from gateway_v2.admit.org_limits import OrgLimits
from gateway_v2.edge.chat_deps import fail_closed_budget
from tests.edge import _gw27_harness as h


@pytest.mark.parametrize("call", [h.CHAT, h.CHAT_STREAM, h.RESPONSES, h.EMBEDDINGS],
                         ids=["chat", "chat-stream", "responses", "embeddings"])
def test_third_back_to_back_request_is_a_429_with_zero_extra_provider_calls(
    tmp_path: Path, call: tuple[str, dict[str, object]]
) -> None:
    provider = h.Recording()
    app, _deps = h.build(tmp_path, h.BURST_TWO, provider)
    assert h.run(app, call)[0] == 200
    assert h.run(app, call)[0] == 200
    opens = provider.opens
    status, body = h.run(app, call)
    if call is h.CHAT_STREAM:  # a refused stream is a declared SSE error frame on a 200
        assert b"rate_limited" in body, body
    else:
        assert status == 429, body
        assert b"rate_limit_error" in body
    assert provider.opens == opens


@pytest.mark.parametrize("call", [h.CHAT, h.RESPONSES, h.EMBEDDINGS],
                         ids=["chat", "responses", "embeddings"])
def test_org_tokens_are_refunded_through_the_real_release_when_the_budget_refuses(
    tmp_path: Path, call: tuple[str, dict[str, object]]
) -> None:
    provider = h.Recording()
    controls = {"org_tpm_limit": 600}
    app, deps = h.build(tmp_path, controls, provider, quota=fail_closed_budget)
    limits = deps["org_limits"]
    assert isinstance(limits, OrgLimits)
    status, _body = h.run(app, call)
    assert status == 503  # budget_unavailable, after the org check passed
    assert provider.opens == 0
    bucket = limits._orgs[h.ORG].tokens  # noqa: SLF001
    assert bucket is not None
    assert bucket.tat - time.monotonic() <= 0.01  # the charge was given back


def test_no_refund_after_dispatch(tmp_path: Path) -> None:
    provider = h.Recording()
    app, deps = h.build(tmp_path, {"org_tpm_limit": 600}, provider)
    limits = deps["org_limits"]
    assert isinstance(limits, OrgLimits)
    assert h.run(app, h.CHAT)[0] == 200
    bucket = limits._orgs[h.ORG].tokens  # noqa: SLF001
    assert bucket is not None
    assert bucket.tat - time.monotonic() > 1.0  # the provider saw it: the estimate stands


def test_dev_and_production_profiles_supply_org_limits(tmp_path: Path) -> None:
    provider = h.Recording()
    _app, deps = h.build(tmp_path, None, provider)
    assert isinstance(deps["org_limits"], OrgLimits)
    from tests.edge.test_gw36_s4_boot import _boot

    prod_deps, logs = _boot()
    assert isinstance(prod_deps["org_limits"], OrgLimits)
    assert any(line.startswith("boot: org_limits ->") for line in logs)
