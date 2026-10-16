"""Semantic detection, windowing and the per-request FPR budget (GW10) — local half.

This package fills the local half of semantic injection detection: role-scoped
input admission, language posture + confusable fold, tokenizer-aware input
windowing, the per-request FPR-budget algebra, the max-score-per-category
aggregation, and the :class:`SemanticDetector` orchestrator over the shipped GW08
``GuardBackend``. The real guard model, GPU throughput, and the production tokenizer
bytes are deferred (GW20 / T01 / GW15, R11).

``__all__`` re-exports the public surface the detector composes: the orchestrator and
its config, the role axis + admission, the language posture + confusable fold +
multilingual reporting, the windowing plan + seam + over-length reject, the FPR-budget
algebra, and the aggregation rule.
"""

from __future__ import annotations

from gateway_v2.detect.semantic.aggregate import (
    SEMANTIC_CATEGORIES,
    AggregatedCategory,
    aggregate_results,
)
from gateway_v2.detect.semantic.detector import (
    DETECTOR_NAME,
    SemanticConfig,
    SemanticDetector,
)
from gateway_v2.detect.semantic.fpr_budget import (
    FprBudget,
    derive_threshold,
    per_window_fpr_budget,
    request_fpr,
)
from gateway_v2.detect.semantic.language import (
    CONFUSABLE_FOLD,
    SUPPORTED_LANGUAGES,
    UNKNOWN_LANGUAGE,
    LanguagePosture,
    MultilingualReport,
    fold_confusables,
    identify_language,
    posture_for,
)
from gateway_v2.detect.semantic.roles import (
    DEFAULT_ROLE_SCOPE,
    RoleScope,
    RoleScopedSegment,
    SegmentRole,
    admit_segments,
)
from gateway_v2.detect.semantic.windowing import (
    CHARS_PER_TOKEN_MARKDOWN,
    CHARS_PER_TOKEN_PROSE,
    DEFAULT_MAX_WINDOW_COUNT,
    DEFAULT_OVERLAP_TOKENS,
    DEFAULT_TOKEN_BAND,
    OverLengthError,
    TokenizerSeam,
    WindowPlan,
    window_text,
)

__all__ = (
    "CHARS_PER_TOKEN_MARKDOWN",
    "CHARS_PER_TOKEN_PROSE",
    "CONFUSABLE_FOLD",
    "DEFAULT_MAX_WINDOW_COUNT",
    "DEFAULT_OVERLAP_TOKENS",
    "DEFAULT_ROLE_SCOPE",
    "DEFAULT_TOKEN_BAND",
    "DETECTOR_NAME",
    "SEMANTIC_CATEGORIES",
    "SUPPORTED_LANGUAGES",
    "UNKNOWN_LANGUAGE",
    "AggregatedCategory",
    "FprBudget",
    "LanguagePosture",
    "MultilingualReport",
    "OverLengthError",
    "RoleScope",
    "RoleScopedSegment",
    "SegmentRole",
    "SemanticConfig",
    "SemanticDetector",
    "TokenizerSeam",
    "WindowPlan",
    "admit_segments",
    "aggregate_results",
    "derive_threshold",
    "fold_confusables",
    "identify_language",
    "per_window_fpr_budget",
    "posture_for",
    "request_fpr",
    "window_text",
)
