"""GW31 RAG value types. Codes live in ``domain/posture.py``; this module holds shapes only.

Every type is frozen and slotted. The two ``None`` defaults on :class:`RagSettings` are
deliberate: numeric defaults are capacity-position values and live only in the
``ResourceContract`` (``plan/rag_settings.py::effective_settings`` fills them in).
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum

from gateway_v2.domain.decision import Decision

__all__ = (
    "ISOLATION_MODES",
    "STAGE_GENERATOR",
    "STAGE_QUERY",
    "STAGE_RANKER",
    "STAGE_RETRIEVER",
    "STAGES",
    "Chunk",
    "Namespace",
    "RagSettings",
    "ScoredChunk",
    "StageNote",
    "TrustLevel",
)

STAGE_QUERY = "query"
STAGE_RETRIEVER = "retriever"
STAGE_RANKER = "ranker"
STAGE_GENERATOR = "generator"
STAGES = (STAGE_QUERY, STAGE_RETRIEVER, STAGE_RANKER, STAGE_GENERATOR)
ISOLATION_MODES = frozenset({"project", "org"})


@dataclass(frozen=True, slots=True)
class RagSettings:
    """The six per-org RAG settings carried on the plan."""

    rag_enabled: bool = True
    vector_db_isolation: str = "project"
    rag_max_documents: int | None = None
    rag_relevance_threshold: float | None = None
    rag_redaction_enabled: bool = True
    rag_tier2_enabled: bool = True


class TrustLevel(IntEnum):
    """Higher is more trusted. ``HIGH`` is never assignable by a caller."""

    UNTRUSTED = 0
    LOW = 1
    MEDIUM = 2
    HIGH = 3


@dataclass(frozen=True, slots=True)
class Namespace:
    """The (org, project, collection) triple. Built only by ``dispatch.rag_namespace``."""

    org_id: str
    project: str
    collection: str


@dataclass(frozen=True, slots=True)
class Chunk:
    chunk_id: str
    text: str
    vector: tuple[float, ...]
    org_id: str
    project: str
    source: str
    trust: TrustLevel


@dataclass(frozen=True, slots=True)
class ScoredChunk:
    chunk: Chunk
    relevance: float
    decision: Decision | None = None


@dataclass(frozen=True, slots=True)
class StageNote:
    """One stage's contribution to the trace. Counts and rule ids only, never text."""

    stage: str
    action: str
    reason: str = ""
    dropped: int = 0
    rule_ids: tuple[str, ...] = ()
