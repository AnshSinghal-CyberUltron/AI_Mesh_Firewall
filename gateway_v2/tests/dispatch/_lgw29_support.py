"""Builders for the GW29 routing tests."""

from __future__ import annotations

from gateway_v2.domain.incident import (
    IsolationOrigin,
    IsolationRecord,
    ModelProfile,
    ModelStatus,
    RequestConstraints,
)


def iso(model: str, fallback: str | None, *, malformed: bool = False) -> IsolationRecord:
    return IsolationRecord(
        model, fallback, IsolationOrigin.OPERATOR, "", None, 1000.0, False, 1, malformed,
    )


def status(
    model: str, *, killed: bool = False, isolation: IsolationRecord | None = None,
) -> ModelStatus:
    return ModelStatus(model, killed, isolation)


def profile(name: str, tags: frozenset[str] = frozenset(), ceiling: int = 1) -> ModelProfile:
    return ModelProfile(name, "p", tags, ceiling)


def constraints(
    *,
    known: bool = True,
    allowed: frozenset[str] | None = None,
    required: frozenset[str] = frozenset(),
    rank: int = 0,
) -> RequestConstraints:
    return RequestConstraints(known, allowed, required, rank)
