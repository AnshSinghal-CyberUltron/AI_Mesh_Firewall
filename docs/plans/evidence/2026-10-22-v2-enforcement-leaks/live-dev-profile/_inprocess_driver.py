"""Task 37 complementary in-process driver for the v2-enforcement-leaks live re-check.

The LIVE uvicorn HTTP server (see server.log / the curl transcript) is the primary evidence and
proves the real TCP/HTTP surface, the real GW06 token lease over Redis, and the provider-side
finding counts from the EchoProviderClient's ``dev-echo:`` stderr lines. Two things the live
echo path cannot give directly are captured here against the IDENTICAL real dev-profile ASGI app
(``serve.build_boot_dependencies`` -> ``serve._assemble_app`` -- the same pipeline the server
hosts), with a RECORDING provider substituted for the echo provider:

  1. BYTE-LEVEL provider-side proof that the SSN is masked before the provider, for chat and for
     the embeddings surface (the embeddings echo cannot produce vectors, so the live embeddings
     call ends 403 after the provider; here a vector-replying recording provider yields 200 and
     records the exact dispatched bytes).
  2. The authoritative request-queue-depth proof for the "slots released" scenario: the live
     dev profile wires only a static exposition registry (GW14d Publisher not wired), so
     ``/metrics`` carries no request-queue depth. The real ``AdmissionController`` exposes the
     depth directly via ``queue_report(now).depth("request")`` -- exactly the accessor the
     shipped Group D lifecycle test reads. ``3 x request_depth`` sequential requests are driven
     through the real app; after each the depth must be 0 and nothing may be shed ``overload_shed``.

No product code is modified. This only drives the shipped real app in-process.
"""

from __future__ import annotations

import asyncio
import json
import random
from collections.abc import AsyncIterator, Mapping
from pathlib import Path

from gateway_v2.admit.admission import AdmissionController
from gateway_v2.admit.codel import CoDelParams
from gateway_v2.admit.metrics import AdmissionMetrics, ShedReason
from gateway_v2.admit.quota import AdmissionBounds
from gateway_v2.admit.supervisor import WorkerSupervisor
from gateway_v2.dispatch.provider import UpstreamEvent, UpstreamRequest
from gateway_v2.dispatch.transform import BODY_DELTAS_KEY
from gateway_v2.edge import serve
from gateway_v2.runtime.kinds import HardwareSignals
from gateway_v2.runtime.resources import ResourceContract, from_signals

PLAN = Path(__file__).resolve().parents[5] / "gateway_v2" / "dev" / "plan.example.json"
KEY = "dev-key-task37"
SSN = "123-45-6789"
REDACTED = "[REDACTED]"


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


def _dev_env() -> dict[str, str]:
    return {
        "AMF_DEV_PROFILE": "1",
        "AMF_DEV_API_KEY": KEY,
        "AMF_DEV_PLAN_PATH": str(PLAN),
        "AMF_DEV_MODELS": "echo-1",
        "AMF_DEV_Q_SAFE": "500",
        "AMF_DEV_AUDIT_DRAIN_RATE_PER_S": "1000",
        "AMF_DEV_AUDIT_BYTES_PER_RECORD": "2765",
        "AMF_DEV_BUDGET": "unlimited",
    }


class _RecordingChatProvider:
    def __init__(self) -> None:
        self.received: list[str] = []

    def open(self, request: UpstreamRequest) -> AsyncIterator[UpstreamEvent]:
        return self._stream(request)

    async def _stream(self, request: UpstreamRequest) -> AsyncIterator[UpstreamEvent]:
        deltas = request.body.get(BODY_DELTAS_KEY, ())
        assert isinstance(deltas, tuple)
        text = "\n".join(str(item[1]) for item in deltas)
        self.received.append(text)
        yield UpstreamEvent(text_deltas=(("content", "echo: " + text),), final=True)

    async def abort(self) -> None:
        return None


