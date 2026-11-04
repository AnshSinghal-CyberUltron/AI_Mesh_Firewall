"""GW31 route behaviours: generator branches, admin/vector/ingest routes, leaks, wiring."""

from __future__ import annotations

import ast
import asyncio
import dataclasses
import json
from pathlib import Path

from gateway_v2.dispatch.rag_context import GroundingVerdict
from tests.edge.gw31_harness import build_app, call, ingest_and_wait, rule, write_plan

DOC = {"id": "d1", "text": "alpha beta gamma delta epsilon", "source": "wiki"}
QUERY = {"collection": "docs", "query": "alpha beta gamma delta epsilon"}


def _code(body: dict[str, object]) -> object:
    return body["error"]["code"]  # type: ignore[index]


def _swap(**fields: object):  # type: ignore[no-untyped-def]
    def mutate(deps: dict[str, object]) -> None:
        deps["rag"] = dataclasses.replace(deps["rag"], **fields)  # type: ignore[type-var]

    return mutate


class _Echo:
    async def answer(self, frame_text: str, query: str) -> str | None:
        return frame_text


class _Fixed:
    def __init__(self, value: str | None) -> None:
        self.value = value

    async def answer(self, frame_text: str, query: str) -> str | None:
        return self.value


class _Ground:
    def __init__(self, code: str) -> None:
        self.code = code

    async def check(self, frame_text: str, query: str) -> GroundingVerdict:
        return GroundingVerdict(self.code)


def _plan(tmp_path: Path, **kw: object) -> Path:
    path = tmp_path / "plan.json"
    write_plan(path, **kw)  # type: ignore[arg-type]
    return path


def test_generator_output_rule_redacts_and_setting_off_leaves_it(tmp_path: Path) -> None:
    rules = [
        rule(
            "gen", ("gamma",), action="redact", scope="output", surfaces=["rag"], stage="generator"
        )
    ]
    for redaction, expect_masked in ((True, True), (False, False)):
        app, _ = build_app(
            _plan(tmp_path, rules=rules, rag_settings={"rag_redaction_enabled": redaction})
        )

        async def go() -> None:
            await ingest_and_wait(app, "docs", [DOC])
            status, body, raw = await call(app, "POST", "/v1/rag/query", QUERY)
            assert status == 200, raw
            text = body["context"][0]["text"]  # type: ignore[index]
            assert ("gamma" not in text) is expect_masked
            if not expect_masked:
                assert "redact_not_applied" in body["trace"][3]["reason"]  # type: ignore[index]

        asyncio.run(go())


def test_retriever_rule_blocks_chunk_only_in_its_stage(tmp_path: Path) -> None:
    rules = [
        rule("ret", ("poison",), action="block", scope="input", surfaces=["rag"], stage="retriever")
    ]
    app, _ = build_app(_plan(tmp_path, rules=rules))

    async def go() -> None:
        await ingest_and_wait(
            app,
            "docs",
            [
                {"id": "ok", "text": "alpha beta gamma delta epsilon", "source": "s"},
                {"id": "bad", "text": "alpha beta gamma delta poison", "source": "s"},
            ],
        )
        status, body, raw = await call(app, "POST", "/v1/rag/query", QUERY)
        assert status == 200, raw
        ids = [c["id"] for c in body["context"]]  # type: ignore[index]
        assert ids == ["ok"]
        status, body, _r = await call(
            app, "POST", "/v1/rag/query", {"collection": "docs", "query": "poison"}
        )
        assert status == 200  # a retriever-stage rule must not fire on the query stage

    asyncio.run(go())


def test_generator_tail_branches(tmp_path: Path) -> None:
    async def run(
        mutate: object, expect_status: int, expect_code: str | None, ingest: bool = True
    ) -> None:
        app, _ = build_app(_plan(tmp_path), mutate=mutate)
        if ingest:
            await ingest_and_wait(app, "docs", [DOC])
        status, body, raw = await call(app, "POST", "/v1/rag/query", QUERY)
        assert status == expect_status, raw
        if expect_code:
            assert _code(body) == expect_code

    async def go() -> None:
        await run(_swap(grounder=None), 503, "rag_grounding_unavailable")
        await run(_swap(grounder=_Ground("unavailable")), 503, "rag_grounding_unavailable")
        await run(_swap(answerer=_Fixed(None)), 502, "rag_answer_unavailable")
        await run(_swap(answerer=_Echo()), 403, "rag_output_withheld")  # frame echo = canary
        await run(_swap(answerer=_Fixed("all good")), 200, None)
        await run(_swap(embedder=None), 503, "rag_embedder_unavailable", ingest=False)

    asyncio.run(go())


