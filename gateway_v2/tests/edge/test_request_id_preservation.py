# Feature: request-id-uniqueness, Property 2: Preservation - behaviour outside id derivation
# Validates: Requirements 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 3.8, 3.10, 3.11 (3.9: see note)
"""Preservation tests for finding F2 (spec ``request-id-uniqueness``, task 2).

Observation-first: every assertion here was observed on the UNFIXED tree and passes there. The
tests never depend on two requests sharing, or not sharing, an id, so they hold unchanged once
the content hash is replaced by a per-request id. Ids are normalised to their form
(``zs-{org}-<hex>`` or ``zs-<24 hex>``); no test pins an id VALUE.

Requirement 3.9 (the control-plane ``proxy_chat`` path) lives in ``gateway/``, outside this
package. Its baseline is ``gateway/ai_mesh_gateway/tests/test_pipeline_request_id_conflation.py``
and is run separately (see the task 2 report); ``test_this_change_leaves_gateway_untouched``
below only checks the working tree.

House idiom: seeded ``random.Random``, ``asyncio.run``, no hypothesis, at least 100 iterations
per property; the seed and iteration are in each assertion message.
"""

from __future__ import annotations

import asyncio
import json
import random
import re
import subprocess
from pathlib import Path
from typing import Any

import httpx
import pytest

from gateway_v2.admit.admission import AdmissionController
from gateway_v2.admit.codel import CoDelParams
from gateway_v2.admit.grant import Admitted, BudgetVerdict, ShedVerdict
from gateway_v2.admit.metrics import AdmissionMetrics
from gateway_v2.admit.quota import AdmissionBounds
from gateway_v2.admit.supervisor import WorkerSupervisor
from gateway_v2.audit.record import AuditPhase, AuditRecord
from gateway_v2.domain import posture
from gateway_v2.edge.request_holds import RequestHolds
from tests.edge import _gw35_harness as dev
from tests.edge import test_gw35_golden_capture as golden
from tests.edge._lgw29_app import ORG, CountingAdmission, CountingBudget, Harness

SEED = 20261105
ITERATIONS = 100

_TENANT_ID = re.compile(r"zs-(?P<org>.+)-(?P<hex>[0-9a-f]{24})")
_PRE_ID = re.compile(r"zs-[0-9a-f]{24}")
_BLOCKED_PROMPT = "key AKIAIOSFODNN7EXAMPLE"


def _ctx(seed: int, it: int) -> str:
    return f"(seed={seed} iteration={it})"


def _tenant_id(rid: str, org: str, ctx: str) -> None:
    """The id is ``zs-{org}-<hex>`` for exactly this tenant (3.1)."""
    m = _TENANT_ID.fullmatch(rid)
    assert m is not None, f"id {rid!r} is not of the form zs-{{org}}-<hex> {ctx}"
    assert m.group("org") == org, f"id {rid!r} is not scoped to {org!r} {ctx}"


def _text(prompt: str) -> dict[str, object]:
    return {"model": "gpt-test", "stream": False, "messages": [{"role": "user", "content": prompt}]}


def _prompt(rng: random.Random) -> str:
    return "p" + "".join(rng.choice("abcdefghij klmnop") for _ in range(rng.randrange(3, 40)))


def _org(rng: random.Random) -> str:
    return f"org-{rng.randrange(10**6):06d}"


def _run(h: Harness, path: str, body: dict[str, object]) -> Any:
    return asyncio.run(h.call(path, body))


def _body_ids(raw: bytes) -> set[str]:
    """Every request id the response body carries (error envelope and/or zeroshield echo)."""
    parsed = json.loads(raw)
    found: set[str] = set()
    err = parsed.get("error")
    if isinstance(err, dict) and err.get("request_id"):
        found.add(err["request_id"])
    zs = parsed.get("zeroshield")
    if isinstance(zs, dict) and zs.get("request_id"):
        found.add(zs["request_id"])
    return found


# -- 3.1 prefix and tenant scope ----------------------------------------------------------


