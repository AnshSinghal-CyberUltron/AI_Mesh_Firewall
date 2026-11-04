"""Shared GW28 test fakes: candidate builders, scripted per-request-safe clients, timelines."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable, Sequence
from dataclasses import dataclass, field
from typing import ClassVar

from gateway_v2.dispatch.openai_upstream import BODY_ENDPOINT_KEY, BODY_OPENAI_KEY
from gateway_v2.dispatch.provider import UpstreamEvent, UpstreamRequest
from gateway_v2.dispatch.routing_types import (
    Candidate,
    CandidateSet,
    RoutingContext,
    RoutingDecision,
)
from gateway_v2.domain.plan import ExecutionPlan, StreamingMode

MAX_LEN = 64
MAX_CANDS = 8
MAX_WEIGHT = 16


def cand(
    cid: str,
    *,
    models: Sequence[str] = ("m",),
    weight: int = 1,
    upstream: str | None = None,
    passthrough: bool = False,
    provider: str | None = None,
) -> Candidate:
    return Candidate(
        candidate_id=cid,
        destination=f"dest-{cid}",
        upstream_model="" if passthrough else (f"up-{cid}" if upstream is None else upstream),
        served_models=frozenset() if passthrough else frozenset(models),
        weight=weight,
        provider_key=cid if provider is None else provider,
        passthrough=passthrough,
    )


def cset(*cands: Candidate, passthrough: bool = False) -> CandidateSet:
    return CandidateSet.build(
        cands,
        max_candidates=MAX_CANDS,
        max_weight=MAX_WEIGHT,
        max_ident_len=MAX_LEN,
        allow_passthrough=passthrough,
    )


def plan(org: str = "org", epoch: int = 1, seq: int = 1, chash: str = "h" * 16) -> ExecutionPlan:
    return ExecutionPlan(
        org_id=org,
        epoch=epoch,
        sequence=seq,
        content_hash=chash,
        compiled_at=0.0,
        rules=(),
        required_detectors=frozenset(),
        streaming_mode=next(iter(StreamingMode)),
        integrity_locked=False,
    )


def ctx(
    model: str = "m",
    *,
    key: str = "rk",
    pl: ExecutionPlan | None = None,
    sink: Callable[[RoutingDecision], None] | None = None,
) -> RoutingContext:
    return RoutingContext.from_plan(
        pl or plan(),
        request_key=key,
        requested_model=model,
        sink=sink or (lambda _d: None),
        max_ident_len=MAX_LEN,
    )


def request(model: str = "m", extra: str = "x") -> UpstreamRequest:
    return UpstreamRequest(
        destination="logical",
        body={
            BODY_OPENAI_KEY: {"model": model, "messages": ({"role": "user", "content": extra},)},
            BODY_ENDPOINT_KEY: "chat/completions",
        },
    )


def text(s: str, *, final: bool = False) -> UpstreamEvent:
    return UpstreamEvent(text_deltas=(("content", s),), final=final)


def role() -> UpstreamEvent:
    return UpstreamEvent(text_deltas=(), final=False)


def err(code: str) -> UpstreamEvent:
    return UpstreamEvent(text_deltas=(), final=True, gateway_error=code)


@dataclass
class Timeline:
    """Open/close/yield log shared by every FakeClient in one test."""

    log: list[tuple[str, str]] = field(default_factory=list)
    live: int = 0
    max_live: int = 0

    def opened(self, who: str) -> None:
        self.live += 1
        self.max_live = max(self.max_live, self.live)
        self.log.append(("open", who))

    def closed(self, who: str) -> None:
        self.live -= 1
        self.log.append(("close", who))

    def opens(self) -> list[str]:
        return [w for k, w in self.log if k == "open"]


class FakeClient:
    """Scripted per-request-safe client. ``script`` items: event, or float (sleep seconds)."""

    per_request_safe: ClassVar[bool] = True

    def __init__(
        self,
        name: str,
        script: Sequence[UpstreamEvent | float | BaseException],
        tl: Timeline,
        *,
        hang_after: bool = False,
        close_raises: bool = False,
        seen: list[UpstreamRequest] | None = None,
    ) -> None:
        self.name = name
        self.script = tuple(script)
        self.tl = tl
        self.hang_after = hang_after
        self.close_raises = close_raises
        self.seen = seen if seen is not None else []

    def open(self, request: UpstreamRequest) -> AsyncIterator[UpstreamEvent]:
        self.seen.append(request)
        return self._gen()

    async def _gen(self) -> AsyncIterator[UpstreamEvent]:
        self.tl.opened(self.name)
        try:
            for item in self.script:
                if isinstance(item, BaseException):
                    raise item
                if isinstance(item, float):
                    await asyncio.sleep(item)
                    continue
                yield item
            if self.hang_after:
                await asyncio.sleep(3600)
        finally:
            self.tl.closed(self.name)
            if self.close_raises:
                raise RuntimeError("close failed")

    async def abort(self) -> None:
        return None
