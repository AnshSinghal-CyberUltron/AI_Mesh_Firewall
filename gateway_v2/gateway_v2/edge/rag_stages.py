"""GW31 stage functions: query, retriever, ranker, generator.

The split with ``dispatch/rag_*`` is by dependency: scanning needs the detector and the
resolver, which sit above ``dispatch``, so the scanning lives here and the pure steps (retrieval,
ranking, context binding) live below. Every scan binds its GW26 ``pipeline_stage`` through
:func:`resolver_at`; no stage can forget the name. Every refusal is a declared value-code raised
as :class:`RagRefused`; chunk text and the canary are never placed in an exception or a log line.
"""
from __future__ import annotations

import asyncio
import dataclasses
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from gateway_v2.dispatch.rag_context import (
    GROUND_OK,
    bind_chunks,
    canary_present,
    frame_context,
    merge_decisions,
    mint_canary,
)
from gateway_v2.dispatch.rag_ranker import rank
from gateway_v2.dispatch.rag_retrieval import retrieve
from gateway_v2.domain import posture
from gateway_v2.domain.decision import Decision, Disposition
from gateway_v2.domain.finding import Finding
from gateway_v2.domain.plan import ExecutionPlan, Phase, Surface
from gateway_v2.domain.rag import (
    STAGE_GENERATOR,
    STAGE_QUERY,
    STAGE_RANKER,
    STAGE_RETRIEVER,
    ScoredChunk,
    StageNote,
)
from gateway_v2.edge.dev_components import bind_surface
from gateway_v2.edge.input_enforcement import REFUSE_INPUT_UNMASKABLE, InputScan, scan_deltas
from gateway_v2.edge.phase_resolvers import resolver_for
from gateway_v2.edge.rag_deps import RagDeps
from gateway_v2.edge.routes import ChatRoute
from gateway_v2.egress.output_guard import OutputBlocked, OutputResolver, apply_decision
from gateway_v2.plan.rag_settings import EffectiveRag
from gateway_v2.resolve.decision import PlanResolver
from gateway_v2.runtime.resources_rag import RagBounds

__all__ = (
    "GeneratorOutcome",
    "QueryOutcome",
    "RagRefused",
    "RankedOutcome",
    "RetrievedOutcome",
    "detector_for",
    "redact_text",
    "resolver_at",
    "run_generator",
    "scan_text",
    "run_query",
    "run_ranker",
    "run_retriever",
)

_Detector = Callable[[str], Sequence[Finding]]