def test_ids_are_tenant_scoped_and_never_contain_another_org() -> None:
    rng = random.Random(SEED)
    for it in range(ITERATIONS):
        ctx = _ctx(SEED, it)
        org_a, org_b = _org(rng), _org(rng)
        if org_a == org_b:
            continue
        ids: dict[str, str] = {}
        for org in (org_a, org_b):
            h = Harness(org=org)
            seen: list[str] = []
            original = h.admission.admit

            async def admit(  # type: ignore[no-untyped-def]
                owner_id: str, request_id: str, enqueued_at: float, _o=original, _s=seen
            ) -> Any:
                _s.append(request_id)
                return await _o(owner_id, request_id, enqueued_at)

            h.admission.admit = admit  # type: ignore[method-assign]
            assert _run(h, "/v1/chat/completions", _text(_prompt(rng))).status == 200
            assert len(seen) == 1 and h.records, ctx
            rid = seen[0]
            _tenant_id(rid, org, ctx)
            assert {r.request_id for r in h.records} == {rid}, f"audit differs from admission {ctx}"
            assert {r.org_id for r in h.records} == {org}, ctx
            ids[org] = rid
        assert org_b not in ids[org_a] and org_a not in ids[org_b], ctx
        assert ids[org_a] != ids[org_b], ctx


# -- 3.2 client X-Request-ID is never canonical -------------------------------------------


def test_client_x_request_id_is_never_the_canonical_id() -> None:
    rng = random.Random(SEED + 1)
    for it in range(ITERATIONS):
        ctx = _ctx(SEED + 1, it)
        org = _org(rng)
        pinned = f"pinned-{rng.randrange(10**6)}"
        h = Harness(org=org)
        stream = rng.random() < 0.5
        body = _text(_prompt(rng))
        body["stream"] = stream

        async def go() -> httpx.Response:
            transport = httpx.ASGITransport(app=h.app)  # type: ignore[arg-type]
            async with httpx.AsyncClient(transport=transport, base_url="http://p.test") as c:
                return await c.post(
                    "/v1/chat/completions",
                    content=json.dumps(body).encode(),
                    headers={
                        "authorization": "Bearer test",
                        "content-type": "application/json",
                        "x-request-id": pinned,
                    },
                )

        response = asyncio.run(go())
        assert response.status_code == 200, ctx
        assert h.records, ctx
        for record in h.records:
            assert pinned not in record.request_id, f"client header became the id {ctx}"
            _tenant_id(record.request_id, org, ctx)
        header = response.headers.get("x-request-id")
        if header is not None:
            assert header != pinned, ctx
        assert pinned not in response.text, f"client header echoed into the body {ctx}"


# -- 3.3 pre-id refusals keep a random content-free id ------------------------------------


def test_identity_and_plan_refusals_render_a_random_content_free_id() -> None:
    from gateway_v2.edge.chat_deps import unresolved_snapshot_source

    rng = random.Random(SEED + 2)
    cases = (
        ("invalid_key", 401, {"auth": "Bearer nope"}, None),
        ("missing_key", 401, {"auth": None}, None),
        (
            "plan_unavailable",
            None,
            {},
            lambda d: d.update(snapshot_source=unresolved_snapshot_source),
        ),
    )
    for name, status, kw, mutate in cases:
        app, records, _ = dev.build_app(dev.ScriptedProvider(dev.ok_events()), mutate_deps=mutate)
        seen: set[str] = set()
        for it in range(ITERATIONS):
            ctx = f"{name} {_ctx(SEED + 2, it)}"
            prompt = _prompt(rng)
            res = dev.run(dev.post(app, records, content=prompt, **kw))
            if status is not None:
                assert res.status == status, ctx
            else:
                assert res.status >= 500, ctx
            ids = _body_ids(res.body)
            assert len(ids) == 1, f"body carries {ids} {ctx}"
            (rid,) = ids
            assert _PRE_ID.fullmatch(rid), f"{rid!r} is not zs-<24 hex> {ctx}"
            # observed: identity refusals set x-request-id; the plan refusal carries the id in
            # the body only. Whenever the header is present it must equal the body id.
            if name != "plan_unavailable":
                assert res.headers["x-request-id"] == rid, ctx
            else:
                assert res.headers.get("x-request-id", rid) == rid, ctx
            assert prompt.replace(" ", "") not in rid, ctx
            seen.add(rid)
        # random per refusal: many refusals, many ids (no content, no per-org constant)
        assert len(seen) == ITERATIONS, f"{name}: {len(seen)} distinct ids for {ITERATIONS}"