def test_known_canary_in_chunk_text_is_withheld(tmp_path: Path) -> None:
    app, _ = build_app(_plan(tmp_path), mutate=_swap(token_source=lambda n: "c" * (2 * n)))

    async def go() -> None:
        canary = "c" * 32
        await ingest_and_wait(
            app, "docs", [{"id": "x", "text": f"alpha beta {canary}", "source": "s"}]
        )
        status, body, raw = await call(
            app, "POST", "/v1/rag/query", {"collection": "docs", "query": "alpha beta"}
        )
        assert status == 403 and _code(body) == "rag_output_withheld"
        assert canary.encode() not in raw

    asyncio.run(go())


def test_tier2_off_still_enforces_surface_rules(tmp_path: Path) -> None:
    rules = [
        rule("q", ("forbidden",), action="block", scope="input", surfaces=["rag"], stage="query")
    ]
    app, _ = build_app(
        _plan(tmp_path, rules=rules, rag_settings={"rag_tier2_enabled": False}),
        mutate=_swap(grounder=None),
    )

    async def go() -> None:
        await ingest_and_wait(app, "docs", [DOC])
        status, body, _r = await call(
            app, "POST", "/v1/rag/query", {"collection": "docs", "query": "forbidden"}
        )
        assert status == 400 and _code(body) == "rag_query_blocked"
        status, _b, raw = await call(app, "POST", "/v1/rag/query", QUERY)
        assert status == 200, raw  # no grounder needed when Tier-2 is off

    asyncio.run(go())


def test_namespace_and_input_refusals_make_no_adapter_call(tmp_path: Path) -> None:
    app, _ = build_app(_plan(tmp_path))

    async def go() -> None:
        for body in (
            {"collection": "UPPER", "query": "x y"},
            {"collection": "a b", "query": "x"},
            {"collection": "a" * 40, "query": "x y"},
        ):
            status, resp, _r = await call(app, "POST", "/v1/rag/query", body)
            assert status == 403 and _code(resp) == "rag_namespace_refused"
        status, resp, _r = await call(app, "POST", "/v1/rag/query", {"collection": "docs"})
        assert status == 400 and _code(resp) == "rag_query_invalid"
        status, resp, _r = await call(app, "POST", "/v1/rag/query", None)
        assert status == 400 and _code(resp) == "rag_invalid_request"

    asyncio.run(go())


def test_admin_collection_routes(tmp_path: Path) -> None:
    app, _ = build_app(_plan(tmp_path))

    async def go() -> None:
        status, _b, _r = await call(app, "POST", "/v1/admin/rag/collections", {"collection": "c1"})
        assert status == 200
        status, body, _r = await call(app, "POST", "/v1/rag/collections", {"collection": "c1"})
        assert status == 409 and _code(body) == "collection_exists"
        status, body, _r = await call(app, "GET", "/v1/rag/collections")
        assert body["collections"] == ["c1"]
        status, body, _r = await call(app, "DELETE", "/v1/rag/collections", {"collection": "c1"})
        assert status == 422 and _code(body) == "unsupported_operation"
        status, body, _r = await call(
            app, "DELETE", "/v1/rag/collections", {"collection": "c1", "ids": ["a"]}
        )
        assert status == 200
        status, body, _r = await call(
            app, "DELETE", "/v1/rag/documents", {"collection": "c1", "ids": ["a b"]}
        )
        assert status == 400 and _code(body) == "rag_invalid_request"

    asyncio.run(go())


def test_vector_upsert_query_delete_and_poison_is_caught(tmp_path: Path) -> None:
    rules = [
        rule(
            "ret",
            ("poison",),
            action="block",
            scope="input",
            surfaces=["vector"],
            stage="retriever",
        )
    ]
    app, _ = build_app(_plan(tmp_path, rules=rules))

    async def go() -> None:
        status, body, raw = await call(
            app,
            "POST",
            "/v1/vector/upsert",
            {
                "collection": "vc",
                "records": [
                    {"id": "r1", "text": "alpha beta gamma"},
                    {"id": "r2", "text": "poison alpha beta"},
                ],
            },
        )
        assert status == 200 and body["stored"] == 2, raw
        status, body, raw = await call(
            app, "POST", "/v1/vector/query", {"collection": "vc", "vector": [0.0] * 64, "top_k": 0}
        )
        assert status == 400
        embed = await _embed("alpha beta gamma")
        status, body, raw = await call(
            app, "POST", "/v1/vector/query", {"collection": "vc", "vector": embed, "top_k": 5}
        )
        assert status == 200, raw
        assert [m["id"] for m in body["matches"]] == ["r1"]  # type: ignore[index]
        status, body, _r = await call(
            app, "POST", "/v1/vector/delete", {"collection": "vc", "ids": ["r1"]}
        )
        assert status == 200
        status, body, _r = await call(
            app,
            "POST",
            "/v1/vector/upsert",
            {
                "collection": "vc",
                "records": [{"id": "x", "text": "t", "metadata": {"_rag_trust": 3}}],
            },
        )
        assert status == 400 and _code(body) == "rag_invalid_request"

    asyncio.run(go())


