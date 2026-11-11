"""GW31 properties P1, P2, P9, P10, P13, P14, P16 (seeded, house idiom, no hypothesis)."""

from __future__ import annotations

import asyncio
import dataclasses
import random
import string
from pathlib import Path

import pytest

from gateway_v2.dispatch.rag_dev_store import InMemoryVectorAdapter
from gateway_v2.dispatch.rag_namespace import build_namespace, parse_wire, wire_collection
from gateway_v2.dispatch.rag_retrieval import listed_name
from gateway_v2.dispatch.vector_registry import VectorRegistry
from gateway_v2.domain.category import Category
from gateway_v2.domain.decision import Decision, Disposition, FindingDisposition, Transformation
from gateway_v2.domain.finding import Finding, FindingStatus, Span
from gateway_v2.edge.rag_ingest import parse_documents
from gateway_v2.edge.rag_stages import RagRefused, redact_text
from gateway_v2.runtime.resources_rag import RagBounds
from tests.edge.gw31_harness import build_app, call, ingest_and_wait, rule, write_plan

B = RagBounds()


class _Counting(InMemoryVectorAdapter):
    def __init__(self) -> None:
        super().__init__()
        self.calls = 0

    async def query(self, req):  # type: ignore[no-untyped-def]
        self.calls += 1
        return await super().query(req)

    async def upsert(self, req):  # type: ignore[no-untyped-def]
        self.calls += 1
        return await super().upsert(req)

    async def create_collection(self, req):  # type: ignore[no-untyped-def]
        self.calls += 1
        return await super().create_collection(req)

    async def delete(self, req):  # type: ignore[no-untyped-def]
        self.calls += 1
        return await super().delete(req)

    async def list_collections(self, req):  # type: ignore[no-untyped-def]
        self.calls += 1
        return await super().list_collections(req)


def _app(tmp_path: Path, name: str, **kw: object):  # type: ignore[no-untyped-def]
    plan = tmp_path / f"{name}.json"
    write_plan(plan, **kw)  # type: ignore[arg-type]
    counting = _Counting()

    def mutate(deps: dict[str, object]) -> None:
        rag = deps["rag"]
        deps["rag"] = dataclasses.replace(
            rag,
            registry=VectorRegistry({"dev-memory": counting}),  # type: ignore[type-var]
        )

    app, deps = build_app(plan, mutate=mutate)
    return app, counting


# Feature: gw31-rag-pipeline, Property 1: a refusal before the retriever makes zero adapter calls
def test_property_refusals_make_zero_adapter_calls(tmp_path: Path) -> None:
    rng = random.Random(1)
    rules = [
        rule("q", ("blockedword",), action="block", scope="input", surfaces=["rag"], stage="query")
    ]
    app, counting = _app(tmp_path, "p1", rules=rules)
    disabled, counting_off = _app(tmp_path, "p1off", rag_settings={"rag_enabled": False})

    async def go() -> None:
        for _ in range(100):
            kind = rng.choice(["ns", "blocked", "invalid", "unauth", "disabled"])
            if kind == "ns":
                req = {"collection": "BAD " + str(rng.random()), "query": "hello there"}
            elif kind == "blocked":
                req = {"collection": "docs", "query": "blockedword " + str(rng.random())}
            elif kind == "invalid":
                req = {"collection": "docs", "query": rng.choice(["", "   ", 5, None])}
            else:
                req = {"collection": "docs", "query": "hello there"}
            target = disabled if kind == "disabled" else app
            status, _b, _r = await call(
                target,
                "POST",
                "/v1/rag/query",
                req,
                key=None if kind == "unauth" else "dev-key-change-me",
            )
            assert status >= 400
        assert counting.calls == 0 and counting_off.calls == 0

    asyncio.run(go())


# Feature: gw31-rag-pipeline, Property 2: a secret in a chunk never reaches the response
def test_property_chunk_secret_never_in_response(tmp_path: Path) -> None:
    rng = random.Random(2)
    app, _ = _app(tmp_path, "p2")

    async def go() -> None:
        for i in range(100):
            key = "AKIA" + "".join(
                rng.choice(string.ascii_uppercase + string.digits) for _ in range(16)
            )
            coll = f"c{i}"
            await ingest_and_wait(
                app, coll, [{"id": "s", "text": f"deploy notes key {key} end", "source": "s"}]
            )
            status, _b, raw = await call(
                app, "POST", "/v1/rag/query", {"collection": coll, "query": "deploy notes key end"}
            )
            assert key.encode() not in raw, status

    asyncio.run(go())