# -- 3.4 moderations keep their own per-request id ----------------------------------------


def test_moderations_keep_their_own_random_tenant_scoped_id() -> None:
    rng = random.Random(SEED + 3)
    for it in range(ITERATIONS):
        ctx = _ctx(SEED + 3, it)
        org = _org(rng)
        h = Harness(org=org)
        h.engage("global")  # the only moderations path that surfaces an id is the org gate refusal
        res = _run(h, "/v1/moderations", {"input": _prompt(rng)})
        assert res.status == 503, ctx
        rid = res.headers["x-request-id"]
        _tenant_id(rid, org, ctx)
        assert _body_ids(res.body) == {rid}, ctx
        assert h.provider.open_calls == 0, ctx
    # a second refusal for byte-identical input gets its own uuid-based id (already unique today)
    h = Harness(org=ORG)
    h.engage("global")
    first = _run(h, "/v1/moderations", {"input": "same"}).headers["x-request-id"]
    second = _run(h, "/v1/moderations", {"input": "same"}).headers["x-request-id"]
    assert first != second


# -- 3.5 one request, one id, echoed by every verdict -------------------------------------


def test_one_request_echoes_one_id_in_every_phase() -> None:
    rng = random.Random(SEED + 4)
    for it in range(ITERATIONS):
        ctx = _ctx(SEED + 4, it)
        org = _org(rng)
        h = Harness(org=org)
        seen_admit: list[str] = []
        seen_budget: list[str] = []
        a_orig, b_orig = h.admission.admit, h.budget.evaluate

        async def admit(owner_id: str, request_id: str, enqueued_at: float) -> Any:
            seen_admit.append(request_id)
            return await a_orig(owner_id, request_id, enqueued_at)

        async def evaluate(cost: int, **kw: Any) -> Any:
            seen_budget.append(kw["request_id"])
            return await b_orig(cost, **kw)

        h.admission.admit = admit  # type: ignore[method-assign]
        h.budget.evaluate = evaluate  # type: ignore[method-assign]
        stream = rng.random() < 0.5
        body = _text(_prompt(rng))
        body["stream"] = stream
        res = _run(h, "/v1/chat/completions", body)
        assert res.status == 200, ctx
        ids = set(seen_admit) | set(seen_budget) | {r.request_id for r in h.records}
        if stream:
            ids.add(res.headers["x-request-id"])  # SSE start header
        else:
            ids |= _body_ids(res.body)
        assert len(ids) == 1, f"phases disagree: {sorted(ids)} {ctx}"
        _tenant_id(next(iter(ids)), org, ctx)
        assert len(seen_admit) == 1 and len(seen_budget) == 1, ctx


def test_incident_refusal_echoes_one_id_in_gate_body_header_and_audit() -> None:
    rng = random.Random(SEED + 5)
    for it in range(ITERATIONS):
        ctx = _ctx(SEED + 5, it)
        org = _org(rng)
        h = Harness(org=org)
        h.engage(rng.choice(("global", f"org:{org}")))
        res = _run(h, "/v1/chat/completions", _text(_prompt(rng)))
        assert res.status == 503, ctx
        rid = res.headers["x-request-id"]
        _tenant_id(rid, org, ctx)
        assert _body_ids(res.body) == {rid}, ctx
        assert {r.request_id for r in h.records} <= {rid}, ctx
        assert h.provider.open_calls == 0 and h.budget.net == 0, ctx


class _Shed:
    def __init__(self) -> None:
        self.seen: list[str] = []

    async def admit(self, owner_id: str, request_id: str, enqueued_at: float) -> object:
        self.seen.append(request_id)
        return ShedVerdict(posture.OVERLOAD_SHED, 1.5, False, request_id)

    def complete(self, request_id: str) -> None:
        return None


