# Feature: request-id-uniqueness, Property 1: Bug Condition - distinct requests get distinct ids
# Validates: Requirements 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7, 2.8
"""Property tests for finding F2 (spec ``request-id-uniqueness``, task 3.10).

The two properties of design.md "Property-Based Tests", tagged in-source:

* A, ``Property 1a: distinct requests never share an id``: K (2..8) byte-identical requests on
  each surface (chat stream, chat non-stream, embeddings, RAG, vector, MCP), sequential and via
  ``asyncio.gather``; ids pairwise distinct and ``zs-{org}-[0-9a-f]{24}``; identical content on
  two orgs never shares an id; completing or refunding one request leaves the others' admission
  slots and spends untouched.
* B, ``Property 1b: all phases of one request carry one id``: the id seen by the incident gate,
  ``admission.admit``, ``quota.evaluate``, ``holds.request_id``, the audit record and the
  response (error envelope, ``x-request-id`` header, SSE start) is exactly one value per
  request, across allow / shed / budget refuse / input block / incident refuse, with a fraction
  of cases injecting a mint failure (refusal with no id, zero provider calls, no admit, no spend).

House idiom: seeded ``random.Random``, ``asyncio.run``, no hypothesis. Default 1,000 iterations
per property (override with ``RID_PROPERTY_ITERATIONS``; floored at 100). The failing seed and
iteration are printed and are in the assertion message.

Mint failure and the negative control are injected by replacing the entropy default of
``mint_tenant_request_id`` (``__kwdefaults__["token_hex"]``) through ``monkeypatch``; product code
is never edited. The negative control (``test_negative_control_*``) runs property A against a
constant entropy source and requires it to FAIL, proving the property can detect a collision.

Static test (scope): ``test_no_edge_module_derives_a_request_id_with_hashlib`` parses every
``edge/*.py`` other than ``identity_resolver`` and ``request_ids`` and flags a module only when
``hashlib`` is used inside a function whose name mentions a request id, or the module both
imports ``hashlib`` and builds a ``zs-`` id literal. Modules that import ``hashlib`` for API-key
or catalog hashing (``oauth_routes``, ``incident_catalog``, ``dev_components``) are therefore
fine; their uses are listed in ``_HASHLIB_NON_ID_USES`` and checked to still be non-id uses.
"""

from __future__ import annotations

import ast
import asyncio
import json
import os
import random
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from gateway_v2.admit.admission import AdmissionController
from gateway_v2.admit.grant import Admitted, BudgetVerdict, ShedVerdict
from gateway_v2.domain import posture
from gateway_v2.edge import request_ids
from gateway_v2.edge.request_holds import RequestHolds
from gateway_v2.edge.request_ids import mint_tenant_request_id
from tests.edge import _gw35_harness as dev
from tests.edge import gw31_harness
from tests.edge._lgw29_app import ORG, CountingBudget, Harness, chat
from tests.edge._mcp_fixtures import Broker, Control, mcp_deps, server_row
from tests.edge.test_lgw29_misc_surfaces import EMBED, VECTOR

SEED = 20261106
ITERATIONS = max(100, int(os.environ.get("RID_PROPERTY_ITERATIONS", "1000")))
SURFACES = ("chat_stream", "chat_nonstream", "embeddings", "mcp", "rag", "vector")
_BLOCKED = "key AKIAIOSFODNN7EXAMPLE"
_MCP_LIST = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
_EDGE = Path(__file__).resolve().parents[2] / "gateway_v2" / "edge"


def _org(rng: random.Random) -> str:
    return f"org-{rng.randrange(10**6):06d}"


def _pattern(org: str) -> re.Pattern[str]:
    return re.compile(rf"zs-{re.escape(org)}-[0-9a-f]{{24}}")


def _fail(seed: int, it: int, msg: str) -> AssertionError:
    text = f"{msg} (seed={seed} iteration={it})"
    print(f"FAILING CASE: {text}")
    return AssertionError(text)


def _entropy(monkeypatch: pytest.MonkeyPatch, fn: Callable[[int], str]) -> None:
    kw = mint_tenant_request_id.__kwdefaults__
    assert kw is not None and "token_hex" in kw
    monkeypatch.setitem(kw, "token_hex", fn)


def _broken_entropy(_n: int) -> str:
    raise OSError("entropy source down")


