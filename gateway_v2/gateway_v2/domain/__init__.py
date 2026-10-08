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
from gateway_v2.domain.identity import Principal
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
from gateway_v2.domain.state import (
    START,
    ZERO,
    Cursor,
    Manifest,
    SignedRecord,
    Stamp,
    StateKind,
    StateOp,
    StoreDataUnavailable,
    Version,
)

__all__ = (
    "START",
    "TEXT_CHANNELS",
    "ZERO",
    "Action",
    "Category",
    "Cursor",
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
    "Manifest",
    "Mode",
    "PG_GRACE_MS",
    "Phase",
    "PlanUnavailable",
    "PlanUnknownTenant",
    "Principal",
    "RELAXED_HOLDBACK_CLASSES",
    "RequestContext",
    "Rule",
    "RuleScope",
    "SignedRecord",
    "Span",
    "StageStep",
    "Stamp",
    "StateKind",
    "StateOp",
    "StoreDataUnavailable",
    "StreamingMode",
    "Surface",
    "TextChannel",
    "Transformation",
    "Version",
    "apply_stage",
    "is_newer",
    "most_restrictive",
    "parse_category",
    "unscanned_channels",
)
