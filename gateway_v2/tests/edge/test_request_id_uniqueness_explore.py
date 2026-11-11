# Feature: request-id-uniqueness, Property 1: Bug Condition - identical requests collide on one id
# Validates: Requirements 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7 (encodes 2.1-2.5 as the expectation)
"""Bug condition exploration for finding F2 (spec ``request-id-uniqueness``, task 1).

The v2 gateway derives ``request_id`` as ``sha256(tenant + content)[:24]``, so byte-identical
requests share one id. This file encodes the EXPECTED behaviour (ids pairwise distinct and of
the form ``zs-{org}-[0-9a-f]{24}``) and is therefore EXPECTED TO FAIL on the unfixed code. The
failure is the success case for an exploration test: it proves the bug. Do not weaken it.
It must pass unchanged once the fix lands (task 3.11).

Counterexamples observed on the UNFIXED tree (run recorded in the task 1 report):

* chat, 5 identical sequential calls    -> 5 requests, 10 audit rows (2 per request), ONE
                                            request_id (``zs-org-776669-ea402a7e6e336f41b8c48e43``)
* chat, 5 identical calls via gather    -> admission saw 5 requests under 1 distinct id
* chat, stream vs non-stream, same body -> 2 requests, 1 admission id
* repeated chat prompts (1.6)           -> 5 prompts, 10 audit rows, all under 1 request_id
* MCP ``tools/list`` twice, same body   -> 2 requests, 1 admission id
* MCP incident gate, no id noted        -> 3 calls, the gate got 1 id (the per-org constant)
* embeddings, 5 identical calls         -> 1 distinct admission id
* RAG query, 5 identical calls          -> 1 distinct admission id (``zs-dev-org-<hash>``)
* vector query, 5 identical calls       -> 1 distinct admission id

Task 3.11 note (2026-11-04): the embeddings test checks admission ids only. The embeddings
success path never emits audit rows (not before the fix either; only incident refusals are
audited), so its former audit assertion encoded a wrong assumption about product behaviour.

Audit emits two rows per chat request, so audit checks count DISTINCT ids against the number
of requests rather than rows.

House idiom: seeded ``random.Random``, ``asyncio.run``, no hypothesis. The failing seed and
iteration are in the assertion message. Harnesses reused: ``_lgw29_app.Harness`` (real
``build_app``, audit list, counting admission/budget), ``_mcp_fixtures`` (MCP deps),
``gw31_harness`` (RAG/vector over the dev boot path), ``_chat_lifecycle_fixtures`` (identity,
plan) via those.
"""

from __future__ import annotations

import asyncio
import random
import re
from pathlib import Path
from typing import Any

from tests.edge import gw31_harness
from tests.edge._lgw29_app import ORG, Harness, chat
from tests.edge._mcp_fixtures import Broker, Control, mcp_deps, server_row

SEED = 20261104
ITERATIONS = 5
IDENTICAL = 5


def _pattern(org: str) -> re.Pattern[str]:
    return re.compile(rf"zs-{re.escape(org)}-[0-9a-f]{{24}}")


def _assert_distinct(ids: list[str], org: str, label: str, context: str = "") -> None:
    assert ids, f"{label}: no ids observed (request never reached the observation point)"
    pat = _pattern(org)
    bad = [i for i in ids if not pat.fullmatch(i)]
    assert not bad, f"{label}: ids not of form zs-{org}-<24 hex>: {bad} {context}"
    assert len(set(ids)) == len(ids), (
        f"{label}: {len(ids)} identical requests produced {len(set(ids))} distinct id(s): "
        f"{sorted(set(ids))} {context}"
    )


def _assert_audit_ids(records: list[Any], n: int, org: str, label: str, context: str = "") -> None:
    """Audit emits several rows per request; N requests must yield exactly N distinct ids."""
    ids = [r.request_id for r in records]
    assert ids, f"{label}: no audit rows"
    assert len(set(ids)) == n, (
        f"{label}: {n} identical requests ({len(ids)} audit rows) share {len(set(ids))} "
        f"request_id(s): {sorted(set(ids))} {context}"
    )
    _assert_distinct(sorted(set(ids)), org, label, context)


def _record_admission(admission: Any, sink: list[str]) -> None:
    original = admission.admit

    async def admit(owner_id: str, request_id: str, enqueued_at: float) -> Any:
        sink.append(request_id)
        return await original(owner_id, request_id, enqueued_at)

    admission.admit = admit


def _post(h: Harness, path: str, body: dict[str, object]) -> Any:
    return asyncio.run(h.call(path, body))


async def _gather(h: Harness, path: str, body: dict[str, object], n: int) -> None:
    await asyncio.gather(*(h.call(path, body) for _ in range(n)))


# -- chat ---------------------------------------------------------------------------------


def test_chat_sequential_identical_calls_get_distinct_ids() -> None:
    rng = random.Random(SEED)
    for it in range(ITERATIONS):
        org = f"org-{rng.randrange(10**6)}"
        h = Harness(org=org)
        seen: list[str] = []
        _record_admission(h.admission, seen)
        body = chat()
        for _ in range(IDENTICAL):
            assert _post(h, "/v1/chat/completions", body).status == 200
        ctx = f"(seed={SEED} iteration={it})"
        _assert_audit_ids(h.records, IDENTICAL, org, "chat sequential audit rows", ctx)
        _assert_distinct(seen, org, "chat sequential admission ids", ctx)


def test_chat_concurrent_identical_calls_get_distinct_ids() -> None:
    rng = random.Random(SEED + 1)
    for it in range(ITERATIONS):
        org = f"org-{rng.randrange(10**6)}"
        h = Harness(org=org)
        seen: list[str] = []
        _record_admission(h.admission, seen)
        asyncio.run(_gather(h, "/v1/chat/completions", chat(), IDENTICAL))
        ctx = f"(seed={SEED + 1} iteration={it})"
        _assert_distinct(seen, org, "chat concurrent admission ids", ctx)
        _assert_audit_ids(h.records, IDENTICAL, org, "chat concurrent audit", ctx)


