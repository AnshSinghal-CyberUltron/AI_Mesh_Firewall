"""RAG and vector fail closed when the request id cannot be minted (request-id-uniqueness, 3.7)."""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from gateway_v2.edge.request_holds import RequestHolds
from gateway_v2.edge.request_ids import RequestIdUnavailable
from tests.edge import gw31_harness


class _Counting:
    """Delegates to the real dependency, counting every call that reaches it."""

    def __init__(self, inner: object, sink: list[str]) -> None:
        self._inner = inner
        self._sink = sink

    def __getattr__(self, name: str) -> Any:
        attr = getattr(self._inner, name)
        if not callable(attr):
            return attr

        def wrapped(*a: object, **k: object) -> object:
            self._sink.append(name)
            return attr(*a, **k)

        return wrapped


@pytest.mark.parametrize(
    ("path", "body"),
    [
        ("/v1/rag/query", {"collection": "docs", "query": "q"}),
        ("/v1/vector/query", {"collection": "docs", "vector": [0.1, 0.2]}),
    ],
)
def test_failing_mint_refuses_with_no_admit_or_spend(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, path: str, body: dict[str, object]
) -> None:
    def failing(self: RequestHolds, org_id: str, *a: object, **k: object) -> str:
        raise RequestIdUnavailable()

    monkeypatch.setattr(RequestHolds, "begin_request", failing)
    plan = tmp_path / "plan.json"
    gw31_harness.write_plan(plan)
    admission_calls: list[str] = []
    quota_calls: list[str] = []

    def mutate(deps: dict[str, object]) -> None:
        deps["admission"] = _Counting(deps["admission"], admission_calls)
        deps["quota"] = _Counting(deps["quota"], quota_calls)

    app, _ = gw31_harness.build_app(plan, mutate=mutate)
    status, parsed, raw = asyncio.run(gw31_harness.call(app, "POST", path, body))

    assert status == 503, raw
    error = parsed["error"]
    assert isinstance(error, dict)
    assert error["code"] == "request_id_unavailable"
    assert error.get("request_id") is None
    assert b"zs-" not in raw
    assert admission_calls == []
    assert quota_calls == []
