"""GW34 Task 7: a route over a contract with no ``tool_bounds`` is tools-disabled (fail closed)."""
from __future__ import annotations

import asyncio
import json
import random
from collections.abc import Mapping
from typing import cast

from gateway_v2.edge.openai_chat import REFUSE_PRE_CHECK, ChatSurface
from gateway_v2.edge.routes import ChatRoute
from gateway_v2.edge.wire.openai_codes import UNSUPPORTED_PARAMETER
from tests.edge.test_lgw15_input_block import _p9_build_deps, _P9CountingProvider

_SCOPE: Mapping[str, object] = {"path": "/v1/chat/completions"}


def _lifecycle(body: bytes) -> tuple[str | None, int, bool]:
    provider = _P9CountingProvider(rng=random.Random(1))
    deps = _p9_build_deps("never-matches", provider)
    surface = ChatSurface(deps=cast("ChatRoute", deps))
    _req, outcome = asyncio.run(surface.run_lifecycle(_SCOPE, body))
    return outcome.refuse_code, provider.open_calls, getattr(deps, "tool_bounds", None) is None


def test_the_fake_contract_serves_plain_chat_and_stream_requests() -> None:
    for stream in (False, True):
        body = json.dumps({"model": "m", "stream": stream,
                           "messages": [{"role": "user", "content": "hi"}]}).encode()
        refused, _calls, _ = _lifecycle(body)
        assert refused is None


def test_a_tools_request_on_a_fake_contract_is_a_declared_refusal_not_a_500() -> None:
    for extra in ({"tools": [{"type": "function", "function": {"name": "f"}}]},
                  {"response_format": {"type": "json_object"}},
                  {"tool_choice": "auto"}):
        body = json.dumps({"model": "m", "messages": [{"role": "user", "content": "hi"}],
                           **extra}).encode()
        refused, calls, no_bounds = _lifecycle(body)
        assert refused == UNSUPPORTED_PARAMETER and calls == 0 and no_bounds


def test_the_fake_contracts_own_part_ceiling_still_applies() -> None:
    # the fake's max_request_parts() is generous (1_000_000): a flood above it is refused, and a
    # body that merely mentions ``tools`` never reads an allowance the fake does not have
    flood = b'{"model":"m","messages":[{"role":"user","content":"' + b"," * 1_000_001 + b'"}]}'
    provider = _P9CountingProvider(rng=random.Random(1))
    deps = _p9_build_deps("never-matches", provider)
    surface = ChatSurface(deps=cast("ChatRoute", deps))
    # the fake's byte bound (1 MB) also rejects it first; both are the declared pre-check code
    refused = surface._pre_checks(_SCOPE, flood)
    assert refused == REFUSE_PRE_CHECK