def test_stream_and_non_stream_with_identical_content_get_distinct_ids() -> None:
    org = ORG
    h = Harness(org=org)
    seen: list[str] = []
    _record_admission(h.admission, seen)
    _post(h, "/v1/chat/completions", chat(stream=False))
    _post(h, "/v1/chat/completions", chat(stream=True))
    _assert_distinct(seen, org, "stream vs non-stream admission ids")
    _assert_audit_ids(h.records, 2, org, "stream vs non-stream audit rows")


def test_audit_rows_for_repeated_prompts_are_separable() -> None:
    """1.6: N repeated prompts must be N rows under N request_ids, not N rows under one."""
    h = Harness(org=ORG)
    for _ in range(IDENTICAL):
        _post(h, "/v1/chat/completions", chat())
    by_id: dict[str, int] = {}
    for record in h.records:
        by_id[record.request_id] = by_id.get(record.request_id, 0) + 1
    assert len(by_id) == IDENTICAL, (
        f"{IDENTICAL} repeated prompts ({len(h.records)} audit rows) collapsed under "
        f"{len(by_id)} request_id(s): {by_id}"
    )


# -- MCP ----------------------------------------------------------------------------------


def _mcp(**kwargs: object) -> Harness:
    return Harness(mcp=mcp_deps(Broker(), Control([server_row("search")])), **kwargs)  # type: ignore[arg-type]


LIST = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}


def test_mcp_tools_list_twice_gets_distinct_ids() -> None:
    h = _mcp()
    seen: list[str] = []
    _record_admission(h.admission, seen)
    for _ in range(2):
        _post(h, "/v1/mcp/search", LIST)
    _assert_distinct(seen, ORG, "mcp tools/list admission ids")
    _assert_audit_ids(h.records, 2, ORG, "mcp tools/list audit rows")


def test_mcp_incident_gate_does_not_use_a_per_org_constant() -> None:
    """1.5: the gate id must be per request; today it is a constant when none is noted."""
    h = _mcp()
    gate_ids: list[str] = []
    original = h.rig.gate.decide_org

    def decide_org(*args: Any, **kwargs: Any) -> Any:
        rid = kwargs.get("request_id", args[1] if len(args) > 1 else "")
        gate_ids.append(rid)
        return original(*args, **kwargs)

    h.rig.gate.decide_org = decide_org  # type: ignore[method-assign]
    for i in range(3):
        _post(h, "/v1/mcp/search", {"jsonrpc": "2.0", "id": i, "method": "tools/list"})
    assert gate_ids, "the MCP incident gate was never consulted"
    # The gate must be handed a per-request id (distinct per call), form zs-{org}-<24 hex>.
    _assert_distinct(gate_ids, ORG, "mcp incident-gate ids")


# -- embeddings ---------------------------------------------------------------------------


def test_embeddings_identical_calls_get_distinct_ids() -> None:
    from tests.edge.test_lgw29_misc_surfaces import EMBED, VECTOR

    h = Harness(VECTOR, org=ORG)
    seen: list[str] = []
    _record_admission(h.admission, seen)
    for _ in range(IDENTICAL):
        _post(h, "/v1/embeddings", EMBED)
    assert seen, "embeddings never reached admission"
    _assert_distinct(seen, ORG, "embeddings admission ids")
    # Task 3.11 (2026-11-04): no audit-row assertion here. The embeddings success path has never
    # called emit_chat_audit (openai_misc.py, unfixed and fixed alike; incident_misc.py audits
    # only incident refusals), so "no audit rows" was a wrong assumption about product
    # behaviour, not the bug condition. The admission-id checks above are the embeddings bug.


# -- RAG / vector (dev boot path) ---------------------------------------------------------


class _RecordingAdmission:
    """Delegates to the dev admission decider, recording each request id it is given."""

    def __init__(self, inner: object, sink: list[str]) -> None:
        self._inner = inner
        self._sink = sink

    async def admit(self, owner_id: str, request_id: str, enqueued_at: float) -> Any:
        self._sink.append(request_id)
        return await self._inner.admit(owner_id, request_id, enqueued_at)  # type: ignore[attr-defined]

    def __getattr__(self, name: str) -> object:
        return getattr(self._inner, name)


def _dev_ids(tmp_path: Path, path: str, body: dict[str, object]) -> list[str]:
    plan = tmp_path / "plan.json"
    gw31_harness.write_plan(plan)
    seen: list[str] = []

    def mutate(deps: dict[str, object]) -> None:
        deps["admission"] = _RecordingAdmission(deps["admission"], seen)

    app, _ = gw31_harness.build_app(plan, mutate=mutate)

    async def go() -> None:
        for _ in range(IDENTICAL):
            await gw31_harness.call(app, "POST", path, body)

    asyncio.run(go())
    return seen


def test_rag_identical_calls_get_distinct_ids(tmp_path: Path) -> None:
    ids = _dev_ids(tmp_path, "/v1/rag/query", {"collection": "docs", "query": "same query"})
    assert ids, "rag never reached admission"
    _assert_distinct(ids, gw31_harness.ORG, "rag admission ids")


def test_vector_identical_calls_get_distinct_ids(tmp_path: Path) -> None:
    ids = _dev_ids(tmp_path, "/v1/vector/query", {"collection": "docs", "vector": [0.1, 0.2]})
    assert ids, "vector never reached admission"
    _assert_distinct(ids, gw31_harness.ORG, "vector admission ids")

