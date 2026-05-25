"""
Pipeline-Aware RAG Firewall: 4-stage governed pipeline.

Stages: QueryStage -> RetrieverStage -> RankerStage -> GeneratorStage
Each stage makes independent policy decisions with inter-stage escalation.
"""
from .pipeline import RAGFirewallPipeline
from .contracts import PipelineResult, StageVerdict, DocumentManifest
from .context import PipelineContext, StageRecord
from .escalation import get_escalation_config, ESCALATION_LEVELS

__all__ = [
    "RAGFirewallPipeline",
    "DocumentManifest",
    "PipelineResult",
    "StageVerdict",
    "PipelineContext",
    "StageRecord",
    "get_escalation_config",
    "ESCALATION_LEVELS",
]
