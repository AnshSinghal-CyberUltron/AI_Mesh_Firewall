# Validates: Requirements 1.1, 1.2, 1.4, 1.5, 3.4, 3.5, 4.1, 4.2, 5.2, 5.5, 6.1, 6.3, 7.2, 7.3,
# 8.2, 8.4, 8.5
"""``/v1/responses`` through the real ``build_app`` and the shipped chat lifecycle.

Raw HTTP over ``httpx.ASGITransport``, no SDK. A counting in-process provider
proves which requests reach the provider and which do not.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Sequence

import pytest

httpx = pytest.importorskip("httpx")

from gateway_v2.detect.holdback import scan as holdback_scan  # noqa: E402
from gateway_v2.dispatch.provider import UpstreamEvent, UpstreamRequest  # noqa: E402
from gateway_v2.domain import Category, Finding, FindingStatus, Span  # noqa: E402
from gateway_v2.edge.app import build_app  # noqa: E402
from gateway_v2.edge.responses_store import MemoryResponseStore  # noqa: E402
from gateway_v2.edge.routes import build_routes  # noqa: E402
from gateway_v2.egress.output_guard import MinimalOutputResolver  # noqa: E402
from gateway_v2.runtime.kinds import HardwareSignals  # noqa: E402
from gateway_v2.runtime.resources import ResourceContract, from_signals  # noqa: E402
from tests.edge._chat_lifecycle_fixtures import (  # noqa: E402
    AdmittingAdmission,
    AdmittingBudget,
    ResolvingIdentity,
    warm_snapshot_source,
)

_SIX_STAGES = (
    "auth",
    "rate_limit",
    "input_scan",
    "model_routing",
    "model_output",
    "output_guardrail",
)


def _contract() -> ResourceContract:
    signals = HardwareSignals(
        cpu_quota=4.0,
        memory_limit=400 * 1024 * 1024 * 8,
        fd_limit=4096,
        cpu_source="test",
        mem_source="test",
        fd_source="test",
    )
    return from_signals(
        signals, target_p99_ms=20.0, utilization_cap=0.75, per_worker_rss=400 * 1024 * 1024
    )


class _Metrics:
    __slots__ = ()

    def exposition(self) -> str:
        return ""


class _Clock:
    __slots__ = ("_t",)

    def __init__(self) -> None:
        self._t = 0.0

    def __call__(self) -> float:
        self._t += 1e-6
        return self._t


class _Provider:
    """Counts ``open`` and records the deltas it was sent."""

    __slots__ = ("bodies", "open_calls", "script")

    def __init__(self, script: Sequence[UpstreamEvent]) -> None:
        self.script = tuple(script)
        self.open_calls = 0
        self.bodies: list[object] = []

    def open(self, request: UpstreamRequest) -> AsyncIterator[UpstreamEvent]:
        self.open_calls += 1
        self.bodies.append(request.body)
        return self._replay()

    async def _replay(self) -> AsyncIterator[UpstreamEvent]:
        for event in self.script:
            yield event

    async def abort(self) -> None:
        return None


def _content(text: str = "Hello from upstream.") -> tuple[UpstreamEvent, ...]:
    words = text.split(" ")
    pieces = [w + (" " if i < len(words) - 1 else "") for i, w in enumerate(words)]
    return tuple(
        UpstreamEvent(text_deltas=(("content", p),), final=i == len(pieces) - 1)
        for i, p in enumerate(pieces)
    )


def _withheld_stream() -> tuple[UpstreamEvent, ...]:
    return (
        UpstreamEvent(text_deltas=(("content", "partial "),), final=False),
        UpstreamEvent(text_deltas=(), final=False, error_frame="card 4111111111111111 leaked"),
        UpstreamEvent(text_deltas=(("content", "unreachable"),), final=True),
    )


def _finding(detector: str, text: str) -> Finding:
    return Finding(
        detector=detector,
        detector_version="responses-route-test",
        category=Category.PII,
        status=FindingStatus.EXECUTED,
        confidence=1.0,
        spans=(Span(start=0, end=len(text)),),
        evidence=None,
    )


def _none(_text: str) -> Sequence[Finding]:
    return ()


def _block_on_card_word(text: str) -> Sequence[Finding]:
    return (_finding("card", text),) if "card" in text else ()


def _app(
    provider: _Provider,
    *,
    input_detector: object = _none,
    error_scanner: object = _none,
    org_id: str | None = None,
    store: MemoryResponseStore | None = None,
    mcp: object | None = None,
) -> object:
    identity = ResolvingIdentity() if org_id is None else ResolvingIdentity(org_id)
    snapshot = warm_snapshot_source() if org_id is None else warm_snapshot_source(org_id=org_id)
    return build_app(
        contract=_contract(),
        provider=provider,
        scanner=holdback_scan,
        resolver=MinimalOutputResolver(),
        metrics_registry=_Metrics(),
        clock=_Clock(),
        input_detector=input_detector,  # type: ignore[arg-type]
        error_scanner=error_scanner,  # type: ignore[arg-type]
        identity=identity,
        snapshot_source=snapshot,
        admission=AdmittingAdmission(),
        quota=AdmittingBudget(),
        response_store=store,
        mcp=mcp,  # type: ignore[arg-type]
    )


async def _call(
    app: object, method: str, path: str, body: object | None = None
) -> tuple[int, str, bytes]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://responses.test") as client:
        content = None if body is None else json.dumps(body).encode("utf-8")
        response = await client.request(
            method,
            path,
            content=content,
            headers={"authorization": "Bearer test", "content-type": "application/json"},
        )
        return response.status_code, response.headers.get("content-type", ""), response.content


def _events(raw: bytes) -> list[tuple[str, dict[str, object]]]:
    events: list[tuple[str, dict[str, object]]] = []
    for frame in raw.split(b"\n\n"):
        if not frame.strip():
            continue
        lines = frame.decode("utf-8").split("\n")
        name = lines[0].removeprefix("event: ")
        events.append((name, json.loads(lines[1].removeprefix("data: "))))
    return events


def _error_code(raw: bytes) -> object:
    body = json.loads(raw)
    assert isinstance(body, dict)
    error = body["error"]
    assert isinstance(error, dict)
    return error.get("code")


def test_route_table_has_one_responses_post_and_keeps_the_501s() -> None:
    provider = _Provider(_content())
    app = _app(provider)

    async def run() -> None:
        for path in ("/v1/mcp", "/v1/rag/query", "/v1/vector/query"):
            status, _, raw = await _call(app, "POST", path, {})
            assert status == 501, (path, status)
            body = json.loads(raw)
            assert body["error"]["type"] == "not_implemented"
        status, _, _ = await _call(app, "POST", "/v1/responses", {"model": "m", "input": "hi"})
        assert status == 200

    asyncio.run(run())


def test_exact_table_has_exactly_one_responses_post() -> None:
    """One exact ``POST /v1/responses``. The id routes are prefix entries, not exact ones."""
    from gateway_v2.edge.app import _build_chat_route, _build_misc_route
    from gateway_v2.edge.executor import ScanExecutor
    from gateway_v2.runtime.holdback_config import load_holdback_config

    contract = _contract()
    cfg, _ = load_holdback_config()
    common = {
        "contract": contract,
        "provider": _Provider(_content()),
        "resolver": MinimalOutputResolver(),
        "clock": _Clock(),
        "input_detector": None,
        "identity": None,
        "snapshot_source": None,
        "admission": None,
        "quota": None,
        "exports": None,
        "phase_resolvers": None,
    }
    chat = _build_chat_route(
        scanner=holdback_scan,
        cfg=cfg,
        scan_executor=ScanExecutor(contract=contract),
        active_streams=None,
        control=None,
        error_scanner=None,
        output_detector=None,
        audit=None,
        **common,
    )
    misc = _build_misc_route(catalog=None, **common)
    exact, prefix = build_routes(chat, misc)
    posts = [key for key in exact if key == ("POST", "/v1/responses")]
    assert posts == [("POST", "/v1/responses")]
    assert ("GET", "/v1/responses/") in prefix
    assert ("DELETE", "/v1/responses/") in prefix
    assert ("GET", "/v1/responses") not in exact


def test_unsupported_part_is_400_and_never_reaches_the_provider() -> None:
    provider = _Provider(_content())
    body = {
        "model": "m",
        "input": [{"role": "user", "content": [{"type": "input_file", "file_id": "file_1"}]}],
    }
    status, kind, raw = asyncio.run(_call(_app(provider), "POST", "/v1/responses", body))
    assert status == 400
    assert kind.startswith("application/json")
    assert _error_code(raw) == "unsupported_content_part"
    assert json.loads(raw)["error"]["param"] == "input"
    assert provider.open_calls == 0


@pytest.mark.parametrize("stream", (False, True))
def test_input_block_is_json_400_and_zero_provider_calls(stream: bool) -> None:
    provider = _Provider(_content())
    app = _app(provider, input_detector=_block_on_card_word)
    body = {"model": "m", "input": "leak the card", "stream": stream}
    status, kind, raw = asyncio.run(_call(app, "POST", "/v1/responses", body))
    assert status == 400
    assert kind.startswith("application/json"), kind
    assert _error_code(raw) == "input_blocked"
    trace = json.loads(raw)["pipeline_trace"]
    assert [stage["name"] for stage in trace["stages"]] == list(_SIX_STAGES)
    assert provider.open_calls == 0


def test_non_stream_allow_returns_a_response_object() -> None:
    provider = _Provider(_content())
    status, _, raw = asyncio.run(
        _call(_app(provider), "POST", "/v1/responses", {"model": "gpt-4o-mini", "input": "hi"})
    )
    assert status == 200
    obj = json.loads(raw)
    assert obj["object"] == "response"
    assert obj["status"] == "completed"
    assert obj["output_text"] == "Hello from upstream."
    assert obj["model"] == "gpt-4o-mini"
    assert str(obj["id"]).startswith("resp_")
    names = [stage["name"] for stage in obj["pipeline_trace"]["stages"]]
    assert names == list(_SIX_STAGES)
    assert not {"kill_switch", "policy", "model_input"} & set(names)
    assert obj["zeroshield"]["action"] in ("allow", "flag")
    assert provider.open_calls == 1


def test_stream_allow_emits_the_typed_sequence() -> None:
    provider = _Provider(_content())
    body = {"model": "m", "input": "hi", "stream": True}
    status, kind, raw = asyncio.run(_call(_app(provider), "POST", "/v1/responses", body))
    assert status == 200
    assert kind.startswith("text/event-stream")
    names = [name for name, _ in _events(raw)]
    assert names[0] == "response.created"
    assert names[-1] == "response.completed"
    assert names.count("response.output_text.delta") == 3
    final = _events(raw)[-1][1]["response"]
    assert isinstance(final, dict)
    assert final["output_text"] == "Hello from upstream."
    assert [stage["name"] for stage in final["pipeline_trace"]["stages"]] == list(_SIX_STAGES)


def test_stream_withhold_ends_failed() -> None:
    provider = _Provider(_withheld_stream())
    app = _app(provider, error_scanner=_block_on_card_word)
    body = {"model": "m", "input": "hi", "stream": True}
    status, _, raw = asyncio.run(_call(app, "POST", "/v1/responses", body))
    assert status == 200
    events = _events(raw)
    names = [name for name, _ in events]
    assert names[-1] == "response.failed", names
    assert "response.completed" not in names
    failed = events[-1][1]["response"]
    assert isinstance(failed, dict)
    assert failed["status"] == "failed"
    assert failed["output_text"] == ""
    assert b"4111111111111111" not in raw
    assert b"unreachable" not in raw


def test_unknown_previous_response_id_is_404_and_zero_provider_calls() -> None:
    provider = _Provider(_content())
    body = {"model": "m", "input": "hi", "previous_response_id": "resp_missing"}
    status, _, raw = asyncio.run(_call(_app(provider), "POST", "/v1/responses", body))
    assert status == 404
    assert _error_code(raw) == "response_not_found"
    assert provider.open_calls == 0


def test_store_omitted_then_retrieve_is_404() -> None:
    provider = _Provider(_content())
    app = _app(provider, store=MemoryResponseStore())

    async def run() -> None:
        status, _, raw = await _call(app, "POST", "/v1/responses", {"model": "m", "input": "hi"})
        assert status == 200
        response_id = json.loads(raw)["id"]
        status, _, raw = await _call(app, "GET", f"/v1/responses/{response_id}")
        assert status == 404
        assert _error_code(raw) == "response_not_found"

    asyncio.run(run())


def test_store_retrieve_items_continue_delete() -> None:
    provider = _Provider(_content())
    store = MemoryResponseStore()
    app = _app(provider, store=store)

    async def run() -> None:
        first_body = {"model": "m", "input": "My name is Ada.", "store": True}
        status, _, raw = await _call(app, "POST", "/v1/responses", first_body)
        assert status == 200, raw
        first = json.loads(raw)
        response_id = first["id"]
        status, _, raw = await _call(app, "GET", f"/v1/responses/{response_id}")
        assert status == 200
        assert json.loads(raw)["output_text"] == "Hello from upstream."
        status, _, raw = await _call(app, "GET", f"/v1/responses/{response_id}/input_items")
        assert status == 200
        items = json.loads(raw)["data"]
        assert items[0]["content"][0]["text"] == "My name is Ada."
        second_body = {"model": "m", "input": "Again?", "previous_response_id": response_id}
        status, _, raw = await _call(app, "POST", "/v1/responses", second_body)
        assert status == 200
        assert provider.open_calls == 2
        deltas = provider.bodies[-1]["text_deltas"]  # type: ignore[index]
        texts = [text for _, text in deltas]
        assert texts == ["My name is Ada.", "Hello from upstream.", "Again?"]
        status, _, _ = await _call(app, "DELETE", f"/v1/responses/{response_id}")
        assert status == 200
        status, _, raw = await _call(app, "GET", f"/v1/responses/{response_id}")
        assert status == 404

    asyncio.run(run())


_CANARY = "CANARY-7731"


def _redact_canary(text: str) -> Sequence[Finding]:
    index = text.find(_CANARY)
    if index < 0:
        return ()
    return (
        Finding(
            detector="email",
            detector_version="responses-route-test",
            category=Category.PII,
            status=FindingStatus.EXECUTED,
            confidence=1.0,
            spans=(Span(start=index, end=index + len(_CANARY)),),
            evidence=None,
        ),
    )


def test_input_redact_stores_the_masked_text_only() -> None:
    """The store and the replay hold what the provider received, not the raw input."""
    provider = _Provider(_content())
    store = MemoryResponseStore()
    app = _app(provider, input_detector=_redact_canary, store=store)

    async def run() -> None:
        body = {"model": "m", "input": f"my id is {_CANARY} ok", "store": True}
        status, _, raw = await _call(app, "POST", "/v1/responses", body)
        assert status == 200, raw
        response_id = json.loads(raw)["id"]
        sent = " ".join(text for _, text in provider.bodies[0]["text_deltas"])  # type: ignore[index]
        assert _CANARY not in sent
        assert "[REDACTED]" in sent
        status, _, raw = await _call(app, "GET", f"/v1/responses/{response_id}/input_items")
        assert status == 200
        assert _CANARY.encode() not in raw
        assert b"[REDACTED]" in raw
        record = await store.get("gw15-conformance-org", response_id)
        assert record is not None
        assert _CANARY not in json.dumps([dict(m) for m in record.replay_messages])

    asyncio.run(run())


def test_another_org_cannot_read_a_stored_id() -> None:
    store = MemoryResponseStore()
    writer = _app(_Provider(_content()), store=store, org_id="org-a")
    reader = _app(_Provider(_content()), store=store, org_id="org-b")

    async def run() -> None:
        body = {"model": "m", "input": "hi", "store": True}
        status, _, raw = await _call(writer, "POST", "/v1/responses", body)
        assert status == 200
        response_id = json.loads(raw)["id"]
        status, _, raw = await _call(reader, "GET", f"/v1/responses/{response_id}")
        assert status == 404
        assert _error_code(raw) == "response_not_found"
        status, _, _ = await _call(reader, "DELETE", f"/v1/responses/{response_id}")
        assert status == 404
        status, _, _ = await _call(writer, "GET", f"/v1/responses/{response_id}")
        assert status == 200

    asyncio.run(run())