class _Refuse:
    def __init__(self, code: str) -> None:
        self.code = code
        self.seen: list[str] = []

    async def evaluate(self, cost: int, *, request_id: str, **kw: object) -> object:
        self.seen.append(request_id)
        return BudgetVerdict(self.code, 1.5, False, request_id)

    def refund(self, *a: object, **k: object) -> None:
        return None


@pytest.mark.parametrize("kind", ["shed", "rate_limited", "budget_unavailable"])
def test_shed_and_budget_verdicts_echo_the_request_id(kind: str) -> None:
    rng = random.Random(SEED + 6)
    stub: Any = _Shed() if kind == "shed" else _Refuse(
        posture.RATE_LIMITED if kind == "rate_limited" else posture.BUDGET_UNAVAILABLE
    )

    def mutate(deps: dict[str, object]) -> None:
        deps["admission" if kind == "shed" else "quota"] = stub

    app, records, _ = dev.build_app(dev.ScriptedProvider(dev.ok_events()), mutate_deps=mutate)
    for it in range(ITERATIONS):
        ctx = f"{kind} {_ctx(SEED + 6, it)}"
        before = len(stub.seen)
        res = dev.run(dev.post(app, records, content=_prompt(rng), stream=rng.random() < 0.5))
        assert res.status == (429 if kind == "rate_limited" else 503), ctx
        assert len(stub.seen) == before + 1, ctx
        rid = stub.seen[-1]
        assert _body_ids(res.body) == {rid} or res.headers["x-request-id"] == rid, ctx
        assert res.headers["x-request-id"] == rid, f"header differs from the verdict id {ctx}"
        assert _body_ids(res.body) <= {rid}, ctx
        assert {r.request_id for r in res.records} <= {rid}, ctx


def test_admitted_verdict_carries_the_id_it_is_given() -> None:
    rng = random.Random(SEED + 7)
    admission = CountingAdmission()
    for it in range(ITERATIONS):
        rid = f"zs-{_org(rng)}-{rng.getrandbits(96):024x}"
        verdict = asyncio.run(admission.admit("o", rid, 0.0))
        assert isinstance(verdict, Admitted) and verdict.request_id == rid, _ctx(SEED + 7, it)
    controller = _controller()
    for it in range(ITERATIONS):
        rid = f"zs-{_org(rng)}-{rng.getrandbits(96):024x}"
        verdict = asyncio.run(controller.admit("o", rid, 0.0))
        assert isinstance(verdict, Admitted) and verdict.request_id == rid, _ctx(SEED + 7, it)
        controller.complete(rid)


# -- 3.6 refund on a zero-provider-call refusal -------------------------------------------


def test_zero_provider_refusal_refunds_exactly_the_spent_cost_to_the_same_owner() -> None:
    rng = random.Random(SEED + 8)
    budget, admission = CountingBudget(), CountingAdmission()

    def mutate(deps: dict[str, object]) -> None:
        deps["quota"] = budget
        deps["admission"] = admission

    provider = dev.ScriptedProvider(dev.ok_events())
    app, records, _ = dev.build_app(provider, mutate_deps=mutate)
    allowed = 0
    for it in range(ITERATIONS):
        ctx = _ctx(SEED + 8, it)
        spent_before = budget.spent
        refunds_before = len(budget.refund_calls)
        blocked = rng.random() < 0.6
        prompt = _BLOCKED_PROMPT if blocked else _prompt(rng)
        res = dev.run(dev.post(app, records, content=prompt, stream=rng.random() < 0.5))
        spent = budget.spent - spent_before
        refunds = budget.refund_calls[refunds_before:]
        assert spent > 0, ctx
        if blocked:
            assert res.status >= 400, ctx
            assert len(refunds) == 1, f"expected exactly one refund {ctx}: {refunds}"
            cost, rid, owner = refunds[0]
            assert cost == spent, f"refunded {cost}, spent {spent} {ctx}"
            assert rid and owner, ctx
            assert {r.org_id for r in res.records} <= {owner}, f"refund owner differs {ctx}"
        else:
            assert res.status == 200, ctx
            assert refunds == [], f"a dispatched request must keep its charge {ctx}"
            allowed += 1
    assert provider.opened == allowed, "blocked requests must not open the provider"