# Feature: gw31-rag-pipeline, Property 9: no chunk text, canary or credential in error bodies
def test_property_no_content_in_error_bodies(tmp_path: Path) -> None:
    rng = random.Random(9)
    app, _ = _app(tmp_path, "p9")

    def mutate(deps: dict[str, object]) -> None:
        deps["rag"] = dataclasses.replace(deps["rag"], grounder=None)  # type: ignore[type-var]

    broken, _deps = build_app(tmp_path / "p9.json", mutate=mutate)

    async def go() -> None:
        for i in range(100):
            marker = "mk" + "".join(rng.choice(string.ascii_lowercase) for _ in range(12))
            coll = f"c{i}"
            await ingest_and_wait(broken, coll, [{"id": "s", "text": f"alpha {marker} beta"}])
            status, _b, raw = await call(
                broken,
                "POST",
                "/v1/rag/query",
                {"collection": coll, "query": f"alpha {marker} beta"},
            )
            assert status == 503
            assert marker.encode() not in raw and b"canary" not in raw and b"<<ctx" not in raw

    asyncio.run(go())


# Feature: gw31-rag-pipeline, Property 10: a stage-scoped rule fires only in its own stage
def test_property_stage_scoping(tmp_path: Path) -> None:
    rng = random.Random(10)

    async def go() -> None:
        for i in range(100):
            stage = rng.choice(["", "query", "retriever", "ranker", "generator"])
            scope = "output" if stage == "generator" else "input"
            rules = [
                rule("r", ("zzmark",), action="block", scope=scope, surfaces=["rag"], stage=stage)
            ]
            app, _ = _app(tmp_path, f"p10_{i}", rules=rules)
            status, body, _r = await call(
                app, "POST", "/v1/rag/query", {"collection": "docs", "query": "has zzmark inside"}
            )
            fires = stage in ("", "query") and scope == "input"
            blocked = status == 400 and body["error"]["code"] == "rag_query_blocked"  # type: ignore[index]
            assert blocked == fires, (stage, status)

    asyncio.run(go())


def _decision(spans: list[tuple[int, int]]) -> Decision:
    findings = tuple(
        Finding("d", "1", Category.PII, FindingStatus.EXECUTED, 1.0, (Span(a, b),), None)
        for a, b in spans
    )
    return Decision(
        Disposition.REDACT,
        tuple(FindingDisposition("d", Disposition.REDACT, i) for i in range(len(spans))),
        tuple(Transformation("redact", Span(a, b), "X") for a, b in spans),
        findings,
        "v",
        ("r",),
        (),
    )


# Feature: gw31-rag-pipeline, Property 13: redaction is byte-verified or refused
def test_property_redaction_byte_verified() -> None:
    rng = random.Random(13)
    for _ in range(1000):
        text = "".join(rng.choice("abcdefgh") for _ in range(30))
        a = rng.randint(0, 28)
        b = rng.randint(a + 1, 30)
        out = redact_text(text, _decision([(a, b)]))
        assert out is not None
        assert out == text[:a] + "X" + text[b:]
    assert redact_text("short", _decision([(3, 99)])) is None  # uncovered span fails closed


# Feature: gw31-rag-pipeline, Property 14: client-supplied _rag_ keys are refused, never persisted
def test_property_reserved_keys_refused() -> None:
    rng = random.Random(14)
    for _ in range(200):
        key = "_rag_" + "".join(
            rng.choice(string.ascii_lowercase) for _ in range(rng.randint(0, 8))
        )
        body = {"documents": [{"id": "a", "text": "t", "metadata": {key: rng.random()}}]}
        with pytest.raises(RagRefused):
            parse_documents(body, "documents", B, lambda: "x")
    docs = parse_documents(
        {"documents": [{"id": "a", "text": "t", "metadata": {"ok": 1}}], "trust": "low"},
        "documents",
        B,
        lambda: "x",
    )
    assert docs[0].trust.name == "LOW"


# Feature: gw31-rag-pipeline, Property 16: created names list back via listed_name + parse_wire
def test_property_create_list_round_trip() -> None:
    rng = random.Random(16)
    chars = "abcdefghij0123456789_-"
    for _ in range(1000):
        seg = lambda n: "".join(rng.choice(chars) for _ in range(rng.randint(1, n)))  # noqa: E731
        ns = build_namespace(
            seg(8),
            seg(6),
            seg(6),
            "project",
            org_max=36,
            project_max=32,
            collection_max=32,
            wire_max=58,
        )
        wire = wire_collection(ns)
        for shaped in (wire, "Gw30_" + wire, "gw30_" + wire):
            name = listed_name(shaped)
            assert name is not None and parse_wire(name) == ns