def _short_entropy(_n: int) -> str:
    return "abc"


def _constant_entropy(_n: int) -> str:
    return "0" * 24


# -- shared recorders --------------------------------------------------------------------------


class _Probe:
    """Records every id each phase sees, for one harness."""

    def __init__(self, h: Harness) -> None:
        self.admit: list[str] = []
        self.quota: list[str] = []
        self.gate: list[str] = []
        self.outstanding: set[str] = set()
        self.slot_errors: list[str] = []
        self.spend_by_id: dict[str, int] = {}
        self.real: AdmissionController | None = None
        adm, quo = h.admission, h.budget
        a_orig, c_orig, q_orig = adm.admit, adm.complete, quo.evaluate

        async def admit(owner_id: str, request_id: str, enqueued_at: float) -> Any:
            self.admit.append(request_id)
            if self.real is not None:
                verdict = await self.real.admit(owner_id, request_id, enqueued_at)
                if isinstance(verdict, Admitted):
                    self.outstanding.add(request_id)
                return verdict
            return await a_orig(owner_id, request_id, enqueued_at)

        def complete(request_id: str) -> None:
            if self.real is not None:
                if request_id not in self.outstanding:
                    self.slot_errors.append(f"complete of unknown {request_id}")
                self.outstanding.discard(request_id)
                self.real.complete(request_id)
                depth = self.real.queue_report(now=0.0).per_queue["request"][0]
                if depth != len(self.outstanding):
                    self.slot_errors.append(f"slots {depth} != outstanding {len(self.outstanding)}")
            c_orig(request_id)

        async def evaluate(cost: int, **kw: Any) -> Any:
            self.quota.append(kw["request_id"])
            self.spend_by_id[kw["request_id"]] = self.spend_by_id.get(kw["request_id"], 0) + cost
            return await q_orig(cost, **kw)

        adm.admit = admit  # type: ignore[method-assign]
        adm.complete = complete  # type: ignore[method-assign]
        quo.evaluate = evaluate  # type: ignore[method-assign]
        for name in ("decide", "decide_org"):
            self._wrap_gate(h, name)

    def _wrap_gate(self, h: Harness, name: str) -> None:
        original = getattr(h.rig.gate, name)

        def wrapped(*args: Any, **kwargs: Any) -> Any:
            rid = kwargs.get("request_id", args[1] if len(args) > 1 else "")
            if isinstance(rid, str):
                self.gate.append(rid)
            return original(*args, **kwargs)

        setattr(h.rig.gate, name, wrapped)


def _sender(h: Harness, path: str, body: dict[str, object]) -> Callable[[], Any]:
    return lambda: h.call(path, body)


def _admission_with_real_slots(probe: _Probe) -> None:
    from tests.edge.test_request_id_preservation import _controller as make

    probe.real = make()


def _response_ids(headers: Any, body: bytes) -> set[str]:
    found: set[str] = set()
    header = headers.get("x-request-id")
    if header:
        found.add(header)
    try:
        parsed = json.loads(body)
    except ValueError:
        return found
    if isinstance(parsed, dict):
        err = parsed.get("error")
        if isinstance(err, dict) and err.get("request_id"):
            found.add(err["request_id"])
        zs = parsed.get("zeroshield")
        if isinstance(zs, dict) and zs.get("request_id"):
            found.add(zs["request_id"])
    return found


def _mcp_harness(org: str) -> Harness:
    return Harness(mcp=mcp_deps(Broker(), Control([server_row("search")])), org=org)


def _harness_call(surface: str) -> tuple[str, dict[str, object], Callable[[str], Harness]]:
    if surface == "chat_stream":
        return "/v1/chat/completions", chat(stream=True), lambda org: Harness(org=org)
    if surface == "chat_nonstream":
        return "/v1/chat/completions", chat(stream=False), lambda org: Harness(org=org)
    if surface == "embeddings":
        return "/v1/embeddings", dict(EMBED), lambda org: Harness(VECTOR, org=org)
    return "/v1/mcp/search", dict(_MCP_LIST), _mcp_harness


async def _fire(send: Callable[[], Any], k: int, gather: bool) -> list[Any]:
    if gather:
        return list(await asyncio.gather(*(send() for _ in range(k))))
    return [await send() for _ in range(k)]


# -- dev boot app for RAG / vector (single org: dev-org) ------------------------------------------


