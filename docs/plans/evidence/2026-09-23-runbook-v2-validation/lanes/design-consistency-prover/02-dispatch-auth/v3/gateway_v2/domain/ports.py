"""Ports (Protocols). Behaviour is injected by edge/, the composition root."""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from typing import Protocol

from gateway_v2.domain.decision import Decision, DispatchAuthorization
from gateway_v2.domain.findings import Finding
from gateway_v2.domain.plan import ExecutionPlan
from gateway_v2.domain.taxonomy import Phase


class Detector(Protocol):
    detector_id: str

    def detect(self, text: str, plan: ExecutionPlan) -> tuple[Finding, ...]: ...


class ResolveFn(Protocol):
    def __call__(
        self, findings: Sequence[Finding], plan: ExecutionPlan, phase: Phase
    ) -> Decision: ...


class DetectFn(Protocol):
    def __call__(self, text: str, plan: ExecutionPlan) -> tuple[Finding, ...]: ...


class ProviderStream(Protocol):
    def __aiter__(self) -> AsyncIterator[bytes]: ...


class ProviderClient(Protocol):
    def open_stream(self, auth: DispatchAuthorization, payload: bytes) -> ProviderStream: ...
