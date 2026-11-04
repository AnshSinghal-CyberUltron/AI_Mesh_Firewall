"""Evidence for D8: ``httpx.ASGITransport`` buffers the response, so incremental SSE, time to
first byte and mid-stream cancel need a real socket. The probe pins the behaviour so the
deviation from R1.1's wording (streaming and cancel cells use loopback uvicorn) is evidenced."""

from __future__ import annotations

import asyncio

import pytest

httpx = pytest.importorskip("httpx")


def test_asgi_transport_buffers_the_body_until_the_app_returns() -> None:
    state = {"gate_set_before_app_finished": False}

    async def go() -> None:
        gate = asyncio.Event()

        async def app(scope: dict[str, object], receive: object, send: object) -> None:
            await send({"type": "http.response.start", "status": 200, "headers": []})  # type: ignore[operator]
            await send({"type": "http.response.body", "body": b"first", "more_body": True})  # type: ignore[operator]
            try:
                await asyncio.wait_for(gate.wait(), timeout=0.5)
                state["gate_set_before_app_finished"] = True
            except TimeoutError:
                pass
            await send({"type": "http.response.body", "body": b"", "more_body": False})  # type: ignore[operator]

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://t"  # type: ignore[arg-type]
        ) as client:
            async with client.stream("GET", "/") as response:
                async for _chunk in response.aiter_bytes():
                    gate.set()  # a streaming transport would deliver "first" before the app ends

    asyncio.run(go())
    assert state["gate_set_before_app_finished"] is False, (
        "httpx.ASGITransport now streams; revisit D8 (streaming cells may use it)"
    )
