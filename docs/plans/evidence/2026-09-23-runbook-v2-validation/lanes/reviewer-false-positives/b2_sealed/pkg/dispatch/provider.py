from __future__ import annotations
from pkg.resolve.decision import DispatchAuthorization

CALLS: list[str] = []


def call_provider(auth: DispatchAuthorization, payload: bytes) -> None:
    CALLS.append(auth.decision.disposition.value)