def test_refund_returns_to_the_spending_org_only() -> None:
    rng = random.Random(SEED + 9)
    for it in range(ITERATIONS):
        ctx = _ctx(SEED + 9, it)
        h_a, h_b = Harness(org=_org(rng)), Harness(org=_org(rng) + "-b")
        h_a.engage("org:" + h_a.org)  # refused after nothing is spent: nothing to refund
        assert _run(h_a, "/v1/chat/completions", _text(_prompt(rng))).status == 503
        assert _run(h_b, "/v1/chat/completions", _text(_prompt(rng))).status == 200
        assert h_a.budget.refund_calls == [] and h_a.budget.net == 0, ctx
        assert h_b.budget.refund_calls == [] and h_b.budget.spent > 0, ctx


# -- 3.7 release performs exactly one complete --------------------------------------------


class _Quota:
    def __init__(self) -> None:
        self.refunds: list[tuple[int, str, str]] = []

    def refund(self, cost: int, *, request_id: str, owner: str) -> None:
        self.refunds.append((cost, request_id, owner))


class _Admit:
    def __init__(self) -> None:
        self.completed: list[str] = []

    def complete(self, request_id: str) -> None:
        self.completed.append(request_id)


def test_release_completes_and_refunds_exactly_once() -> None:
    rng = random.Random(SEED + 10)
    for it in range(ITERATIONS):
        ctx = _ctx(SEED + 10, it)
        org, rid = _org(rng), f"zs-{_org(rng)}-{rng.getrandbits(96):024x}"
        cost, dispatched = rng.randrange(1, 500), rng.random() < 0.4
        holds, admission, quota = RequestHolds(), _Admit(), _Quota()
        holds.note_tenant(org)
        holds.admitted(rid)
        holds.spent(cost, rid)
        if dispatched:
            holds.mark_dispatched()
        for _ in range(rng.randrange(1, 4)):
            holds.release(admission, quota)  # type: ignore[arg-type]
        assert admission.completed == [rid], f"complete not exactly once {ctx}"
        expected = [] if dispatched else [(cost, rid, org)]
        assert quota.refunds == expected, ctx


def test_a_never_admitted_request_does_not_complete() -> None:
    holds, admission, quota = RequestHolds(), _Admit(), _Quota()
    holds.release(admission, quota)  # type: ignore[arg-type]
    assert admission.completed == [] and quota.refunds == []


def _controller() -> AdmissionController:
    depth = 64
    bounds = AdmissionBounds(
        concurrency=depth,
        request_depth=depth,
        guard_depth=depth,
        dispatch_depth=depth,
        egress_depth=depth,
        audit_depth=depth,
    )
    return AdmissionController(
        bounds=bounds,
        params=CoDelParams(),
        rng=random.Random(0),
        supervisor=WorkerSupervisor(),
        metrics=AdmissionMetrics(),
        drain_window_s=1.0,
        clock=lambda: 0.0,
    )


def _depths(c: AdmissionController) -> dict[str, int]:
    return {n: d for n, (d, _a) in c.queue_report(now=0.0).per_queue.items()}


def test_complete_is_idempotent_and_a_no_op_for_an_unknown_id() -> None:
    rng = random.Random(SEED + 11)
    c = _controller()
    baseline = _depths(c)
    for it in range(ITERATIONS):
        ctx = _ctx(SEED + 11, it)
        rid = f"zs-{_org(rng)}-{rng.getrandbits(96):024x}"
        c.complete(rid)  # unknown id: nothing changes
        assert _depths(c) == baseline, ctx
        assert isinstance(asyncio.run(c.admit("o", rid, 0.0)), Admitted)
        assert _depths(c)["request"] == baseline["request"] + 1, ctx
        c.complete(rid)
        c.complete(rid)  # second complete must not pop a foreign slot
        assert _depths(c) == baseline, ctx