class _DevApp:
    """RAG and vector go through the dev boot path; one app per test run, ids sunk per call."""

    def __init__(self, tmp: Path) -> None:
        plan = tmp / "plan.json"
        gw31_harness.write_plan(plan)
        self.admitted: list[str] = []
        self.quota_spent: list[int] = []
        sink, spent = self.admitted, self.quota_spent

        class _Admission:
            def __init__(self, inner: Any) -> None:
                self._inner = inner

            async def admit(self, owner_id: str, request_id: str, enqueued_at: float) -> Any:
                sink.append(request_id)
                return await self._inner.admit(owner_id, request_id, enqueued_at)

            def __getattr__(self, name: str) -> object:
                return getattr(self._inner, name)

        class _Quota:
            def __init__(self, inner: Any) -> None:
                self._inner = inner

            async def evaluate(self, cost: int, **kw: Any) -> Any:
                spent.append(cost)
                return await self._inner.evaluate(cost, **kw)

            def __getattr__(self, name: str) -> object:
                return getattr(self._inner, name)

        def mutate(deps: dict[str, object]) -> None:
            deps["admission"] = _Admission(deps["admission"])
            deps["quota"] = _Quota(deps["quota"])

        self.app, _deps = gw31_harness.build_app(plan, mutate=mutate)

    def path_body(self, surface: str) -> tuple[str, dict[str, object]]:
        if surface == "rag":
            return "/v1/rag/query", {"collection": "docs", "query": "same query"}
        return "/v1/vector/query", {"collection": "docs", "vector": [0.1, 0.2]}


@pytest.fixture(scope="module")
def dev_app(tmp_path_factory: pytest.TempPathFactory) -> _DevApp:
    return _DevApp(tmp_path_factory.mktemp("rid_prop_dev"))


# -- Property A --------------------------------------------------------------------------------


def _assert_ids(ids: list[str], k: int, org: str, label: str, seed: int, it: int) -> None:
    pat = _pattern(org)
    if len(ids) != k:
        raise _fail(seed, it, f"{label}: expected {k} ids, saw {len(ids)}: {ids}")
    bad = [i for i in ids if not pat.fullmatch(i)]
    if bad:
        raise _fail(seed, it, f"{label}: ids not zs-{org}-<24 hex>: {bad}")
    if len(set(ids)) != k:
        raise _fail(seed, it, f"{label}: {k} identical requests got {len(set(ids))} id(s)")


def _property_a(seed: int, iterations: int, dev_app: _DevApp | None) -> None:
    rng = random.Random(seed)
    for it in range(iterations):
        surface = rng.choice(SURFACES)
        k = rng.randrange(2, 9)
        gather = rng.random() < 0.5
        if surface in ("rag", "vector"):
            if dev_app is None:
                continue
            path, body = dev_app.path_body(surface)
            dev_app.admitted.clear()

            async def send(_p: str = path, _b: dict[str, object] = body) -> Any:
                return await gw31_harness.call(dev_app.app, "POST", _p, _b)

            # the dev backend may answer 502 (no real vector store); the id is minted and
            # admitted before that, which is all this property observes.
            asyncio.run(_fire(send, k, gather))
            _assert_ids(list(dev_app.admitted), k, gw31_harness.ORG, surface, seed, it)
            continue

        path, body, make = _harness_call(surface)
        # the MCP control fixture is bound to the default org (a random org gets a 403)
        orgs = [ORG if surface == "mcp" else _org(rng)]
        if (
            surface != "mcp" and rng.random() < 0.3
        ):  # identical content on a second org (tenant scoping)
            second = _org(rng)
            if second != orgs[0]:
                orgs.append(second)
        seen: dict[str, list[str]] = {}
        for org in orgs:
            h = make(org)
            probe = _Probe(h)
            _admission_with_real_slots(probe)
            results = asyncio.run(_fire(_sender(h, path, body), k, gather))
            if any(r.status != 200 for r in results):
                raise _fail(seed, it, f"{surface}: non-200 {[r.status for r in results]}")
            _assert_ids(probe.admit, k, org, f"{surface} admission", seed, it)
            _assert_ids(probe.quota, k, org, f"{surface} quota", seed, it) if probe.quota else None
            audit = {r.request_id for r in h.records}
            # embeddings emit no audit rows today (task 3.11 owns that explore assertion)
            if surface != "embeddings" and len(audit) != k:
                raise _fail(seed, it, f"{surface}: {k} requests, {len(audit)} audit id(s)")
            if surface == "chat_stream":
                hdr = [r.headers["x-request-id"] for r in results]
                _assert_ids(hdr, k, org, "stream x-request-id", seed, it)
                if set(hdr) != set(probe.admit):
                    raise _fail(seed, it, "SSE start id differs from the admitted id")
            # completing one request must leave the others' slots and spends untouched (2.7)
            if probe.slot_errors:
                raise _fail(seed, it, f"{surface}: slot accounting {probe.slot_errors}")
            if probe.outstanding or h.admission.completed != k:
                raise _fail(seed, it, f"{surface}: {h.admission.completed}/{k} completed")
            if probe.quota and (len(probe.spend_by_id) != k or h.budget.refunded != 0):
                raise _fail(seed, it, f"{surface}: spends {probe.spend_by_id}")
            seen[org] = probe.admit
        if len(orgs) == 2:
            a, b = orgs
            if set(seen[a]) & set(seen[b]) or any(b in i for i in seen[a]):
                raise _fail(seed, it, f"{surface}: ids shared or leaked across orgs {a}/{b}")


