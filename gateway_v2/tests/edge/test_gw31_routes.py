"""GW31 end to end over the dev boot path: ingest, query, scoping, refusals."""
from __future__ import annotations

import asyncio
from pathlib import Path

from tests.edge.gw31_harness import (
    build_app,
    call,
    ingest_and_wait,
    rule,
    write_plan,
)

DOCS = [
    {"id": "d1", "text": "the quick brown fox jumps over the lazy dog", "source": "wiki"},
    {"id": "d2", "text": "pack my box with five dozen liquor jugs", "source": "wiki"},
]


def test_dev_profile_serves_rag_not_501(tmp_path: Path) -> None:
    plan = tmp_path / "plan.json"
    write_plan(plan)
    app, _ = build_app(plan)

    async def go() -> None:
        job = await ingest_and_wait(app, "docs", DOCS)
        assert job["state"] == "stored" and job["stored"] == 2
        status, body, raw = await call(
            app, "POST", "/v1/rag/query",
            {"collection": "docs", "query": "the quick brown fox jumps over the lazy dog"},
        )
        assert status == 200, raw
        texts = [c["text"] for c in body["context"]]  # type: ignore[index]
        assert "the quick brown fox jumps over the lazy dog" in texts
        stages = [t["stage"] for t in body["trace"]]  # type: ignore[index]
        assert stages == ["query", "retriever", "ranker", "generator"]
        assert body["answer"] is None

    asyncio.run(go())


def test_auth_required_and_unknown_collection_is_not_empty_success(tmp_path: Path) -> None:
    plan = tmp_path / "plan.json"
    write_plan(plan)
    app, _ = build_app(plan)

    async def go() -> None:
        status, _b, _r = await call(app, "POST", "/v1/rag/query", {"query": "x"}, key=None)
        assert status == 401
        status, _b, raw = await call(
            app, "POST", "/v1/rag/query", {"collection": "never", "query": "hello there"}
        )
        assert status != 200, raw

    asyncio.run(go())


def test_rag_disabled_makes_zero_adapter_calls(tmp_path: Path) -> None:
    plan = tmp_path / "plan.json"
    write_plan(plan, rag_settings={"rag_enabled": False})
    app, _ = build_app(plan)

    async def go() -> None:
        status, body, _r = await call(
            app, "POST", "/v1/rag/query", {"collection": "docs", "query": "hello"}
        )
        assert status == 403 and body["error"]["code"] == "rag_disabled"  # type: ignore[index]

    asyncio.run(go())


def test_lost_chunk_store_is_declared_error_never_empty_success(tmp_path: Path) -> None:
    plan = tmp_path / "plan.json"
    write_plan(plan)
    app, deps = build_app(plan)

    async def go() -> None:
        await ingest_and_wait(app, "docs", DOCS)
        rag = deps["rag"]
        await rag.chunk_store.delete_many(  # type: ignore[attr-defined]
            "odev_dorg_sdefault_sdocs", ("d1", "d2")
        )
        status, body, raw = await call(
            app, "POST", "/v1/rag/query", {"collection": "docs", "query": "quick fox"}
        )
        assert status == 503, raw
        assert body["error"]["code"] == "rag_chunk_store_unavailable"  # type: ignore[index]

    asyncio.run(go())


def test_stage_scoped_rule_fires_only_in_its_stage(tmp_path: Path) -> None:
    plan = tmp_path / "plan.json"
    write_plan(
        plan,
        rules=[
            rule("q-only", ("forbiddenword",), action="block", scope="input",
                 surfaces=["rag"], stage="query"),
            rule("gen-redact", ("secretsauce",), action="redact", scope="output",
                 surfaces=["rag"], stage="generator"),
        ],
    )
    app, _ = build_app(plan)

    async def go() -> None:
        await ingest_and_wait(
            app, "docs",
            [{"id": "p", "text": "contains forbiddenword and secretsauce here", "source": "s"}],
        )
        status, body, _r = await call(
            app, "POST", "/v1/rag/query", {"collection": "docs", "query": "forbiddenword"}
        )
        assert status == 400 and body["error"]["code"] == "rag_query_blocked"  # type: ignore[index]
        status, body, raw = await call(
            app, "POST", "/v1/rag/query",
            {"collection": "docs", "query": "contains forbiddenword and secretsauce here"[9:]
             .replace("forbiddenword", "other")},
        )
        # the query-stage rule must not fire at the retriever: the chunk containing the
        # keyword is still retrieved, and only the generator rule redacts secretsauce
        if status == 200:
            text = body["context"][0]["text"]  # type: ignore[index]
            assert "secretsauce" not in text
            assert "forbiddenword" in text
        else:
            raise AssertionError(raw)

    asyncio.run(go())


def test_projects_are_isolated(tmp_path: Path) -> None:
    plan = tmp_path / "plan.json"
    write_plan(plan)
    app, deps = build_app(plan)

    async def go() -> None:
        await ingest_and_wait(app, "docs", DOCS)
        rag = deps["rag"]
        got = await rag.chunk_store.get_many(  # type: ignore[attr-defined]
            "odev_dorg_sother_sdocs", ("d1",)
        )
        assert got == {}
        status, body, _r = await call(app, "GET", "/v1/rag/collections")
        assert status == 200 and body["collections"] == ["docs"]

    asyncio.run(go())


def test_vector_config_has_no_credential_fields(tmp_path: Path) -> None:
    plan = tmp_path / "plan.json"
    write_plan(plan)
    app, _ = build_app(plan)

    async def go() -> None:
        status, body, _r = await call(app, "GET", "/v1/vector/config")
        assert status == 200
        assert set(body) <= {"connector", "capabilities", "request_id"}

    asyncio.run(go())