def test_every_admitted_request_is_completed_once_across_outcomes() -> None:
    rng = random.Random(SEED + 12)
    for it in range(ITERATIONS):
        ctx = _ctx(SEED + 12, it)
        h = Harness(org=_org(rng))
        prompt = _prompt(rng)
        status = _run(h, "/v1/chat/completions", {**_text(prompt), "stream": rng.random() < 0.5})
        assert status.status == 200, ctx
        assert h.admission.admitted == 1 and h.admission.completed == 1, ctx


# -- 3.8 audit still needs a request_id ---------------------------------------------------


def test_audit_record_rejects_an_empty_request_id_and_accepts_a_non_empty_one() -> None:
    rng = random.Random(SEED + 13)
    for it in range(ITERATIONS):
        ctx = _ctx(SEED + 13, it)
        org = _org(rng)
        with pytest.raises(ValueError, match="request_id"):
            AuditRecord(
                request_id="", org_id=org, phase=AuditPhase.INPUT, outcome="allow", recorded_at_ns=1
            )
        rid = f"zs-{org}-{rng.getrandbits(96):024x}"
        rec = AuditRecord(
            request_id=rid, org_id=org, phase=AuditPhase.INPUT, outcome="allow", recorded_at_ns=1
        )
        assert rec.request_id == rid, ctx


def test_every_audit_row_the_edge_emits_has_a_non_empty_id() -> None:
    rng = random.Random(SEED + 14)
    for it in range(ITERATIONS):
        ctx = _ctx(SEED + 14, it)
        h = Harness(org=_org(rng))
        if rng.random() < 0.4:
            h.engage("global")
        _run(h, "/v1/chat/completions", _text(_prompt(rng)))
        assert all(r.request_id for r in h.records), ctx


# -- 3.10 GW35 golden wire bytes ----------------------------------------------------------


@pytest.mark.parametrize("name", sorted(golden.SCENARIOS))
def test_golden_wire_bytes_match_with_the_id_normalised(name: str) -> None:
    recorded = json.loads(golden.GOLDEN.read_text(encoding="utf-8"))[name]
    assert golden._canonical(golden._capture(name)) == golden._canonical(recorded), name


# -- 3.11 fail closed ---------------------------------------------------------------------


class _Raises:
    def __init__(self, which: str) -> None:
        self.which = which

    async def admit(self, owner_id: str, request_id: str, enqueued_at: float) -> object:
        if self.which == "admit":
            raise RuntimeError("admission store down")
        return Admitted(owner_id=owner_id, request_id=request_id, admitted_at=0.0)

    async def evaluate(self, cost: int, **kw: object) -> object:
        raise RuntimeError("budget store down")

    def complete(self, request_id: str) -> None:
        return None

    def refund(self, *a: object, **k: object) -> None:
        return None


@pytest.mark.parametrize("which", ["admit", "budget"])
def test_a_failing_phase_never_reaches_the_provider(which: str) -> None:
    rng = random.Random(SEED + 15)

    def mutate(deps: dict[str, object]) -> None:
        deps["admission" if which == "admit" else "quota"] = _Raises(which)
        if which == "budget":
            deps["admission"] = CountingAdmission()

    provider = dev.ScriptedProvider(dev.ok_events())
    app, records, _ = dev.build_app(provider, mutate_deps=mutate)
    for it in range(ITERATIONS):
        ctx = f"{which} {_ctx(SEED + 15, it)}"
        try:
            res = dev.run(dev.post(app, records, content=_prompt(rng)))
        except RuntimeError:
            continue  # an unhandled raise is also fail-closed: no response, no dispatch
        assert res.status >= 400, f"fail-open: {res.status} {ctx}"
    assert provider.opened == 0, "a failing phase dispatched to the provider"


# -- 3.9 control plane untouched (working-tree check; the baseline run is separate) --------


def test_this_change_leaves_gateway_untouched() -> None:
    root = Path(__file__).resolve().parents[3]
    if not (root / "gateway").is_dir():
        pytest.skip("no gateway/ tree next to gateway_v2/")
    out = subprocess.run(  # noqa: S603
        ["git", "diff", "--name-only", "HEAD", "--", "gateway/"],  # noqa: S607
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    if out.returncode != 0:
        pytest.skip("not a git work tree")
    assert out.stdout.strip() == "", f"gateway/ changed: {out.stdout}"
