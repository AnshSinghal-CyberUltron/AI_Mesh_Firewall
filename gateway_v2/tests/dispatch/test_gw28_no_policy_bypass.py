"""GW28 P6: no policy bypass -- rebind changes only the model; structural AST checks."""

from __future__ import annotations

import ast
import asyncio
import inspect
import random
from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType
from typing import Any

import gateway_v2.dispatch.model_routing as model_routing
import gateway_v2.dispatch.routed_provider as routed_provider
import gateway_v2.dispatch.stall_guard as stall_guard
from gateway_v2.dispatch.model_routing import ModelRouter
from gateway_v2.dispatch.openai_upstream import BODY_OPENAI_KEY
from gateway_v2.dispatch.provider import UpstreamRequest
from gateway_v2.dispatch.routed_provider import rebind
from tests.dispatch._gw28_fakes import FakeClient, Timeline, build_rp, cand, ctx, drain, text

_FORBIDDEN_LAYERS = ("edge", "admit", "detect", "plan", "resolve")


def _rand_value(rng: random.Random, depth: int = 0) -> Any:
    kind = rng.randint(0, 5 if depth < 3 else 2)
    if kind == 0:
        return rng.randint(-5, 5)
    if kind == 1:
        return "".join(rng.choice("abc|\x1f xyz") for _ in range(rng.randint(0, 6)))
    if kind == 2:
        return rng.choice([None, True, False, 1.5])
    if kind == 3:
        return tuple(_rand_value(rng, depth + 1) for _ in range(rng.randint(0, 3)))
    return MappingProxyType(
        {f"k{j}": _rand_value(rng, depth + 1) for j in range(rng.randint(0, 3))}
    )


def _strip_model(body: Mapping[str, object]) -> dict[str, object]:
    return {k: v for k, v in body.items() if k != "model"}


# Feature: gw28-multi-model-routing, Property 6: rebind changes only model (+destination label)
def test_p6a_rebind_changes_only_model() -> None:
    rng = random.Random(0x2806)
    for _ in range(10_000):
        openai = MappingProxyType(
            {"model": "orig", **{f"f{j}": _rand_value(rng) for j in range(rng.randint(0, 5))}}
        )
        other = {f"o{j}": _rand_value(rng) for j in range(rng.randint(0, 3))}
        req = UpstreamRequest(
            destination="logical", body=MappingProxyType({BODY_OPENAI_KEY: openai, **other})
        )
        c = cand("x", upstream="up-x")
        out = rebind(req, c)
        assert out.destination == c.destination
        new_openai = out.body[BODY_OPENAI_KEY]
        assert isinstance(new_openai, Mapping) and new_openai["model"] == "up-x"
        assert _strip_model(new_openai) == _strip_model(openai)
        # every non-model value is the SAME object (identical verified content)
        assert all(new_openai[k] is openai[k] for k in openai if k != "model")
        assert {k: v for k, v in out.body.items() if k != BODY_OPENAI_KEY} == other
        # the original is untouched
        assert openai["model"] == "orig"
        # passthrough leaves the model alone
        pt = rebind(req, cand("p", passthrough=True))
        assert pt.body[BODY_OPENAI_KEY] is openai


def test_rebind_without_rendered_body_returns_request_unchanged() -> None:
    req = UpstreamRequest(destination="d", body={"other": 1})
    assert rebind(req, cand("a")) is req


# Feature: gw28-multi-model-routing, Property 6: every attempt carries the verified body
def test_p6b_every_attempt_body_equals_verified_modulo_model() -> None:
    from gateway_v2.dispatch.stall_guard import StallBounds  # noqa: F401
    from tests.dispatch._gw28_fakes import err, request

    async def main() -> None:
        rng = random.Random(0x2816)
        for i in range(200):
            tl = Timeline()
            seen: list[UpstreamRequest] = []
            clients = {
                n: FakeClient(n, [err("upstream_unavailable")], tl, seen=seen) for n in "abc"
            }
            req = request(extra=f"secret-{rng.random()}")
            await drain(build_rp(clients, max_attempts=3), ctx(key=str(i)), req=req)
            assert len(seen) == 3
            verified = req.body[BODY_OPENAI_KEY]
            assert isinstance(verified, Mapping)
            for s in seen:
                body = s.body[BODY_OPENAI_KEY]
                assert isinstance(body, Mapping)
                assert _strip_model(body) == _strip_model(verified)
                assert body["messages"] is verified["messages"]

    asyncio.run(main())


# Feature: gw28-multi-model-routing, Property 6: ctx missing or wrong type -> zero opens
def test_p6c_missing_or_wrong_context_opens_nothing() -> None:
    async def main() -> None:
        for bad in (None, object(), "ctx", 7, {}):
            tl = Timeline()
            rp = build_rp({"a": FakeClient("a", [text("x", final=True)], tl)})
            ev = await drain(rp, bad)  # type: ignore[arg-type]
            assert [e.gateway_error for e in ev] == ["routing_context_missing"]
            assert tl.opens() == []

    asyncio.run(main())


def _imports(module: Any) -> set[str]:
    tree = ast.parse(Path(inspect.getsourcefile(module) or "").read_text())
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            out.add(node.module)
        elif isinstance(node, ast.Import):
            out.update(a.name for a in node.names)
    return out


# Feature: gw28-multi-model-routing, Property 6: no enforcement parameter, no upward imports
def test_p6d_no_enforcement_parameter_and_no_upward_imports() -> None:
    params = set(inspect.signature(ModelRouter.rank).parameters)
    assert params == {"self", "ctx", "cset", "snapshot", "max_ident_len"}
    for module in (routed_provider, model_routing, stall_guard):
        for name in _imports(module):
            parts = name.split(".")
            assert not (
                len(parts) > 1 and parts[0] == "gateway_v2" and parts[1] in _FORBIDDEN_LAYERS
            ), (module.__name__, name)
        assert "DispatchAuthorization" not in Path(inspect.getsourcefile(module) or "").read_text()


# Feature: gw28-multi-model-routing, Property 6: exactly one candidate .open( call site
def test_p6e_exactly_one_candidate_open_call_site() -> None:
    tree = ast.parse(Path(inspect.getsourcefile(routed_provider) or "").read_text())
    calls = [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "open"
    ]
    assert len(calls) == 1
