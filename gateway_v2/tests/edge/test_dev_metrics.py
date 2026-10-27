"""DEV PROFILE ``/metrics``: the Publisher reads the producers the request path writes to."""

from __future__ import annotations

import asyncio
import json

from gateway_v2.audit.sink import SinkCounters
from gateway_v2.edge import serve
from gateway_v2.edge.dev_metrics import _LiveAudit
from gateway_v2.edge.publisher import Publisher
from tests.edge.test_dev_profile_boot import _contract, _dev_env

_CHAT = {"model": "echo-1", "messages": [{"role": "user", "content": "hello"}]}


async def _call(app: object, method: str, path: str, body: object = None, key: str = "sk-dev"):  # type: ignore[no-untyped-def]
    raw = b"" if body is None else json.dumps(body).encode()
    scope = {
        "type": "http", "method": method, "path": path, "raw_path": path.encode(),
        "query_string": b"", "http_version": "1.1", "scheme": "http", "root_path": "",
        "server": ("test", 80), "client": ("test", 1),
        "headers": [(b"authorization", f"Bearer {key}".encode()),
                    (b"content-type", b"application/json")],
    }
    done = False
    status: list[int] = []
    chunks: list[bytes] = []

    async def receive() -> dict[str, object]:
        nonlocal done
        if done:
            await asyncio.sleep(3600)
        done = True
        return {"type": "http.request", "body": raw, "more_body": False}

    async def send(message: dict[str, object]) -> None:
        if message["type"] == "http.response.start":
            status.append(int(message["status"]))  # type: ignore[call-overload]
        elif message["type"] == "http.response.body":
            chunks.append(bytes(message.get("body", b"")))  # type: ignore[arg-type]

    await app(scope, receive, send)  # type: ignore[operator]
    return status[0], b"".join(chunks).decode()


def _series(text: str) -> dict[str, float]:
    values: dict[str, float] = {}
    for line in text.splitlines():
        if line and not line.startswith("#"):
            name, _, value = line.rpartition(" ")
            values[name] = float(value)
    return values


async def _traffic() -> tuple[dict[str, object], dict[str, float]]:
    deps, _logs = serve.build_boot_dependencies(contract=_contract(), env=_dev_env())
    app = serve._assemble_app(deps)
    assert (await _call(app, "POST", "/v1/chat/completions", _CHAT))[0] == 200
    for _ in range(2):
        streamed = {**_CHAT, "stream": True}
        assert (await _call(app, "POST", "/v1/chat/completions", streamed))[0] == 200
    assert (await _call(app, "POST", "/v1/chat/completions", _CHAT, key="wrong"))[0] == 503
    assert (await _call(app, "GET", "/v1/models"))[0] == 200
    status, text = await _call(app, "GET", "/metrics")
    assert status == 200
    return deps, _series(text)


def test_dev_boot_publishes_through_the_gw14d_publisher() -> None:
    deps, logs = serve.build_boot_dependencies(contract=_contract(), env=_dev_env())
    assert isinstance(deps["metrics_registry"], Publisher)
    assert deps["observer"] is deps["metrics_registry"]
    line = next(item for item in logs if item.startswith("boot: metrics_registry ->"))
    assert "GW14d Publisher" in line and "ABSENT" in line


def test_metrics_follow_the_requests_the_app_served() -> None:
    _deps, series = asyncio.run(_traffic())
    # Four authenticated requests: one chat, two streams, one models list.
    assert series['amf_tenant_magnitude{tenant="dev-org"}'] == 4
    assert series["amf_admit_admitted_total"] == 3
    # The bad key: one posture refusal and one pre-decision 503 rejection.
    assert series["amf_posture_refusals_shared_state_unavailable_total"] == 1
    assert series['amf_rejected{status="503",reason="rejected"}'] == 1
    assert series['amf_rejected{status="503",reason="shed"}'] == 0


def test_every_stream_records_into_one_holdback_producer() -> None:
    _deps, series = asyncio.run(_traffic())
    assert series["amf_holdback_held_tokens_count"] == 2
    assert series["amf_holdback_release_processing_ms_count"] == 2


def test_state_producers_read_absent_not_healthy() -> None:
    _deps, series = asyncio.run(_traffic())
    absent = [name for name, value in series.items() if value == -1.0]
    assert any("freshness" in name or "age" in name for name in absent), absent


class _Sink:
    def __init__(self) -> None:
        self.counters_value = SinkCounters()

    def counters(self) -> SinkCounters:
        return self.counters_value


def test_live_audit_reads_the_sink_on_every_scrape() -> None:
    sink = _Sink()
    live = _LiveAudit(sink)  # type: ignore[arg-type]
    before = dict(live.series())
    sink.counters_value = SinkCounters(produced=7, written=7)
    after = dict(live.series())
    assert before != after
    assert _LiveAudit(None).series() == before