class RagRefused(Exception):
    """Stop the request with a declared value-code. Carries a code only, never content."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class QueryOutcome:
    text: str
    vector: tuple[float, ...]
    note: StageNote


@dataclass(frozen=True, slots=True)
class RetrievedOutcome:
    chunks: tuple[ScoredChunk, ...]
    note: StageNote


@dataclass(frozen=True, slots=True)
class RankedOutcome:
    chunks: tuple[ScoredChunk, ...]
    note: StageNote


@dataclass(frozen=True, slots=True)
class GeneratorOutcome:
    texts: tuple[tuple[ScoredChunk, str], ...]
    answer: str | None
    canary_hit: bool
    note: StageNote


def resolver_at(
    chat: ChatRoute, plan: ExecutionPlan, phase: Phase, surface: Surface, stage: str | None
) -> OutputResolver:
    """Bind the GW26 pipeline stage. Fail closed if the resolver cannot carry one.

    With ``stage=None`` the resolver skips every stage-scoped rule, a silent fail-open of admin
    intent, so anything that is not a :class:`PlanResolver` is refused here.
    """
    resolver = resolver_for(chat, plan, phase, surface=surface)
    if not isinstance(resolver, PlanResolver):
        raise RagRefused(posture.RAG_INTERNAL_ERROR)
    return dataclasses.replace(resolver, stage=stage)


def detector_for(
    chat: ChatRoute,
    deps: RagDeps,
    eff: EffectiveRag,
    surface: Surface,
    phase: Phase = Phase.INPUT,
    stage: str | None = None,
) -> _Detector:
    """Tier-2 detector when ``rag_tier2_enabled``, else the deterministic-only one.

    The detector itself carries a direction (``DeterministicFindings.phase`` selects which
    customer conditions it evaluates), so the OUTPUT scan must use the output detector, as MCP
    does. Using the input detector for the generator would silently skip output-scoped rules.
    """
    if phase is Phase.OUTPUT:
        out = chat.output_detector or chat.error_scanner
        base = out if eff.rag_tier2_enabled else (deps.detector_tier1_output or out)
    else:
        base = chat.input_detector if eff.rag_tier2_enabled else deps.detector_tier1
    return bind_surface(base, surface, stage)


async def scan_text(
    chat: ChatRoute, detector: _Detector, resolver: OutputResolver, channel: str, text: str
) -> InputScan:
    try:
        return await chat.executor.run(
            lambda: scan_deltas(
                ((channel, text),), join_runs=(), detector=detector, resolver=resolver
            ),
            exports=chat.exports,
        )
    except Exception as exc:  # noqa: BLE001 - saturated or raising executor: refuse, never inline
        raise RagRefused("scan_failure") from exc


def redact_text(text: str, decision: Decision) -> str | None:
    """Apply ``decision`` and verify by bytes that no raw finding span survives."""
    try:
        out = apply_decision(text, decision, 0)
    except (ValueError, OutputBlocked):
        return None
    for item in decision.transformations:
        raw = text[item.span.start : item.span.end]
        if raw and raw in out and raw != item.replacement:
            return None
    return out


def _rules(decision: Decision | None) -> tuple[str, ...]:
    return () if decision is None else decision.deciding_rules


async def run_query(
    chat: ChatRoute,
    deps: RagDeps,
    plan: ExecutionPlan,
    eff: EffectiveRag,
    query: object,
    org_id: str,
    query_bytes: int,
) -> QueryOutcome:
    if not isinstance(query, str) or not query.strip() or len(query.encode()) > query_bytes:
        raise RagRefused(posture.RAG_QUERY_INVALID)
    scan = await scan_text(
        chat,
        detector_for(chat, deps, eff, Surface.RAG, Phase.INPUT, STAGE_QUERY),
        resolver_at(chat, plan, Phase.INPUT, Surface.RAG, STAGE_QUERY),
        "rag:query",
        query,
    )
    if scan.refuse_code is not None or scan.decision is None:
        raise RagRefused(scan.refuse_code or "scan_failure")
    decision = scan.decision
    if decision.disposition is Disposition.BLOCK:
        raise RagRefused(posture.RAG_QUERY_BLOCKED)
    text = query
    if decision.disposition is Disposition.REDACT:
        redacted = redact_text(query, decision)
        if redacted is None:
            raise RagRefused(REFUSE_INPUT_UNMASKABLE)
        text = redacted
    embedder = deps.embedder
    if embedder is None:
        raise RagRefused(posture.RAG_EMBEDDER_UNAVAILABLE)
    try:
        vectors = await embedder.embed(org_id, (text,))
    except Exception as exc:  # noqa: BLE001
        raise RagRefused(posture.RAG_EMBEDDER_UNAVAILABLE) from exc
    if vectors is None or len(vectors) != 1 or len(vectors[0]) != embedder.dimension:
        raise RagRefused(posture.RAG_EMBEDDER_UNAVAILABLE)
    note = StageNote(STAGE_QUERY, decision.disposition.value, "", 0, _rules(decision))
    return QueryOutcome(text, vectors[0], note)


async def run_retriever(
    chat: ChatRoute,
    deps: RagDeps,
    plan: ExecutionPlan,
    eff: EffectiveRag,
    *,
    surface: Surface,
    org_id: str,
    project: str,
    store_id: str,
    wire: str,
    vector: tuple[float, ...],
    timeout_s: float,
) -> RetrievedOutcome:
    got = await retrieve(
        deps.registry,
        deps.chunk_store,
        store_id=store_id,
        org_id=org_id,
        project=project,
        wire=wire,
        vector=vector,
        top_k=eff.top_k,
        timeout_s=timeout_s,
    )
    if not got.ok:
        raise RagRefused(got.code)
    detector = detector_for(chat, deps, eff, surface, Phase.INPUT, STAGE_RETRIEVER)
    resolver = resolver_at(chat, plan, Phase.INPUT, surface, STAGE_RETRIEVER)
    kept: list[ScoredChunk] = []
    blocked = failed = 0
    rules: list[str] = []
    for item in got.chunks:
        try:
            scan = await scan_text(chat, detector, resolver, "rag:chunk", item.chunk.text)
        except RagRefused:
            failed += 1
            continue
        if scan.refuse_code is not None or scan.decision is None:
            failed += 1
            continue
        rules.extend(r for r in scan.decision.deciding_rules if r not in rules)
        if scan.decision.disposition is Disposition.BLOCK:
            blocked += 1
            continue
        kept.append(dataclasses.replace(item, decision=scan.decision))
    dropped = got.dropped_unscorable + blocked + failed
    reason = f"unscorable={got.dropped_unscorable},blocked={blocked},failed={failed}"
    return RetrievedOutcome(
        tuple(kept), StageNote(STAGE_RETRIEVER, "allow", reason, dropped, tuple(rules))
    )


async def run_ranker(
    chat: ChatRoute,
    deps: RagDeps,
    plan: ExecutionPlan,
    eff: EffectiveRag,
    *,
    surface: Surface,
    query_vector: tuple[float, ...],
    chunks: tuple[ScoredChunk, ...],
    bounds: RagBounds,
) -> RankedOutcome:
    outcome = rank(
        query_vector,
        chunks,
        threshold=eff.rag_relevance_threshold,
        max_documents=eff.rag_max_documents,
        bounds=bounds,
    )
    detector = detector_for(chat, deps, eff, surface, Phase.INPUT, STAGE_RANKER)
    resolver = resolver_at(chat, plan, Phase.INPUT, surface, STAGE_RANKER)
    kept: list[ScoredChunk] = []
    blocked = 0
    rules: list[str] = []
    for item in outcome.chunks:
        scan = await scan_text(chat, detector, resolver, "rag:chunk", item.chunk.text)
        if scan.refuse_code is not None or scan.decision is None:
            blocked += 1  # an uncomputable scan excludes the chunk (fail closed)
            continue
        rules.extend(r for r in scan.decision.deciding_rules if r not in rules)
        if scan.decision.disposition is Disposition.BLOCK:
            blocked += 1
            continue
        merged = merge_decisions(item.decision, scan.decision)
        kept.append(dataclasses.replace(item, decision=merged))
    dropped = (
        outcome.dropped_anomalous
        + outcome.dropped_duplicate
        + outcome.dropped_threshold
        + outcome.dropped_unscorable
        + blocked
    )
    reason = (
        f"threshold={outcome.dropped_threshold},anomalous={outcome.dropped_anomalous},"
        f"duplicate={outcome.dropped_duplicate},unscorable={outcome.dropped_unscorable},"
        f"blocked={blocked}"
    )
    return RankedOutcome(
        tuple(kept), StageNote(STAGE_RANKER, "allow", reason, dropped, tuple(rules))
    )


async def run_generator(
    chat: ChatRoute,
    deps: RagDeps,
    plan: ExecutionPlan,
    eff: EffectiveRag,
    *,
    org_id: str,
    project: str,
    query: str,
    chunks: tuple[ScoredChunk, ...],
    canary_bytes: int,
    timeout_s: float,
) -> GeneratorOutcome:
    withheld = RagRefused(posture.RAG_OUTPUT_WITHHELD)
    bound, removed = bind_chunks(chunks, org_id, project)
    texts: list[str] = []
    notes: list[str] = []
    if removed:
        notes.append(f"unbound={removed}")
    for item in bound:
        text = item.chunk.text
        decision = item.decision
        if decision is not None and decision.disposition is Disposition.REDACT:
            if eff.rag_redaction_enabled:
                redacted = redact_text(text, decision)
                if redacted is None:
                    raise withheld
                text = redacted
            else:
                notes.append("redact_not_applied")
        texts.append(text)
    canary = mint_canary(deps.token_source, canary_bytes)
    frame = frame_context(tuple(texts), canary)
    if eff.rag_tier2_enabled:
        grounder = deps.grounder
        if grounder is None:
            raise RagRefused(posture.RAG_GROUNDING_UNAVAILABLE)
        try:
            verdict = await asyncio.wait_for(grounder.check(frame.text, query), timeout_s)
        except Exception as exc:  # noqa: BLE001
            raise RagRefused(posture.RAG_GROUNDING_UNAVAILABLE) from exc
        if verdict.code != GROUND_OK:
            raise RagRefused(posture.RAG_GROUNDING_UNAVAILABLE)
    answer: str | None = None
    if deps.answerer is not None:
        try:
            answer = await asyncio.wait_for(deps.answerer.answer(frame.text, query), timeout_s)
        except Exception as exc:  # noqa: BLE001
            raise RagRefused(posture.RAG_ANSWER_UNAVAILABLE) from exc
        if answer is None:
            raise RagRefused(posture.RAG_ANSWER_UNAVAILABLE)
    detector = detector_for(chat, deps, eff, Surface.RAG, Phase.OUTPUT, STAGE_GENERATOR)
    resolver = resolver_at(chat, plan, Phase.OUTPUT, Surface.RAG, STAGE_GENERATOR)
    rules: list[str] = []

    async def gate(channel: str, text: str) -> str:
        try:
            scan = await scan_text(chat, detector, resolver, channel, text)
        except RagRefused as exc:
            raise withheld from exc
        if scan.refuse_code is not None or scan.decision is None:
            raise withheld
        decision = scan.decision
        rules.extend(r for r in decision.deciding_rules if r not in rules)
        if decision.disposition is Disposition.BLOCK:
            raise withheld
        if decision.disposition is Disposition.REDACT:
            if not eff.rag_redaction_enabled:
                notes.append("redact_not_applied")
                return text
            redacted = redact_text(text, decision)
            if redacted is None:
                raise withheld
            return redacted
        return text

    if answer is not None:
        answer = await gate("rag:answer", answer)
    final = [await gate("rag:chunk", t) for t in texts]
    outgoing = tuple(final) + (() if answer is None else (answer,))
    if canary_present(outgoing, canary):
        raise withheld
    note = StageNote(STAGE_GENERATOR, "allow", ",".join(notes), removed, tuple(rules))
    return GeneratorOutcome(
        tuple(zip(bound, final, strict=True)), answer, False, note
    )
