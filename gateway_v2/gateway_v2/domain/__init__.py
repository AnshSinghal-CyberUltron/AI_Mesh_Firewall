"""Shared value types. The lowest layer. Upper layers import this; it imports none of them."""

from gateway_v2.domain.category import Category, parse_category
from gateway_v2.domain.channels import TEXT_CHANNELS, TextChannel, unscanned_channels
from gateway_v2.domain.context import RequestContext, StageStep, apply_stage
from gateway_v2.domain.decision import (
    Decision,
    DecisionRecord,
    Disposition,
    FindingDisposition,
    Transformation,
    most_restrictive,
)
from gateway_v2.domain.finding import Finding, FindingStatus, Span
from gateway_v2.domain.locks import FRESH_MS, PG_GRACE_MS, RELAXED_HOLDBACK_CLASSES, InFlightKill
from gateway_v2.domain.plan import (
    Action,
    ExecutionPlan,
    FailurePosture,
    Mode,
    Phase,
    PlanUnavailable,
    PlanUnknownTenant,
    Rule,
    RuleScope,
    StreamingMode,
    Surface,
    is_newer,
)

__all__ = (
    "TEXT_CHANNELS",
    "Action",
    "Category",
    "Decision",
    "DecisionRecord",
    "Disposition",
    "ExecutionPlan",
    "FRESH_MS",
    "FailurePosture",
    "Finding",
    "FindingDisposition",
    "FindingStatus",
    "InFlightKill",
    "Mode",
    "PG_GRACE_MS",
    "Phase",
    "PlanUnavailable",
    "PlanUnknownTenant",
    "RELAXED_HOLDBACK_CLASSES",
    "RequestContext",
    "Rule",
    "RuleScope",
    "Span",
    "StageStep",
    "StreamingMode",
    "Surface",
    "TextChannel",
    "Transformation",
    "apply_stage",
    "is_newer",
    "most_restrictive",
    "parse_category",
    "unscanned_channels",
)
