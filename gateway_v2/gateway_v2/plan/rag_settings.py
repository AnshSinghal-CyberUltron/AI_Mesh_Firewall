"""GW31 RAG settings: structural parse at compile time, contract clamp at request time.

``parse_rag_settings`` validates STRUCTURE AND TYPES ONLY and needs no contract, so no call site
that compiles a plan has to hold one. A published ``rag_enabled: false`` is therefore honoured
without any boot wiring. The contract ceiling is applied by :func:`effective_settings` on the
read path: ``rag_max_documents`` above the ceiling is clamped DOWN, never widened.
"""
from __future__ import annotations

import dataclasses
import json
import math
from collections.abc import Mapping
from dataclasses import dataclass, fields

from gateway_v2.domain.plan import ExecutionPlan
from gateway_v2.domain.rag import ISOLATION_MODES, RagSettings
from gateway_v2.plan.compiler import CompileError
from gateway_v2.runtime.resources import ResourceContract

__all__ = (
    "EffectiveRag",
    "canonical_settings_json",
    "effective_settings",
    "encode_rag_settings",
    "parse_rag_settings",
    "settings_of",
)

_KNOWN = frozenset(f.name for f in fields(RagSettings))
_BOOLS = ("rag_enabled", "rag_redaction_enabled", "rag_tier2_enabled")


def _is_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def parse_rag_settings(raw: Mapping[str, object] | None) -> RagSettings:
    """Raise :class:`CompileError` on any unknown key or wrong type. ``None`` gives defaults."""
    if raw is None:
        return RagSettings()
    if not isinstance(raw, Mapping):
        raise CompileError("rag_settings must be an object")
    unknown = sorted(str(key) for key in raw if key not in _KNOWN)
    if unknown:
        raise CompileError(f"unknown rag_settings key {unknown[0]!r}")
    values: dict[str, object] = {}
    for name in _BOOLS:
        if name in raw:
            if not isinstance(raw[name], bool):
                raise CompileError(f"rag_settings.{name} must be a boolean")
            values[name] = raw[name]
    if "vector_db_isolation" in raw:
        mode = raw["vector_db_isolation"]
        if not isinstance(mode, str) or mode not in ISOLATION_MODES:
            raise CompileError("rag_settings.vector_db_isolation must be 'project' or 'org'")
        values["vector_db_isolation"] = mode
    if raw.get("rag_max_documents") is not None:
        docs = raw["rag_max_documents"]
        if isinstance(docs, bool) or not isinstance(docs, int) or docs < 1:
            raise CompileError("rag_settings.rag_max_documents must be an integer >= 1")
        values["rag_max_documents"] = docs
    if raw.get("rag_relevance_threshold") is not None:
        thr = raw["rag_relevance_threshold"]
        if not _is_number(thr) or not isinstance(thr, (int, float)):
            raise CompileError("rag_settings.rag_relevance_threshold must be a number")
        if not math.isfinite(thr) or not 0.0 <= thr <= 1.0:
            raise CompileError("rag_settings.rag_relevance_threshold must be within [0, 1]")
        values["rag_relevance_threshold"] = float(thr)
    return RagSettings(**values)  # type: ignore[arg-type]


def encode_rag_settings(settings: RagSettings) -> dict[str, object] | None:
    """Only non-default fields; ``None`` when everything is default."""
    default = RagSettings()
    out = {
        f.name: getattr(settings, f.name)
        for f in fields(RagSettings)
        if getattr(settings, f.name) != getattr(default, f.name)
    }
    return out or None


def canonical_settings_json(settings: RagSettings) -> str:
    body = encode_rag_settings(settings) or {}
    return json.dumps(body, sort_keys=True, separators=(",", ":"))


def settings_of(plan: ExecutionPlan) -> RagSettings:
    return plan.rag


@dataclass(frozen=True, slots=True)
class EffectiveRag:
    """The six settings with every ``None`` filled in from the contract."""

    rag_enabled: bool
    vector_db_isolation: str
    rag_max_documents: int
    rag_relevance_threshold: float
    rag_redaction_enabled: bool
    rag_tier2_enabled: bool
    top_k: int
    settings_clamped: bool

    def capped(self, top_k: int) -> EffectiveRag:
        """The same settings with a smaller per-request ``top_k``. Never widens."""
        return dataclasses.replace(self, top_k=min(top_k, self.top_k))


def effective_settings(plan: ExecutionPlan, contract: ResourceContract) -> EffectiveRag:
    s = plan.rag
    ceiling = contract.rag_max_documents_ceiling()
    wanted = s.rag_max_documents if s.rag_max_documents is not None else ceiling
    docs = min(wanted, ceiling)
    threshold = (
        s.rag_relevance_threshold
        if s.rag_relevance_threshold is not None
        else contract.rag_default_relevance_threshold()
    )
    return EffectiveRag(
        rag_enabled=s.rag_enabled,
        vector_db_isolation=s.vector_db_isolation,
        rag_max_documents=docs,
        rag_relevance_threshold=threshold,
        rag_redaction_enabled=s.rag_redaction_enabled,
        rag_tier2_enabled=s.rag_tier2_enabled,
        top_k=min(docs, contract.vector_max_result_count()),
        settings_clamped=wanted > ceiling,
    )
