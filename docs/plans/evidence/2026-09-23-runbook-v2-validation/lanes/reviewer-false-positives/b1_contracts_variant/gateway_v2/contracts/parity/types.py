"""Frozen C2 / replay records. No module-level mutable containers."""

from __future__ import annotations

from dataclasses import dataclass

SURFACES: tuple[str, ...] = (
    "chat",
    "completions",
    "embeddings",
    "responses",
    "mcp",
    "rag",
    "vector",
)
STREAMABLE: frozenset[str] = frozenset({"chat", "completions", "responses"})
TENANTS: tuple[str, ...] = ("tenant-block", "tenant-monitor")
MODES: tuple[str, ...] = ("stream", "nonstream")
C2_TARGET = 50_000
BUCKETS: tuple[str, ...] = ("IDENTICAL", "EXPECTED", "UNEXPECTED", "WIRE")
SDK_FIELDS: frozenset[str] = frozenset(
    {
        "id",
        "object",
        "choices",
        "error",
        "model",
        "usage",
        "data",
        "tool_calls",
        "finish_reason",
        "created",
    },
)


@dataclass(frozen=True)
class C2Record:
    request_id: str
    surface: str
    mode: str
    tenant: str
    plan_version: str
    headers: tuple[tuple[str, str], ...]
    prompt: str
    captured_at: int


@dataclass(frozen=True)
class ReplayOutcome:
    request_id: str
    disposition: str
    transformations: tuple[str, ...]
    provider_bytes: bytes
    client_object: str
    client_model: str
    client_id: str
    created: int


@dataclass(frozen=True)
class LedgerEntry:
    rule_id: str
    row_id: str
    c3_score: str
    diff_signature: str
    commit_timestamp: int