class _RecordingVectorProvider:
    """Records the exact forwarded deltas and replies one JSON vector per delta (200)."""

    def __init__(self) -> None:
        self.received: list[tuple[tuple[str, str], ...]] = []

    def open(self, request: UpstreamRequest) -> AsyncIterator[UpstreamEvent]:
        return self._stream(request)

    async def _stream(self, request: UpstreamRequest) -> AsyncIterator[UpstreamEvent]:
        raw = request.body.get(BODY_DELTAS_KEY, ())
        assert isinstance(raw, tuple)
        deltas = tuple((str(i[0]), str(i[1])) for i in raw)
        self.received.append(deltas)
        for index, _delta in enumerate(deltas):
            vector = json.dumps([float(index), 0.5])
            yield UpstreamEvent(
                text_deltas=(("embeddings_input", vector),), final=index == len(deltas) - 1
            )

    async def abort(self) -> None:
        return None


def _app(provider: object, *, admission: object | None = None) -> object:
    deps, _logs = serve.build_boot_dependencies(contract=_contract(), env=_dev_env())
    deps["provider"] = provider
    if admission is not None:
        deps["admission"] = admission
    return serve._assemble_app(deps)


async def _post(app: object, path: str, body_obj: dict[str, object]) -> tuple[int, bytes]:
    body = json.dumps(body_obj).encode()
    scope = {
        "type": "http",
        "method": "POST",
        "path": path,
        "headers": [(b"authorization", f"Bearer {KEY}".encode()), (b"content-type", b"json")],
    }
    sent: list[Mapping[str, object]] = []
    delivered = False

    async def _receive() -> Mapping[str, object]:
        nonlocal delivered
        if delivered:
            await asyncio.sleep(3600)
        delivered = True
        return {"type": "http.request", "body": body, "more_body": False}

    async def _send(message: Mapping[str, object]) -> None:
        sent.append(message)

    assert callable(app)
    await app(scope, _receive, _send)
    status = next(int(str(m["status"])) for m in sent if m["type"] == "http.response.start")
    payload = b"".join(
        bytes(m.get("body", b""))  # type: ignore[arg-type]
        for m in sent
        if m["type"] == "http.response.body"
    )
    return status, payload


def chat_ssn_bytes() -> None:
    print("=== IN-PROCESS: SSN masked in the bytes DISPATCHED TO THE PROVIDER (chat) ===")
    provider = _RecordingChatProvider()
    app = _app(provider)
    status, payload = asyncio.run(
        _post(
            app,
            "/v1/chat/completions",
            {"model": "echo-1", "messages": [{"role": "user", "content": f"My SSN is {SSN} thanks"}]},
        )
    )
    dispatched = provider.received[0]
    print(f"client status      : {status}")
    print(f"PROVIDER received  : {dispatched!r}")
    print(f"raw SSN in bytes   : {SSN in dispatched}  (must be False)")
    print(f"[REDACTED] in bytes: {REDACTED in dispatched}  (must be True)")
    assert status == 200
    assert SSN not in dispatched
    assert REDACTED in dispatched
    print("RESULT: PASS -- provider received masked bytes; raw SSN absent\n")


def embeddings_ssn_bytes() -> None:
    print("=== IN-PROCESS: SSN item masked in the bytes DISPATCHED TO THE PROVIDER (embeddings) ===")
    provider = _RecordingVectorProvider()
    app = _app(provider)
    status, _payload = asyncio.run(
        _post(
            app,
            "/v1/embeddings",
            {"model": "echo-1", "input": ["release notes", f"My SSN is {SSN}"]},
        )
    )
    deltas = provider.received[0]
    texts = [t for _c, t in deltas]
    print(f"client status      : {status}")
    print(f"PROVIDER received  : {deltas!r}")
    print(f"item count         : {len(deltas)}  (must be 2, in order)")
    print(f"clean item kept    : {texts[0]!r}  (must be 'release notes' byte-identical)")
    print(f"raw SSN in any item: {any(SSN in t for t in texts)}  (must be False)")
    print(f"[REDACTED] present : {any(REDACTED in t for t in texts)}  (must be True)")
    assert status == 200
    assert len(deltas) == 2
    assert texts[0] == "release notes"
    assert all(SSN not in t for t in texts)
    assert any(REDACTED in t for t in texts)
    print("RESULT: PASS -- each embeddings item masked at the provider, shape/order preserved\n")


