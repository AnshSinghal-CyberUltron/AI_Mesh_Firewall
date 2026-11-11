"""GW26: a rule applies only on the surfaces it names.

Runbook L26-1: a chat-only block is a block on chat and an allow on MCP and RAG.
Omitting the surface keeps the phase-only selection the resolver had before.
"""

from __future__ import annotations

from types import SimpleNamespace

from gateway_v2.domain import (
    Action,
    Category,
    Disposition,
    ExecutionPlan,
    FailurePosture,
    Finding,
    FindingStatus,
    Mode,
    Phase,
    Rule,
    RuleScope,
    StreamingMode,
    Surface,
)
from gateway_v2.edge.dev_components import phase_resolver_factory
from gateway_v2.edge.phase_resolvers import resolver_for
from gateway_v2.resolve.decision import PlanResolver
from gateway_v2.resolve.resolver import resolve


def _finding() -> Finding:
    return Finding(
        detector="gw26",
        detector_version="1",
        category=Category.PROMPT_INJECTION,
        status=FindingStatus.EXECUTED,
        confidence=1.0,
        spans=(),
        evidence=None,
    )


def _rule(*surfaces: Surface) -> Rule:
    return Rule(
        rule_id="competitor-chat",
        category=Category.PROMPT_INJECTION,
        mode=Mode.ENFORCE,
        action=Action.BLOCK,
        threshold=None,
        priority=1,
        scope=RuleScope.BOTH,
        on_unavailable=FailurePosture.FAIL_CLOSED,
        surfaces=frozenset(surfaces),
    )


def _plan(rule: Rule) -> ExecutionPlan:
    return ExecutionPlan(
        org_id="dev-org",
        epoch=1,
        sequence=1,
        content_hash="a" * 64,
        compiled_at=0.0,
        rules=(rule,),
        required_detectors=frozenset(),
        streaming_mode=StreamingMode.INCREMENTAL,
        integrity_locked=False,
    )


def test_chat_only_rule_blocks_chat_and_allows_mcp_and_rag() -> None:
    """L26-1. Chat blocks. MCP and RAG do not select the rule."""
    plan = _plan(_rule(Surface.CHAT))
    finding = _finding()
    chat = resolve((finding,), plan, Phase.INPUT, Surface.CHAT)
    mcp = resolve((finding,), plan, Phase.INPUT, Surface.MCP)
    rag = resolve((finding,), plan, Phase.INPUT, Surface.RAG)
    assert chat.disposition is Disposition.BLOCK
    assert chat.deciding_rules == ("competitor-chat",)
    assert mcp.disposition is Disposition.ALLOW
    assert mcp.deciding_rules == ()
    assert rag.disposition is Disposition.ALLOW
    assert rag.deciding_rules == ()


def test_omitted_surface_keeps_phase_only_selection() -> None:
    """Callers that do not name a surface still select a chat-only rule."""
    plan = _plan(_rule(Surface.CHAT))
    decision = resolve((_finding(),), plan, Phase.INPUT)
    assert decision.disposition is Disposition.BLOCK


def test_resolver_for_binds_chat_and_an_mcp_override() -> None:
    """The chat route's surface is chat. MCP passes its own surface over that route."""
    plan = _plan(_rule(Surface.CHAT))
    factory = phase_resolver_factory()
    route = SimpleNamespace(resolver=object(), phase_resolvers=factory, surface=Surface.CHAT)
    chat = resolver_for(route, plan, Phase.INPUT)
    mcp = resolver_for(route, plan, Phase.INPUT, surface=Surface.MCP)
    assert isinstance(chat, PlanResolver) and chat.surface is Surface.CHAT
    assert isinstance(mcp, PlanResolver) and mcp.surface is Surface.MCP
    assert chat.decide((_finding(),)).disposition is Disposition.BLOCK
    assert mcp.decide((_finding(),)).disposition is Disposition.ALLOW
