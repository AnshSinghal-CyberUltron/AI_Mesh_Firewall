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
    # Escalation level in force when THIS stage ran. Stamped by
    # PipelineContext.add_stage so the audit reports the level a stage actually
    # executed under, never a level derived after the fact. (RAG-20)
    escalation_level_in: int = 0

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
    # Stages that did NOT run, with why: [{"name": "ranker", "reason": "disabled"}].
    # A control that never ran must never be readable as a control that ran and
    # passed — see mark_stage_skipped. (RAG-19)
    skipped_stages: list[dict[str, str]] = field(default_factory=list)
    final_action: str = "allow"
    # Per-org audit gate: when the caller's org has telemetry_enabled=False
    # (control-plane audit_logging_enabled OFF), per-stage pipeline telemetry
    # must be suppressed — mirroring the chat path's _emit_telemetry gate. The
    # handler resolves the per-org value and passes it into execute(). Defaults
    # True so unauthenticated / no-org callers keep emitting (backward compat).
    telemetry_enabled: bool = True

    def add_stage(self, record: StageRecord) -> None:
        """Add a stage record and auto-escalate based on verdict."""
        record.escalation_level_in = self.escalation_level
        self.stages.append(record)
        # A "block" verdict used to jump the level straight to 2 ("strict").
        # Every block path in RAGFirewallPipeline returns immediately, so no
        # later stage could ever observe that level: the sole effect was an
        # audit trail (and per-stage telemetry) claiming strict escalation that
        # no stage ever applied. A block is terminal — record it and leave the
        # level where the stages actually ran. (RAG-20)
        if record.verdict.action == "flag":
            self.escalation_level = min(self.escalation_level + 1, 2)

    def mark_stage_skipped(self, stage_name: str, reason: str) -> None:
        """Record a stage that never ran, and why.

        "Evaluated and found nothing" and "never evaluated" must not serialize
        identically. RankerStage is the SOLE producer of flagged/anomalous
        document indices, so a pipeline that skips it still emits empty lists —
        indistinguishable from a clean scan, and read by a prior review as
        proof that anomaly detection was a hardcoded empty literal. (RAG-19)

        First reason wins: a stage skipped by configuration keeps that reason
        even if a later pass would also call it unreached.
        """
        if any(s.get("name") == stage_name for s in self.skipped_stages):
            return
        self.skipped_stages.append({"name": stage_name, "reason": reason})

    @property
    def executed_stages(self) -> set[str]:
        """Names of the stages that actually ran."""
        return {s.stage_name for s in self.stages}

    @property
    def blocked_by(self) -> str:
        """Name of the stage whose verdict terminated the pipeline, else ""."""
        for s in self.stages:
            if s.verdict.action == "block":
                return s.stage_name
        return ""

    @property
    def escalation_level_applied(self) -> int:
        """Highest escalation level any executed stage actually ran under. (RAG-20)"""
        return max((s.escalation_level_in for s in self.stages), default=0)

    @property
    def total_latency_ms(self) -> float:
        return sum(s.latency_ms for s in self.stages)

    def to_audit_dict(self) -> dict[str, Any]:
        """Serialize for telemetry metadata and API response."""
        return {
            "request_id": self.request_id,
            "escalation_level": self.escalation_level,
            # What the stages ran under, as opposed to where the level ended up.
            # These diverge whenever a "flag" raises the level after the last
            # stage that could consume it. (RAG-20)
            "escalation_level_applied": self.escalation_level_applied,
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
                    "escalation_level": s.escalation_level_in,
                }
                for s in self.stages
            ],
            # Controls that never ran. Absent from "stages" is not enough: a
            # reader cannot tell a skipped control from one that passed. (RAG-19)
            "stages_skipped": [dict(s) for s in self.skipped_stages],
        }