def test_property_a_distinct_requests_never_share_an_id(dev_app: _DevApp) -> None:
    # Feature: request-id-uniqueness, Property 1a: distinct requests never share an id
    _property_a(SEED, ITERATIONS, dev_app)


def _blocked_post(app: object, records: list[Any], content: str, stream: bool) -> Any:
    return dev.post(app, records, content=content, stream=stream)


def test_property_a_refunding_a_blocked_request_leaves_the_others_spend_alone() -> None:
    # Feature: request-id-uniqueness, Property 1a: distinct requests never share an id
    rng = random.Random(SEED + 1)
    iterations = max(100, ITERATIONS // 5)
    budget, admission = CountingBudget(), None
    from tests.edge._lgw29_app import CountingAdmission

    admission = CountingAdmission()

    def mutate(deps: dict[str, object]) -> None:
        deps["quota"] = budget
        deps["admission"] = admission

    app, records, _ = dev.build_app(dev.ScriptedProvider(dev.ok_events()), mutate_deps=mutate)
    for it in range(iterations):
        k = rng.randrange(2, 9)
        gather = rng.random() < 0.5
        spent0, refunds0 = budget.spent, len(budget.refund_calls)

        async def go() -> list[Any]:
            contents = [_BLOCKED] * k
            contents.insert(rng.randrange(k + 1), "hello")
            sends = [_blocked_post(app, records, c, False) for c in contents]
            if gather:
                return list(await asyncio.gather(*sends))
            return [await s for s in sends]

        results = asyncio.run(go())
        ok = [r for r in results if r.status == 200]
        if len(ok) != 1:
            raise _fail(SEED + 1, it, f"expected one allowed request, got {len(ok)}")
        refunds = budget.refund_calls[refunds0:]
        if len(refunds) != k or len({rid for _c, rid, _o in refunds}) != k:
            raise _fail(SEED + 1, it, f"{k} blocked requests got refunds {refunds}")
        allowed_ids = {r.request_id for r in ok[0].records}
        if len(allowed_ids) != 1 or any(rid in allowed_ids for _c, rid, _o in refunds):
            raise _fail(SEED + 1, it, "a refund was keyed on the allowed request's id")
        spent = budget.spent - spent0
        refunded = sum(c for c, _r, _o in refunds)
        if spent - refunded <= 0 or spent - refunded >= spent:
            raise _fail(SEED + 1, it, f"net spend {spent - refunded} of {spent}")


def test_negative_control_a_fails_when_the_mint_returns_a_constant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Property A must detect a collision: with a constant entropy source it has to fail."""
    _entropy(monkeypatch, _constant_entropy)
    with pytest.raises(AssertionError, match=r"seed=\d+ iteration=\d+"):
        _property_a(SEED, 20, None)


# -- Property B --------------------------------------------------------------------------------

OUTCOMES = ("allow", "allow_stream", "shed", "budget_refuse", "incident_refuse", "input_block")


class _CaptureHolds:
    """Records ``RequestHolds.request_id`` at release time (the id the holds carried)."""

    def __init__(self) -> None:
        self.ids: list[str] = []


def _property_b(
    seed: int, iterations: int, monkeypatch: pytest.MonkeyPatch, mint_failure_rate: float
) -> None:
    rng = random.Random(seed)
    holds_sink = _CaptureHolds()
    original_release = RequestHolds.release

    def release(self: RequestHolds, *args: Any, **kwargs: Any) -> Any:
        holds_sink.ids.append(self.request_id or "")
        return original_release(self, *args, **kwargs)

    monkeypatch.setattr(RequestHolds, "release", release)
    for it in range(iterations):
        outcome = rng.choice(OUTCOMES)
        org = _org(rng)
        mint_fails = rng.random() < mint_failure_rate
        holds_sink.ids.clear()
        with monkeypatch.context() as m:
            if mint_fails:
                _entropy(m, rng.choice((_broken_entropy, _short_entropy)))
            if outcome == "input_block":
                _case_input_block(seed, it, mint_fails, holds_sink)
            else:
                _case_harness(seed, it, outcome, org, mint_fails, holds_sink)


def _case_harness(
    seed: int, it: int, outcome: str, org: str, mint_fails: bool, holds: _CaptureHolds
) -> None:
    h = Harness(org=org)
    probe = _Probe(h)
    stream = outcome == "allow_stream"
    if outcome == "shed":

        async def shed(owner_id: str, request_id: str, enqueued_at: float) -> Any:
            probe.admit.append(request_id)
            return ShedVerdict(posture.OVERLOAD_SHED, 1.5, False, request_id)

        h.admission.admit = shed  # type: ignore[method-assign]
    elif outcome == "budget_refuse":

        async def refuse(cost: int, **kw: Any) -> Any:
            probe.quota.append(kw["request_id"])
            return BudgetVerdict(posture.BUDGET_UNAVAILABLE, 1.5, False, kw["request_id"])

        h.budget.evaluate = refuse  # type: ignore[method-assign]
    elif outcome == "incident_refuse":
        h.engage("global")
    res = asyncio.run(h.call("/v1/chat/completions", chat(stream=stream)))

    if mint_fails:
        _assert_mint_refusal(seed, it, res.status, res.headers, res.body)
        _assert_nothing_happened(seed, it, h.provider.open_calls, probe, h.budget.spent, h.records)
        if any(holds.ids):
            raise _fail(seed, it, f"holds carried an id after a mint failure: {holds.ids}")
        return

    expected_status = {
        "allow": 200,
        "allow_stream": 200,
        "shed": 503,
        "budget_refuse": 503,
        "incident_refuse": 503,
    }[outcome]
    if res.status != expected_status:
        raise _fail(seed, it, f"{outcome}: status {res.status}")
    seen: dict[str, set[str]] = {
        "admission": set(probe.admit),
        "quota": set(probe.quota),
        "gate": set(probe.gate),
        "holds": {i for i in holds.ids if i},
        "audit": {r.request_id for r in h.records},
        "response": _response_ids(res.headers, res.body),
    }
    required = {
        "allow": ("admission", "quota", "gate", "holds", "audit"),
        "allow_stream": ("admission", "quota", "gate", "holds", "audit", "response"),
        "shed": ("admission", "holds", "response"),
        "budget_refuse": ("admission", "quota", "holds", "response"),
        "incident_refuse": ("gate", "response"),
    }[outcome]
    missing = [name for name in required if not seen[name]]
    if missing:
        raise _fail(seed, it, f"{outcome}: phases never observed an id: {missing} ({seen})")
    union = set().union(*seen.values())
    if len(union) != 1:
        raise _fail(seed, it, f"{outcome}: phases disagree on the id: {seen}")
    if not _pattern(org).fullmatch(next(iter(union))):
        raise _fail(seed, it, f"{outcome}: id {union} is not zs-{org}-<24 hex>")


def _case_input_block(seed: int, it: int, mint_fails: bool, holds: _CaptureHolds) -> None:
    from tests.edge._lgw29_app import CountingAdmission

    budget, admission = CountingBudget(), CountingAdmission()
    seen: dict[str, set[str]] = {"admission": set(), "quota": set()}

    class _A:
        async def admit(self, owner_id: str, request_id: str, enqueued_at: float) -> Any:
            seen["admission"].add(request_id)
            return await admission.admit(owner_id, request_id, enqueued_at)

        def complete(self, request_id: str) -> None:
            admission.complete(request_id)

    class _Q:
        async def evaluate(self, cost: int, **kw: Any) -> Any:
            seen["quota"].add(kw["request_id"])
            return await budget.evaluate(cost, **kw)

        def refund(self, cost: int, **kw: Any) -> None:
            budget.refund(cost, **kw)

    def mutate(deps: dict[str, object]) -> None:
        deps["admission"] = _A()
        deps["quota"] = _Q()

    provider = dev.ScriptedProvider(dev.ok_events())
    app, records, _ = dev.build_app(provider, mutate_deps=mutate)
    res = dev.run(dev.post(app, records, content=_BLOCKED))
    if mint_fails:
        _assert_mint_refusal(seed, it, res.status, httpx_like(res.headers), res.body)
        if provider.opened or admission.admitted or budget.spent or res.records:
            raise _fail(seed, it, "input block + mint failure reached a phase")
        return
    if res.status < 400 or provider.opened:
        raise _fail(seed, it, f"input_block: status {res.status}, provider {provider.opened}")
    ids = {
        "admission": seen["admission"],
        "quota": seen["quota"],
        "audit": {r.request_id for r in res.records},
        "response": _response_ids(httpx_like(res.headers), res.body),
        "holds": {i for i in holds.ids if i},
        "refund": {rid for _c, rid, _o in budget.refund_calls},
    }
    missing = [
        n for n in ("admission", "quota", "audit", "response", "holds", "refund") if not ids[n]
    ]
    if missing:
        raise _fail(seed, it, f"input_block: phases never observed an id: {missing} ({ids})")
    union = set().union(*ids.values())
    if len(union) != 1:
        raise _fail(seed, it, f"input_block: phases disagree on the id: {ids}")


class httpx_like:  # noqa: N801 - a tiny Mapping with .get for dev.Result's plain dict headers
    def __init__(self, headers: dict[str, str]) -> None:
        self._h = {k.lower(): v for k, v in headers.items()}

    def get(self, name: str, default: Any = None) -> Any:
        return self._h.get(name.lower(), default)


def _assert_mint_refusal(seed: int, it: int, status: int, headers: Any, body: bytes) -> None:
    if status != 503:
        raise _fail(seed, it, f"mint failure: status {status}, expected 503")
    parsed = json.loads(body)
    err = parsed.get("error", {})
    if err.get("code") != posture.REQUEST_ID_UNAVAILABLE and err.get("type") is None:
        raise _fail(seed, it, f"mint failure: body {parsed}")
    if headers.get("x-request-id") is not None or err.get("request_id"):
        raise _fail(seed, it, f"mint failure still rendered an id: {headers} {parsed}")
    if _response_ids(headers, body):
        raise _fail(seed, it, "mint failure response carries an id")


def _assert_nothing_happened(
    seed: int, it: int, opened: int, probe: _Probe, spent: int, records: list[Any]
) -> None:
    if opened or probe.admit or probe.quota or spent:
        raise _fail(
            seed,
            it,
            f"mint failure reached provider={opened} admit={probe.admit} quota={probe.quota} "
            f"spent={spent}",
        )
    if any(r.request_id for r in records if r.outcome == "allow"):
        raise _fail(seed, it, "mint failure produced an allow audit row")


def test_property_b_all_phases_of_one_request_carry_one_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Feature: request-id-uniqueness, Property 1b: all phases of one request carry one id
    _property_b(SEED + 2, ITERATIONS, monkeypatch, mint_failure_rate=0.2)


# -- mint failure on every surface (2.8) -----------------------------------------------------------


@pytest.mark.parametrize("surface", ["chat_stream", "chat_nonstream", "embeddings", "mcp"])
def test_mint_failure_refuses_without_an_id_on_every_harness_surface(
    surface: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Feature: request-id-uniqueness, Property 1b: all phases of one request carry one id
    rng = random.Random(SEED + 3)
    path, body, make = _harness_call(surface)
    for it in range(max(100, ITERATIONS // 10)):
        with monkeypatch.context() as m:
            _entropy(m, rng.choice((_broken_entropy, _short_entropy)))
            h = make(ORG if surface == "mcp" else _org(rng))
            probe = _Probe(h)
            res = asyncio.run(h.call(path, body))
        if surface == "mcp":
            # JSON-RPC surface: the refusal is a transport error; the contract is still no id,
            # no provider/broker call, no admit and no spend.
            if _response_ids(res.headers, res.body):
                raise _fail(SEED + 3, it, f"mcp refusal carries an id: {res.text}")
        else:
            _assert_mint_refusal(SEED + 3, it, res.status, res.headers, res.body)
        _assert_nothing_happened(
            SEED + 3, it, h.provider.open_calls, probe, h.budget.spent, h.records
        )


@pytest.mark.parametrize("surface", ["rag", "vector"])
def test_mint_failure_refuses_without_an_id_on_rag_and_vector(
    surface: str, dev_app: _DevApp, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Feature: request-id-uniqueness, Property 1b: all phases of one request carry one id
    rng = random.Random(SEED + 4)
    path, body = dev_app.path_body(surface)
    for it in range(max(100, ITERATIONS // 10)):
        dev_app.admitted.clear()
        dev_app.quota_spent.clear()
        with monkeypatch.context() as m:
            _entropy(m, rng.choice((_broken_entropy, _short_entropy)))
            status, _parsed, raw = asyncio.run(gw31_harness.call(dev_app.app, "POST", path, body))
        if status != 503 or _response_ids({}, raw):
            raise _fail(SEED + 4, it, f"{surface}: status {status} body {raw!r}")
        if dev_app.admitted or dev_app.quota_spent:
            raise _fail(SEED + 4, it, f"{surface}: admitted or spent after a mint failure")


# -- static: no edge module derives a request id with hashlib ------------------------------

_ALLOWED_MODULES = frozenset({"identity_resolver", "request_ids"})
# hashlib uses that are NOT request ids: API-key hashing, catalog digest, dev key comparison.
_HASHLIB_NON_ID_USES = {
    "oauth_routes": "key_hash = hashlib.sha256(raw_key...)",
    "incident_catalog": "hashlib.sha256(text...) catalog digest",
    "dev_components": "hashlib.sha256(expected...) dev key comparison",
}


def _hashlib_calls(tree: ast.AST) -> list[tuple[str, int]]:
    """(enclosing function name or '<module>', line) of every ``hashlib.<x>`` reference."""
    found: list[tuple[str, int]] = []

    def visit(node: ast.AST, scope: str) -> None:
        for child in ast.iter_child_nodes(node):
            inner = (
                child.name if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef) else scope
            )
            if (
                isinstance(child, ast.Attribute)
                and isinstance(child.value, ast.Name)
                and child.value.id == "hashlib"
            ):
                found.append((scope, child.lineno))
            visit(child, inner)

    visit(tree, "<module>")
    return found


def test_no_edge_module_derives_a_request_id_with_hashlib() -> None:
    """Scoped to request-id derivation, not to every hashlib import (see module docstring)."""
    offenders: list[str] = []
    users: set[str] = set()
    for path in sorted(_EDGE.rglob("*.py")):
        module = path.stem
        if module in _ALLOWED_MODULES:
            continue
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        calls = _hashlib_calls(tree)
        if calls:
            users.add(module)
        for scope, line in calls:
            if re.search(r"request_id|_rid\b|^rid$|mint", scope):
                offenders.append(f"{path.name}:{line} hashlib in {scope}()")
        if calls and re.search(r'f?["\']zs-\{', source):
            offenders.append(f"{path.name}: imports hashlib and builds a zs-{{...}} id")
    assert not offenders, f"request ids derived with hashlib: {offenders}"
    # the exempt list must stay accurate: every non-id hashlib user is named, none is a new one
    assert users <= set(_HASHLIB_NON_ID_USES), (
        f"new hashlib user(s) in edge: {sorted(users - set(_HASHLIB_NON_ID_USES))}; "
        "confirm they do not derive a request id and list them in _HASHLIB_NON_ID_USES"
    )


def test_request_ids_module_has_no_content_parameter_and_no_hashlib() -> None:
    tree = ast.parse((_EDGE / "request_ids.py").read_text(encoding="utf-8"))
    assert not _hashlib_calls(tree)
    imports = [n for n in ast.walk(tree) if isinstance(n, ast.Import | ast.ImportFrom)]
    assert not any("hashlib" in ast.dump(n) for n in imports)
    code = request_ids.mint_tenant_request_id.__code__
    assert code.co_varnames[: code.co_argcount] == ("org_id",)
