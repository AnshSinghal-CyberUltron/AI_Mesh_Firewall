"""GW33 D-11/D-19 through the real §10.4 lifecycle: the canary rides as a verified slot, or the
request is refused before dispatch with `canary_unavailable` and zero provider calls."""
from __future__ import annotations

import asyncio
import json
import random
from dataclasses import replace
from typing import cast

import pytest

from gateway_v2.dispatch.openai_upstream import BODY_OPENAI_KEY
from gateway_v2.domain import Action, Category, FailurePosture, StreamingMode, posture
from gateway_v2.edge import canary_inject
from gateway_v2.edge.openai_chat import ChatSurface
from gateway_v2.edge.routes import ChatRoute
from tests.e2e.gw33_support import contract, plan_of, rule
from tests.edge.test_lgw15_input_block import (
    _p9_build_deps,
    _P9CountingProvider,
    _P9Snapshot,
)


def _run(body: dict[str, object], canary_rule):  # type: ignore[no-untyped-def]
    provider = _P9CountingProvider(rng=random.Random(1))
    deps = _p9_build_deps("never-present", provider)
    plan = plan_of(StreamingMode.INCREMENTAL, canary_rule)
    deps.snapshot_source = lambda: _P9Snapshot(replace(plan, org_id="p9-org"))  # type: ignore[assignment]
    deps.contract = contract()  # type: ignore[assignment]
    surface = ChatSurface(deps=cast("ChatRoute", deps))
    scope = {"path": "/v1/chat/completions"}
    req, outcome = asyncio.run(surface.run_lifecycle(scope, json.dumps(body).encode()))
    return provider, req, outcome


_CHAT = {"model": "gpt-4o", "messages": [{"role": "user", "content": "hello there"}]}
_COMPLETION = {"model": "gpt-4o", "prompt": "hello there"}


def test_a_chat_request_gets_one_verified_system_slot_carrying_the_token() -> None:
    canary = rule("out_canary", Category.CANARY, Action.BLOCK, "output.canary")
    provider, req, outcome = _run(_CHAT, canary)
    assert outcome.refuse_code is None and provider.open_calls == 0  # lifecycle only
    assert req is not None and len(req.text_deltas) == 2
    messages = outcome.body[BODY_OPENAI_KEY]["messages"]  # type: ignore[index]
    assert [m["role"] for m in messages] == ["user", "system"]  # type: ignore[index]
    assert str(messages[1]["content"]).startswith("Internal reference cnry_")  # type: ignore[index]


def test_a_legacy_completions_prompt_is_chat_shaped_upstream_so_it_is_injected_too() -> None:
    canary = rule("out_canary", Category.CANARY, Action.BLOCK, "output.canary")
    _provider, req, outcome = _run(_COMPLETION, canary)
    assert outcome.refuse_code is None and req is not None and len(req.text_deltas) == 2


def test_a_fail_closed_canary_that_cannot_be_injected_is_refused_before_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def broken(*_a: object, **_k: object) -> object:
        raise RuntimeError("entropy source down")

    monkeypatch.setattr(canary_inject, "new_ticket", broken)
    canary = rule("out_canary", Category.CANARY, Action.BLOCK, "output.canary")
    provider, _req, outcome = _run(_CHAT, canary)
    assert outcome.refuse_code == posture.CANARY_UNAVAILABLE
    assert provider.open_calls == 0


def test_a_fail_open_canary_that_cannot_be_injected_proceeds_unchecked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def broken(*_a: object, **_k: object) -> object:
        raise RuntimeError("entropy source down")

    monkeypatch.setattr(canary_inject, "new_ticket", broken)
    canary = rule("out_canary", Category.CANARY, Action.FLAG, "output.canary",
                  posture=FailurePosture.FAIL_OPEN)
    _provider, req, outcome = _run(_CHAT, canary)
    assert outcome.refuse_code is None and req is not None and len(req.text_deltas) == 1


def test_a_plan_without_a_canary_rule_is_untouched() -> None:
    other = rule("r", Category.PII, Action.REDACT)
    _provider, req, outcome = _run(_CHAT, other)
    assert outcome.refuse_code is None and req is not None and len(req.text_deltas) == 1
