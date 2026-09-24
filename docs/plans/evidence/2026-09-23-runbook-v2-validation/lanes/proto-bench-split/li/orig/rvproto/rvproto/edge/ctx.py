"""Request-scoped frozen context values shared by chat.py and respond.py."""

from __future__ import annotations

from dataclasses import dataclass

from rvproto.admit.admission import Grant
from rvproto.domain.decision import Decision, DispatchAuthorization
from rvproto.domain.plan import ExecutionPlan
from rvproto.domain.request import ChatRequest


@dataclass(frozen=True, slots=True)
class Ctx:
    rid: str
    t0: int
    chat: ChatRequest
    grant: Grant
    plan: ExecutionPlan
    passthrough: tuple[tuple[str, str], ...]
    ticket: int  # admission ticket (admit/overload.py)


@dataclass(frozen=True, slots=True)
class InputResult:
    decision: Decision
    auth: DispatchAuthorization | None
    body: bytes
    verification: str
    stages: str
    audited: bool


def headers_for(ctx: Ctx, disposition: str, stages: str) -> list[tuple[bytes, bytes]]:
    return [(b"x-rv-disposition", disposition.encode()),
            (b"x-rv-plan-version", ctx.plan.version.encode()),
            (b"x-rv-stages", stages.encode())]


def blocked_stages(stages: str, audited: bool) -> str:
    return f"{stages},dispatch:S,out:S,audit:{'E' if audited else 'U'}"
