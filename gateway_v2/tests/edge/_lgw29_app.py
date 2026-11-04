"""A real-gate app harness for the GW29 edge tests.

Builds ``build_app`` over a REAL :class:`IncidentGate` (real :class:`KillSwitchSnapshot`, real
:class:`IncidentState`), the production :class:`KillSwitchControlFactory`, counting admission /
budget / provider fakes and an audit list, so a test can engage a kill and assert what the whole
edge did: status, headers, provider calls, net spend and the audit records.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

import httpx

from gateway_v2.admit.grant import Admitted, ShedVerdict
from gateway_v2.admit.revocation import RevocationLedger
from gateway_v2.audit.record import AuditRecord
from gateway_v2.detect.holdback import scan as holdback_scan
from gateway_v2.dispatch.provider import UpstreamEvent, UpstreamRequest
from gateway_v2.edge.app import build_app
from gateway_v2.edge.inflight_source import KillSwitchControlFactory
from gateway_v2.egress.output_guard import MinimalOutputResolver
from gateway_v2.runtime.kinds import HardwareSignals
from gateway_v2.runtime.resources import ResourceContract, from_signals
from tests.admit._lgw29_support import GateRig, ks
from tests.edge._chat_lifecycle_fixtures import (
    DEFAULT_ORG_ID,
    ResolvingIdentity,
    warm_snapshot_source,
)

ORG = DEFAULT_ORG_ID
__all__ = ("ORG", "Harness", "ks", "words")


def contract() -> ResourceContract:
    rss = 400 * 1024 * 1024
    signals = HardwareSignals(
        cpu_quota=4.0,
        memory_limit=rss * 8,
        fd_limit=4096,
        cpu_source="t",
        mem_source="t",
        fd_source="t",
    )
    return from_signals(signals, target_p99_ms=20.0, utilization_cap=0.75, per_worker_rss=rss)


def words(text: str = "one two three four five six") -> tuple[UpstreamEvent, ...]:
    pieces = [w + " " for w in text.split(" ")]
    return tuple(
        UpstreamEvent(text_deltas=(("content", p),), final=i == len(pieces) - 1)
        for i, p in enumerate(pieces)
    )


class _Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        self.t += 1e-6
        return self.t


class _Metrics:
    def exposition(self) -> str:
        return ""


class CountingProvider:
    """Counts opens, keeps each request body, and runs ``between`` after each yielded event."""

    def __init__(self, script: Sequence[UpstreamEvent], between: Callable[[int], None] | None):
        self.script = tuple(script)
        self.between = between
        self.open_calls = 0
        self.bodies: list[Any] = []
        self.aborts = 0

    def open(self, request: UpstreamRequest) -> AsyncIterator[UpstreamEvent]:
        self.open_calls += 1
        self.bodies.append(request.body)
        return self._replay()

    async def _replay(self) -> AsyncIterator[UpstreamEvent]:
        for index, event in enumerate(self.script):
            yield event
            if self.between is not None:
                self.between(index)

    async def abort(self) -> None:
        self.aborts += 1


class CountingBudget:
    """Records every spend and refund so the NET spend of a request is observable."""

    def __init__(self) -> None:
        self.spent = 0
        self.refunded = 0
        self.refund_calls: list[tuple[int, str, str]] = []

    async def evaluate(
        self,
        cost: int,
        *,
        request_id: str,
        owner: str = "",
        principal: object = None,
        key_hash: str = "",
    ) -> Admitted:
        self.spent += cost
        return Admitted(owner_id=owner, request_id=request_id, admitted_at=0.0)

    def refund(self, cost: int, *, request_id: str, owner: str = "") -> None:
        self.refunded += cost
        self.refund_calls.append((cost, request_id, owner))

    @property
    def net(self) -> int:
        return self.spent - self.refunded


class CountingAdmission:
    def __init__(self) -> None:
        self.admitted = 0
        self.completed = 0

    async def admit(
        self, owner_id: str, request_id: str, enqueued_at: float
    ) -> Admitted | ShedVerdict:
        self.admitted += 1
        return Admitted(owner_id=owner_id, request_id=request_id, admitted_at=0.0)

    def complete(self, request_id: str) -> None:
        self.completed += 1


@dataclass
class Result:
    status: int
    headers: httpx.Headers
    body: bytes
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", "replace")

    def code(self) -> object:
        parsed = json.loads(self.body)
        return parsed["error"]["code"]


class Harness:
    """One app over a real gate. ``rig`` is the kill-switch rig; call ``engage`` to apply kills."""

    def __init__(
        self,
        script: Sequence[UpstreamEvent] | None = None,
        *,
        org: str = ORG,
        aliases: dict[str, str] | None = None,
        rig: dict[str, Any] | None = None,
        mcp: object | None = None,
        between: Callable[[Harness, int], None] | None = None,
        audit: bool = True,
    ) -> None:
        self.rig = GateRig(aliases=aliases, **(rig or {}))
        self.ledger = RevocationLedger(clock=self.rig.mono, slots=64, retention_s=600.0)
        self.budget = CountingBudget()
        self.admission = CountingAdmission()
        self.records: list[AuditRecord] = []
        self.provider = CountingProvider(
            words() if script is None else script,
            None if between is None else (lambda i: between(self, i)),
        )
        self.org = org
        self.recheck: Callable[..., Any] = self.rig.gate.recheck
        self.app = build_app(
            contract=contract(),
            provider=self.provider,
            scanner=holdback_scan,  # type: ignore[arg-type]
            resolver=MinimalOutputResolver(),
            metrics_registry=_Metrics(),
            clock=_Clock(),  # type: ignore[arg-type]
            identity=ResolvingIdentity(org),
            snapshot_source=warm_snapshot_source(org_id=org),
            admission=self.admission,
            quota=self.budget,  # type: ignore[arg-type]
            incident_gate=self.rig.gate,
            incident_recheck=lambda **kw: self.recheck(**kw),
            control_factory=KillSwitchControlFactory(self.rig.snap, self.ledger),
            audit=self.records.append if audit else None,
            mcp=mcp,  # type: ignore[arg-type]
        )

    def engage(self, *scopes: str) -> None:
        self.rig.engage(*[ks(scope, feed_seq=i + 1) for i, scope in enumerate(scopes)])

    async def call(self, path: str, body: dict[str, object]) -> Result:
        transport = httpx.ASGITransport(app=self.app)  # type: ignore[arg-type]
        async with httpx.AsyncClient(transport=transport, base_url="http://gw29.test") as client:
            response = await client.post(
                path,
                content=json.dumps(body).encode(),
                headers={"authorization": "Bearer test", "content-type": "application/json"},
            )
        return Result(response.status_code, response.headers, response.content)


def chat(model: str = "gpt-test", *, stream: bool = False) -> dict[str, object]:
    return {"model": model, "stream": stream, "messages": [{"role": "user", "content": "hi"}]}


def completion(model: str = "gpt-test", *, stream: bool = False) -> dict[str, object]:
    return {"model": model, "stream": stream, "prompt": "hi"}


def responses(model: str = "gpt-test", *, stream: bool = False) -> dict[str, object]:
    return {"model": model, "stream": stream, "input": "hi"}


ENDPOINTS: tuple[tuple[str, Callable[..., dict[str, object]]], ...] = (
    ("/v1/chat/completions", chat),
    ("/v1/completions", completion),
    ("/v1/responses", responses),
)