class _EchoProvider:
    def open(self, request: UpstreamRequest) -> AsyncIterator[UpstreamEvent]:
        return self._stream(request)

    async def _stream(self, request: UpstreamRequest) -> AsyncIterator[UpstreamEvent]:
        yield UpstreamEvent(text_deltas=(("content", "ok"),), final=True)

    async def abort(self) -> None:
        return None


class _VectorEcho:
    def open(self, request: UpstreamRequest) -> AsyncIterator[UpstreamEvent]:
        return self._stream(request)

    async def _stream(self, request: UpstreamRequest) -> AsyncIterator[UpstreamEvent]:
        raw = request.body.get(BODY_DELTAS_KEY, ())
        deltas = tuple(raw) if isinstance(raw, tuple) else ()
        for index in range(len(deltas)):
            vector = json.dumps([float(index), 0.5])
            yield UpstreamEvent(
                text_deltas=(("embeddings_input", vector),), final=index == len(deltas) - 1
            )

    async def abort(self) -> None:
        return None


def slots_released() -> None:
    print("=== IN-PROCESS: slots released -- request-queue depth returns to 0, no overload_shed ===")
    contract = _contract()
    # Match the live dev-profile derive_bounds request_depth on this contract.
    from gateway_v2.admit.quota import derive_bounds

    bounds = derive_bounds(
        contract, q_safe=500.0, audit_drain_rate_per_s=1000.0, audit_bytes_per_record=2765
    )
    request_depth = bounds.request_depth
    metrics = AdmissionMetrics()
    controller = AdmissionController(
        bounds=bounds,
        params=CoDelParams(),
        rng=random.Random(0),
        supervisor=WorkerSupervisor(),
        metrics=metrics,
        drain_window_s=1.0,
        clock=lambda: 0.0,
    )
    total = 3 * request_depth
    print(f"request_depth={request_depth}; driving {total} sequential requests (real controller)")

    async def _run() -> list[tuple[int, str, int, int]]:
        trace: list[tuple[int, str, int, int]] = []
        for i in range(total):
            if i % 3 == 2:
                surface = "embeddings"
                app = _app(_VectorEcho(), admission=controller)
                status, _ = await _post(
                    app, "/v1/embeddings", {"model": "echo-1", "input": [f"req {i}"]}
                )
            else:
                surface = "chat"
                app = _app(_EchoProvider(), admission=controller)
                status, _ = await _post(
                    app,
                    "/v1/chat/completions",
                    {"model": "echo-1", "messages": [{"role": "user", "content": f"req {i}"}]},
                )
            depth = controller.queue_report(now=0.0).depth("request")
            trace.append((i + 1, surface, status, depth))
        return trace

    trace = asyncio.run(_run())
    max_depth = max(d for *_r, d in trace)
    sheds = metrics.snapshot().shed_for(ShedReason.QUEUE_FULL)
    in_flight = len(controller._in_flight)
    for number, surface, status, depth in trace:
        print(f"  req {number:2d} [{surface:10s}] status={status} request_queue_depth={depth}")
    print(f"max request-queue depth across run: {max_depth}  (must be 0)")
    print(f"overload_shed (QUEUE_FULL) count  : {sheds}  (must be 0)")
    print(f"in-flight records remaining       : {in_flight}  (must be 0)")
    assert all(status == 200 for *_r, status, _d in [(n, s, st, d) for n, s, st, d in trace])
    assert max_depth == 0
    assert sheds == 0
    assert in_flight == 0
    print("RESULT: PASS -- every slot released; depth back to 0; no overload_shed\n")


if __name__ == "__main__":
    chat_ssn_bytes()
    embeddings_ssn_bytes()
    slots_released()
    print("ALL IN-PROCESS CHECKS PASSED")
