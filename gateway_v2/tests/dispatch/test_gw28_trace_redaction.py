"""GW28 P9: the routing trace carries no content and no unvalidated client text."""

from __future__ import annotations

import asyncio
import random
import string

from gateway_v2.dispatch.model_routing import ModelRouter
from gateway_v2.dispatch.routed_provider import RoutedProvider
from gateway_v2.dispatch.routing_types import (
    RoutingDecision,
    RoutingSnapshot,
    RoutingTable,
    ident_pattern,
    is_valid_model_name,
)
from gateway_v2.domain.posture import ROUTING_UNKNOWN_MODEL
from tests.dispatch._gw28_fakes import (
    MAX_LEN,
    RELAXED,
    SMALL,
    FakeClient,
    Timeline,
    build_rp,
    cand,
    cset,
    ctx,
    drain,
    err,
    request,
    text,
)

_SECRET = "sk-PLANTED-SECRET-123456"
_ALPHABET = string.printable + "\x00\x1f\x7f\u00fc\u202e\u0131"


def _fields(d: RoutingDecision) -> list[str]:
    return [
        v
        for v in (
            d.request_key,
            d.requested_model,
            d.selected_candidate,
            d.selected_model,
            d.destination,
            d.remapped_from,
            d.routing_reason,
            d.decision_source,
            d.refusal_code,
            d.cause,
        )
        if isinstance(v, str)
    ]


def test_trace_is_content_free_with_planted_secret() -> None:
    async def main() -> None:
        tl = Timeline()
        decisions: list[RoutingDecision] = []
        clients = {
            "a": FakeClient("a", [err("upstream_unavailable")], tl),
            "b": FakeClient("b", [text("ok", final=True)], tl),
        }
        await drain(build_rp(clients), ctx(sink=decisions.append), req=request(extra=_SECRET))
        assert decisions
        for d in decisions:
            assert all(_SECRET not in f for f in _fields(d))

    asyncio.run(main())


def _fallback_rp() -> RoutedProvider:
    pt = cset(cand("p", passthrough=True), passthrough=True)
    return RoutedProvider(
        router=ModelRouter(),
        table=RoutingTable.build({}, fallback_set=pt),
        clients={"p": FakeClient("p", [text("x", final=True)], Timeline())},
        bounds=SMALL,
        max_attempts=1,
        max_ident_len=MAX_LEN,
        snapshot_source=lambda: RoutingSnapshot(),
    )


def _draw(rng: random.Random) -> str:
    n = rng.choice([0, 1, 2, 5, 20, MAX_LEN - 1, MAX_LEN, MAX_LEN + 1, 2 * MAX_LEN])
    pieces = [rng.choice(_ALPHABET) for _ in range(n)]
    if rng.random() < 0.3:
        pieces.insert(rng.randint(0, len(pieces)), rng.choice(["|", "\x1f", "\n", " ", "/"]))
    return "".join(pieces)


# Feature: gw28-multi-model-routing, Property 9: trace is content-free and charset-closed
def test_p9_model_name_fuzz() -> None:
    rng = random.Random(0x2809)
    rp = _fallback_rp()
    pattern = ident_pattern(MAX_LEN)
    loop = asyncio.new_event_loop()
    try:
        for i in range(10_000):
            name = _draw(rng)
            accepted = pattern.fullmatch(name) is not None and len(name) <= MAX_LEN
            assert is_valid_model_name(name, MAX_LEN) == accepted
            c = ctx(name, key="k")
            assert (c.requested_model == name and name != "") == accepted
            verdict = rp.preflight(c)
            if not accepted:
                assert verdict == ROUTING_UNKNOWN_MODEL
                assert c.requested_model == ""
            else:
                assert verdict is None
            if i % 25 == 0:  # exercise the full open path on a sample
                decisions: list[RoutingDecision] = []
                events = loop.run_until_complete(
                    drain(rp, ctx(name, key="k", sink=decisions.append))
                )
                for d in decisions:
                    for f in _fields(d):
                        assert (f == "" or pattern.fullmatch(f) is not None) or f in {
                            "p",
                            "dest-p",
                        }
                        assert "\x1f" not in f and "|" not in f and "\n" not in f
                    if not accepted:
                        assert d.refusal_code == ROUTING_UNKNOWN_MODEL
                if not accepted:
                    assert [e.gateway_error for e in events] == [ROUTING_UNKNOWN_MODEL]
                    assert (
                        not decisions
                        or name == ""
                        or name not in "".join(f for d in decisions for f in _fields(d))
                    )
                    # 0 opens: the passthrough client was never asked
                else:
                    # accepted names rank identically on repeat
                    assert rp.preflight(ctx(name, key="k")) is None
    finally:
        loop.close()


def test_rejected_name_opens_nothing() -> None:
    async def main() -> None:
        tl = Timeline()
        pt = cset(cand("p", passthrough=True), passthrough=True)
        client = FakeClient("p", [text("x", final=True)], tl)
        rp = RoutedProvider(
            router=ModelRouter(),
            table=RoutingTable.build({}, fallback_set=pt),
            clients={"p": client},
            bounds=RELAXED,
            max_attempts=1,
            max_ident_len=MAX_LEN,
            snapshot_source=lambda: RoutingSnapshot(),
        )
        for bad in (
            "a|b",
            "a\nb",
            "a\x1fb",
            "a b",
            "meta/llama-3",
            "\x00",
            "ünï",
            "abc\n",
            "",
            "x" * (MAX_LEN + 1),
        ):
            ev = await drain(rp, ctx(bad))
            assert [e.gateway_error for e in ev] == [ROUTING_UNKNOWN_MODEL]
        assert tl.opens() == []
        ev = await drain(rp, ctx("x" * MAX_LEN))
        assert ev[-1].final and ev[-1].gateway_error is None and tl.opens() == ["p"]

    asyncio.run(main())
