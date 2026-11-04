"""The genuine ``build_app`` under a scripted provider, for the L34-1 SDK matrix.

``ScriptedProvider`` is NEW test code (review R5-5), not the shipped ``StubProviderClient``: it
selects a script from a marker in the last user message (one shared instance serves every cell),
builds fresh per-call state and counts ``closed`` / ``aborted`` per call (R6.1/R6.2 evidence).
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass

from gateway_v2.dispatch.openai_upstream import BODY_OPENAI_KEY
from gateway_v2.dispatch.provider import ProviderClient, UpstreamEvent, UpstreamRequest
from gateway_v2.dispatch.transform import BODY_DELTAS_KEY
from gateway_v2.edge import serve
from gateway_v2.edge.app import ASGIApplication

__all__ = (
    "API_KEY",
    "HOLD",
    "LoopbackServer",
    "ScriptedProvider",
    "build_matrix_app",
    "dev_env",
    "marker",
    "matrix_contract",
)

import asyncio
import threading
import time
from pathlib import Path
from typing import Any

from gateway_v2.runtime.kinds import HardwareSignals
from gateway_v2.runtime.resources import ResourceContract, from_signals

API_KEY = "sk-gw34-matrix"
_PLAN = Path(__file__).resolve().parents[3] / "dev" / "plan.example.json"
#: A script item that blocks the stream (a stalled upstream) until the client cancels.
HOLD: UpstreamEvent = UpstreamEvent(text_deltas=(("hold", ""),), final=False)


def matrix_contract() -> ResourceContract:
    signals = HardwareSignals(
        cpu_quota=4.0, memory_limit=400 * 1024 * 1024 * 8, fd_limit=4096,
        cpu_source="test", mem_source="test", fd_source="test",
    )
    return from_signals(
        signals, target_p99_ms=20.0, utilization_cap=0.75, per_worker_rss=400 * 1024 * 1024
    )


def dev_env(provider_url: str | None = None) -> dict[str, str]:
    env = {
        "AMF_DEV_PROFILE": "1", "AMF_DEV_API_KEY": API_KEY, "AMF_DEV_PLAN_PATH": str(_PLAN),
        "AMF_DEV_MODELS": "gpt-4o-mini", "AMF_DEV_Q_SAFE": "500",
        "AMF_DEV_AUDIT_DRAIN_RATE_PER_S": "1000", "AMF_DEV_AUDIT_BYTES_PER_RECORD": "2765",
        "AMF_DEV_BUDGET": "unlimited",
    }
    if provider_url is not None:
        env["AMF_DEV_PROVIDER_URL"] = provider_url
    return env


class LoopbackServer:
    """uvicorn on ``127.0.0.1:0`` in a thread (D8: ASGITransport buffers, so streaming and cancel
    cells need a real socket). ``port`` is the OS-assigned port once started."""

    def __init__(self, app: object) -> None:
        import uvicorn

        async def asgi3(scope: Any, receive: Any, send: Any) -> None:
            # a plain coroutine function, so uvicorn never mistakes a bound method for ASGI2
            await app(scope, receive, send)  # type: ignore[operator]

        self._server = uvicorn.Server(
            uvicorn.Config(asgi3, host="127.0.0.1", port=0, log_level="warning")
        )
        self._thread = threading.Thread(target=self._server.run, daemon=True)
        self.port = 0

    def __enter__(self) -> LoopbackServer:
        self._thread.start()
        deadline = time.monotonic() + 10
        while not self._server.started:
            if time.monotonic() > deadline:
                raise RuntimeError("loopback server did not start")
            time.sleep(0.01)
        self.port = self._server.servers[0].sockets[0].getsockname()[1]
        return self

    def __exit__(self, *_exc: object) -> None:
        self._server.should_exit = True
        self._thread.join(timeout=10)

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

_MARKER_PREFIX = "SDKMATRIX:"


def marker(name: str) -> str:
    """The message content that selects script ``name`` (put it in the last user message)."""
    return f"{_MARKER_PREFIX}{name}"


@dataclass
class _CallState:
    closed: int = 0
    aborted: int = 0


class ScriptedProvider:
    """A :class:`ProviderClient` that replies with the script named by the request's marker.

    ``scripts`` maps a marker name to a sequence of ``UpstreamEvent``\\ s; the default script
    (marker ``None``) answers any request with no recognised marker. Fresh per-call state (never
    shared across calls, R6.2: a cancelled call must not prevent a later one from succeeding).
    """

    def __init__(self, scripts: Mapping[str, Sequence[UpstreamEvent]], *, default: str) -> None:
        self._scripts = scripts
        self._default = default
        self.calls: list[_CallState] = []

    def _script_for(self, request: UpstreamRequest) -> Sequence[UpstreamEvent]:
        deltas = request.body.get(BODY_DELTAS_KEY)
        if isinstance(deltas, (tuple, list)) and any(
            isinstance(d, (tuple, list)) and d and d[0] == "embeddings_input" for d in deltas
        ):
            return self._scripts["embeddings"]
        body = request.body.get(BODY_OPENAI_KEY)
        name = self._default
        if isinstance(body, Mapping):
            for message in reversed(body.get("messages") or ()):
                if not isinstance(message, Mapping):
                    continue
                content = message.get("content")
                if isinstance(content, str) and content.startswith(_MARKER_PREFIX):
                    name = content[len(_MARKER_PREFIX):]
                    break
        return self._scripts.get(name, self._scripts[self._default])

    def open(self, request: UpstreamRequest) -> AsyncIterator[UpstreamEvent]:
        state = _CallState()
        self.calls.append(state)
        return self._stream(self._script_for(request), state)

    async def _stream(
        self, script: Sequence[UpstreamEvent], state: _CallState
    ) -> AsyncIterator[UpstreamEvent]:
        try:
            for event in script:
                if event is HOLD:
                    await asyncio.sleep(3600)
                yield event
        finally:
            state.closed += 1

    async def abort(self) -> None:
        if self.calls:
            self.calls[-1].aborted += 1


def build_matrix_app(
    *, contract: object, provider: ProviderClient | None, env: Mapping[str, str] | None = None
) -> ASGIApplication:
    """The genuine ``build_app`` (via the dev-profile boot), ``provider`` injected when given."""
    deps, _logs = serve.build_boot_dependencies(
        contract=contract, env=dict(env if env is not None else dev_env())  # type: ignore[arg-type]
    )
    if provider is not None:  # ``None``: keep the env-built real OpenAI upstream (wire mode)
        deps["provider"] = provider
    return serve._assemble_app(deps)
