"""GW28 P12: the routed seam -- not a ProviderClient, one caller, per_request_safe enforced."""

from __future__ import annotations

import ast
import random
from pathlib import Path

import httpx
import pytest

import gateway_v2
from gateway_v2.dispatch.http_provider import HttpProviderClient
from gateway_v2.dispatch.model_routing import ModelRouter
from gateway_v2.dispatch.openai_upstream import OpenAIUpstreamProvider
from gateway_v2.dispatch.provider import ProviderClient, StubProviderClient
from gateway_v2.dispatch.routed_provider import RoutedProvider
from gateway_v2.dispatch.routing_types import RoutingConfigError, RoutingSnapshot, RoutingTable
from gateway_v2.dispatch.stall_guard import StallBounds
from gateway_v2.edge.wire.sse import SSEDecoder
from tests.dispatch._gw28_fakes import MAX_LEN, SMALL, FakeClient, Timeline, build_rp, cand, cset

_ROOT = Path(gateway_v2.__file__).resolve().parent


def _build(client: object, **over: object) -> RoutedProvider:
    kwargs: dict[str, object] = {
        "router": ModelRouter(),
        "table": RoutingTable.build({"org": cset(cand("a"))}),
        "clients": {"a": client},
        "bounds": SMALL,
        "max_attempts": 2,
        "max_ident_len": MAX_LEN,
        "snapshot_source": lambda: RoutingSnapshot(),
    }
    kwargs.update(over)
    return RoutedProvider(**kwargs)  # type: ignore[arg-type]


def _real() -> OpenAIUpstreamProvider:
    return OpenAIUpstreamProvider(
        base_url="http://x/v1",
        decoder_factory=SSEDecoder,
        client_factory=lambda: httpx.AsyncClient(),
        connections=3,
    )


def test_routed_provider_is_not_a_provider_client() -> None:
    rp = build_rp({"a": FakeClient("a", [], Timeline())})
    assert not isinstance(rp, ProviderClient)
    assert not hasattr(rp, "open")


def test_per_request_safe_clients_accepted_and_instance_latch_clients_rejected() -> None:
    _build(_real())
    stub = StubProviderClient(script=[], rng=random.Random(0))
    http = HttpProviderClient(
        client_factory=lambda: None, decoder_factory=lambda: None, clock=lambda: 0.0
    )  # type: ignore[arg-type,return-value]
    for bad in (stub, http, object()):
        with pytest.raises(RoutingConfigError):
            _build(bad)
    assert _real().connections == 3


def test_construction_validation() -> None:
    with pytest.raises(RoutingConfigError):
        _build(FakeClient("a", [], Timeline()), clients={})  # candidate without a client
    with pytest.raises(RoutingConfigError):
        _build(FakeClient("a", [], Timeline()), max_attempts=0)
    with pytest.raises(RoutingConfigError):
        StallBounds(first_event_s=1.0, gap_s=2.0, total_s=3.0, teardown_s=0.1)


def test_open_routed_is_only_called_from_the_chat_upstream_module() -> None:
    callers: set[str] = set()
    for path in _ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "open_routed"
            ):
                callers.add(path.relative_to(_ROOT).as_posix())
    assert callers <= {"edge/chat_upstream.py"}, callers


def test_provider_abort_is_a_noop() -> None:
    import asyncio

    rp = build_rp({"a": FakeClient("a", [], Timeline())})
    assert asyncio.run(rp.abort()) is None
