"""PipelineContext accumulator -- audit trail for every RAG request."""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from .contracts import StageVerdict


@dataclass
class StageRecord:
    """Single stage execution record for the audit trail."""

    stage_name: str  # "query" | "retriever" | "ranker" | "generator"
    started_at: float  # time.perf_counter()
    completed_at: float
    verdict: StageVerdict
    document_count_in: int = 0
    document_count_out: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)
    approved_doc_ids: list[str] = field(default_factory=list)
    rejected_doc_ids: list[str] = field(default_factory=list)
    policy_rules_consulted: list[str] = field(default_factory=list)

    @property
    def latency_ms(self) -> float:
        return (self.completed_at - self.started_at) * 1000


@dataclass
class PipelineContext:
    """Accumulates state across all pipeline stages for a single request."""

    request_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    project_id: str = ""
    collection_name: str = ""
    query_text: str = ""
    stages: list[StageRecord] = field(default_factory=list)
    escalation_level: int = 0  # 0=normal, 1=elevated, 2=strict
    final_action: str = "allow"

    def add_stage(self, record: StageRecord) -> None:
        """Add a stage record and auto-escalate based on verdict."""
        self.stages.append(record)
        if record.verdict.action == "block":
            self.escalation_level = 2
        elif record.verdict.action == "flag":
            self.escalation_level = min(self.escalation_level + 1, 2)

    @property
    def total_latency_ms(self) -> float:
        return sum(s.latency_ms for s in self.stages)

    def to_audit_dict(self) -> dict[str, Any]:
        """Serialize for telemetry metadata and API response."""
        return {
            "request_id": self.request_id,
            "escalation_level": self.escalation_level,
            "total_latency_ms": round(self.total_latency_ms, 2),
            "final_action": self.final_action,
            "stages": [
                {
                    "name": s.stage_name,
                    "action": s.verdict.action,
                    "threat_type": s.verdict.threat_type,
                    "confidence": s.verdict.confidence,
                    "latency_ms": round(s.latency_ms, 2),
                    "docs_in": s.document_count_in,
                    "docs_out": s.document_count_out,
                    "approved_doc_ids": s.approved_doc_ids,
                    "rejected_doc_ids": s.rejected_doc_ids,
                    "policy_rules_consulted": s.policy_rules_consulted,
                    "rewritten_text": s.verdict.rewritten_text,
                }
                for s in self.stages
            ],
        }
