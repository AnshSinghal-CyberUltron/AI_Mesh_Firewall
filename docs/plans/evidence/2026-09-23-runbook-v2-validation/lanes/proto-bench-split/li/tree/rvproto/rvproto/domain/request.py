"""Request-scoped frozen values: wire request, principal, error spec."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class Segment:
    """One scannable text field of the request; path locates it in the JSON doc."""

    path: tuple[str | int, ...]
    role: str
    text: str


@dataclass(frozen=True, slots=True)
class ChatRequest:
    model: str
    stream: bool
    max_tokens: int | None
    segments: tuple[Segment, ...]
    body: bytes  # exact bytes received; forwarded verbatim when nothing is transformed
    doc: Any  # parsed JSON, treated as read-only; REDACT builds a new document


@dataclass(frozen=True, slots=True)
class Principal:
    key_id: str
    org_id: str
    rate_per_s: float
    burst: float
    epoch: int


@dataclass(frozen=True, slots=True)
class ErrorSpec:
    """A rejection as data. Only edge/ renders it into an HTTP response."""

    status: int
    type: str
    code: str
    message: str
    param: str | None = None
    retry_after_s: float | None = None