async def _embed(text: str) -> list[float]:
    from gateway_v2.dispatch.rag_embedding import DeterministicHashEmbedder
    from gateway_v2.runtime.resources_rag import RagBounds

    out = await DeterministicHashEmbedder(RagBounds().embedding_dimension).embed("o", (text,))
    assert out is not None
    return list(out[0])


def test_ingest_validation_trust_and_job_scoping(tmp_path: Path) -> None:
    app, _ = build_app(_plan(tmp_path))

    async def go() -> None:
        base = {"collection": "docs", "documents": [DOC]}
        for bad in (
            {**base, "trust": "high"},
            {**base, "trust": 2},
            {**base, "documents": [{**DOC, "metadata": {"_rag_org": "x"}}]},
            {**base, "documents": [DOC, DOC]},
            {**base, "documents": []},
            {**base, "documents": [{"id": "has space", "text": "t"}]},
        ):
            status, body, _r = await call(app, "POST", "/v1/rag/ingest", bad)
            assert status == 400 and _code(body) == "rag_invalid_request", bad
        status, body, _r = await call(
            app,
            "POST",
            "/v1/rag/ingest",
            {**base, "documents": [{"id": "big", "text": "x" * 20000}]},
        )
        assert status == 413 and _code(body) == "rag_ingest_too_large"
        status, _b, _r = await call(app, "GET", "/v1/rag/ingest/job-nope")
        assert status == 404
        job = await ingest_and_wait(app, "docs", [DOC], trust="untrusted")
        assert job["state"] == "stored"
        status, body, _r = await call(app, "POST", "/v1/rag/query", QUERY)
        assert body["context"][0]["trust"] == "untrusted"  # type: ignore[index]

    asyncio.run(go())


def test_ingest_block_and_job_store_failure(tmp_path: Path) -> None:
    rules = [rule("blk", ("evil",), action="block", scope="input", surfaces=["rag"])]
    app, _ = build_app(_plan(tmp_path, rules=rules))

    class _Down:
        async def put(self, job: object) -> bool:
            return False

        async def get(self, job_id: str) -> None:
            return None

        async def update(self, job: object) -> bool:
            return False

        async def remove(self, job_id: str) -> None:
            return None

    down, _ = build_app(_plan(tmp_path), mutate=_swap(job_store=_Down()))

    async def go() -> None:
        status, body, _r = await call(
            app,
            "POST",
            "/v1/rag/ingest",
            {"collection": "docs", "documents": [{"id": "e", "text": "evil text"}]},
        )
        assert status == 400 and _code(body) == "rag_ingest_blocked"
        status, body, _r = await call(
            down, "POST", "/v1/rag/ingest", {"collection": "docs", "documents": [DOC]}
        )
        assert status == 503 and _code(body) == "rag_unavailable"

    asyncio.run(go())


def test_no_chunk_text_or_credential_in_error_bodies(tmp_path: Path) -> None:
    app, _ = build_app(_plan(tmp_path), mutate=_swap(grounder=None))

    async def go() -> None:
        await ingest_and_wait(app, "docs", [{"id": "s", "text": "topsecretphrase alpha beta"}])
        status, _b, raw = await call(
            app, "POST", "/v1/rag/query", {"collection": "docs", "query": "topsecretphrase alpha"}
        )
        assert status == 503
        assert b"topsecretphrase" not in raw and b"detail_ref" not in raw

    asyncio.run(go())


def test_dev_org_is_not_refused_and_functions_are_short(tmp_path: Path) -> None:
    app, _ = build_app(_plan(tmp_path))

    async def go() -> None:
        status, body, _r = await call(
            app, "POST", "/v1/rag/query", {"collection": "docs", "query": "hello world"}
        )
        assert not (status == 403 and body["error"]["code"] == "rag_namespace_refused")  # type: ignore[index]

    asyncio.run(go())
    root = Path(__file__).resolve().parents[2] / "gateway_v2/edge"
    for name in (
        "app.py",
        "dev_profile.py",
        "dev_rag.py",
        "rag.py",
        "rag_stages.py",
        "rag_common.py",
        "rag_vector.py",
        "rag_admin.py",
        "rag_ingest.py",
    ):
        tree = ast.parse((root / name).read_text())
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                assert (node.end_lineno or 0) - node.lineno + 1 <= 120, (name, node.name)
    assert json.dumps({}) == "{}"
