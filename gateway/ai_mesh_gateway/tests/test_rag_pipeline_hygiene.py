"""Pipeline hygiene: RAG-19 (not-run vs. clean), RAG-20 (escalation honesty), RAG-24 (dead wrapper).

RAG-19 — the guardrails-only fast path (ranker AND generator disabled, the
default) returned ``flagged_documents: []`` / ``anomalous_documents: []``. The
ranker is the SOLE producer of both, so those empty lists asserted a clean
document scan for a scan that never ran. main.py forwards the verdict verbatim
to the client; a prior red-team read the same literals as proof that anomaly
detection was hardcoded to empty.

RAG-20 — a "block" verdict jumped escalation_level to 2 ("strict"). Every block
path returns immediately, so no stage could observe it: the audit trail claimed
strict escalation that nothing applied.

RAG-24 — RAGOrchestrator (dead, zero production call sites) hardcoded
input_scanner=None, so reviving it would silently disable QueryStage input
scanning.
"""
from __future__ import annotations

import logging

import pytest

from rag_orchestrator import RAGOrchestrator
from rag_pipeline import RAGFirewallPipeline, get_escalation_config


class _V:
    def __init__(self, action="allow", threat_type="", confidence=0.0, detail="",
                 matched_patterns=None, flagged_documents=None):
        self.action = action
        self.threat_type = threat_type
        self.confidence = confidence
        self.detail = detail
        self.matched_patterns = matched_patterns or []
        self.flagged_documents = flagged_documents or []


class _Guard:
    """ContextGuard stub. ``scan`` drives the batch content-scan verdict."""

    def __init__(self, scan=None):
        self._scan = scan or _V(action="allow")

    def detect_embedding_anomaly(self, distances, threshold):
        return []

    async def scan_documents(self, documents, query_text):
        return self._scan

    async def scan_single_document(self, content):
        return _V(action="allow")


class _Client:
    async def query(self, **kw):
        return [
            {"id": "d1", "content": "Refunds within 30 days.", "score": 0.9, "distance": 0.1},
            {"id": "d2", "content": "Shipping takes 5 days.", "score": 0.8, "distance": 0.2},
        ]


def _pipe(config=None, guard=None):
    return RAGFirewallPipeline(
        input_scanner=None, context_guard=guard or _Guard(), leakage_detector=None,
        vector_clients={}, circuit_breaker=None, rate_limiter=None,
        telemetry=None, redis_client=None, config=config or {},
    )


async def _run(pipe, client=_Client(), **kw):
    base = dict(
        query_text="what is the refund policy",
        collection_name="docs",
        project_id="org1-default",
        vector_db_type="pinecone",
        n_results=5,
        vector_client_override=client,
    )
    base.update(kw)
    return await pipe.execute(**base)


def _skipped(audit) -> dict[str, str]:
    return {s["name"]: s["reason"] for s in audit["stages_skipped"]}


# ──────────────── RAG-19: "never ran" must not read as "found nothing" ────────────────


async def test_guardrails_fast_path_marks_document_scan_not_run():
    # Ranker and generator both disabled (the default): nothing produced the
    # flagged/anomalous lists, and the verdict must say so.
    result = await _run(_pipe())

    assert result.scan_verdict["document_content_scan"] == "not_run"


async def test_guardrails_fast_path_records_the_stages_it_skipped():
    result = await _run(_pipe())

    assert _skipped(result.pipeline_audit) == {"ranker": "disabled", "generator": "disabled"}
    assert [s["name"] for s in result.pipeline_audit["stages"]] == ["query", "retriever"]


async def test_fast_path_keeps_the_list_keys_as_lists():
    # Compatibility: rag_orchestrator feeds anomalous_documents straight into
    # RAGVerdict.anomalous_indices, main.py setdefaults flagged_documents, and
    # JS consumers may read .length. The not-run signal is an ADDED key — the
    # existing ones must stay present and stay lists, never None.
    verdict = (await _run(_pipe())).scan_verdict

    assert verdict["flagged_documents"] == []
    assert verdict["anomalous_documents"] == []
    assert isinstance(verdict["flagged_documents"], list)
    assert isinstance(verdict["anomalous_documents"], list)


async def test_a_real_clean_scan_is_distinguishable_from_a_skipped_one():
    # THE regression this file exists for. Both requests report empty
    # flagged/anomalous lists; only the marker separates "the ranker ran and
    # found nothing" from "the ranker never ran".
    skipped = (await _run(_pipe())).scan_verdict
    scanned = (await _run(_pipe({"rag_ranker_enabled": True}))).scan_verdict

    assert skipped["flagged_documents"] == scanned["flagged_documents"] == []
    assert skipped["anomalous_documents"] == scanned["anomalous_documents"] == []
    assert skipped["document_content_scan"] == "not_run"
    assert scanned["document_content_scan"] == "ran"


async def test_full_ranker_run_reports_real_findings():
    # Guard flags document 0: the ranker runs, drops it, and the indices in the
    # verdict are real output — not the fast path's placeholder.
    guard = _Guard(_V(action="block", threat_type="prompt_injection", flagged_documents=[0]))
    result = await _run(_pipe({"rag_ranker_enabled": True}, guard))

    assert result.scan_verdict["document_content_scan"] == "ran"
    assert result.scan_verdict["flagged_documents"] == [0]
    assert "ranker" not in _skipped(result.pipeline_audit)
    assert "ranker" in [s["name"] for s in result.pipeline_audit["stages"]]


async def test_policy_forced_ranker_reports_a_real_scan():
    # A policy declaring document-content controls forces the ranker on even
    # with the global flag off — the scan state must follow the actual run.
    result = await _run(_pipe(), policy={"block_sensitive_documents": True})

    assert result.scan_verdict["document_content_scan"] == "ran"
    assert "ranker" not in _skipped(result.pipeline_audit)


async def test_blocked_request_does_not_claim_a_document_scan():
    # No vector client → the retriever blocks. Stages downstream never ran, so
    # the audit records them rather than leaving a silent gap.
    result = await _run(_pipe(), client=None)

    assert result.action == "block"
    assert result.scan_verdict["document_content_scan"] == "not_run"
    assert _skipped(result.pipeline_audit) == {"ranker": "not_reached", "generator": "not_reached"}


async def test_blocked_request_omits_the_document_list_keys():
    # Emitting flagged_documents: [] on a request that terminated before the
    # ranker would re-introduce exactly the assertion RAG-19 is about.
    verdict = (await _run(_pipe(), client=None)).scan_verdict

    assert "flagged_documents" not in verdict
    assert "anomalous_documents" not in verdict


# ──────────────── RAG-20: the audit reports the level stages ran under ────────────────


async def test_block_does_not_claim_strict_escalation():
    # Was escalation_level 2 ("strict") on every blocked request, applied by
    # nothing: the pipeline returns before any stage can consume it.
    audit = (await _run(_pipe(), client=None)).pipeline_audit

    assert audit["final_action"] == "block"
    assert audit["escalation_level"] == 0
    assert audit["escalation_level_applied"] == 0


async def test_stages_record_the_level_they_ran_under():
    audit = (await _run(_pipe({"rag_ranker_enabled": True}))).pipeline_audit

    assert audit["escalation_level_applied"] == 0
    assert all(s["escalation_level"] == 0 for s in audit["stages"])


async def test_escalation_is_reachable_by_explicit_caller_opt_in():
    # context.escalation_level had no caller: level 2 (the only config carrying
    # block_on_any_flag) was structurally unreachable. It is now reachable —
    # by explicit opt-in only.
    audit = (await _run(_pipe({"rag_ranker_enabled": True}), escalation_level=2)).pipeline_audit

    assert audit["escalation_level_applied"] == 2
    assert all(s["escalation_level"] == 2 for s in audit["stages"])


@pytest.mark.parametrize("requested,expected", [(-3, 0), (0, 0), (1, 1), (2, 2), (7, 2)])
async def test_escalation_opt_in_is_clamped(requested, expected):
    audit = (await _run(_pipe(), escalation_level=requested)).pipeline_audit

    assert audit["escalation_level"] == expected


async def test_escalation_defaults_to_zero_for_existing_callers():
    # Every current caller omits the argument: no behaviour change.
    audit = (await _run(_pipe())).pipeline_audit

    assert audit["escalation_level"] == 0
    assert audit["escalation_level_applied"] == 0


def test_no_level_below_strict_arms_block_on_any_flag():
    # Guards the over-block that moving block_on_any_flag down to level 1 would
    # cause: the firewall must not take an action the operator did not select.
    assert get_escalation_config(0).block_on_any_flag is False
    assert get_escalation_config(1).block_on_any_flag is False
    assert get_escalation_config(2).block_on_any_flag is True


def test_flag_verdicts_still_escalate():
    # The block bump is gone; the flag accumulation that stages DO observe stays.
    from rag_pipeline.context import PipelineContext, StageRecord
    from rag_pipeline.contracts import StageVerdict

    ctx = PipelineContext()

    def _add(action):
        ctx.add_stage(StageRecord(stage_name="query", started_at=0.0, completed_at=0.0,
                                  verdict=StageVerdict(action=action)))

    _add("flag")
    assert ctx.escalation_level == 1
    _add("flag")
    assert ctx.escalation_level == 2
    _add("block")
    assert ctx.escalation_level == 2  # already there; a block neither raises nor lowers
    assert ctx.blocked_by == "query"


def test_block_alone_does_not_raise_the_level():
    from rag_pipeline.context import PipelineContext, StageRecord
    from rag_pipeline.contracts import StageVerdict

    ctx = PipelineContext()
    ctx.add_stage(StageRecord(stage_name="retriever", started_at=0.0, completed_at=0.0,
                              verdict=StageVerdict(action="block")))

    assert ctx.escalation_level == 0
    assert ctx.escalation_level_applied == 0
    assert ctx.blocked_by == "retriever"


# ──────────────── RAG-24: the dead wrapper cannot be revived silently ────────────────


def test_orchestrator_cannot_be_built_without_an_input_scanner():
    with pytest.raises(TypeError):
        RAGOrchestrator(context_guard=_Guard(), vector_clients={}, config={})


def test_orchestrator_input_scanner_cannot_be_passed_positionally():
    # Keyword-only: disabling input scanning has to be spelled out at the call site.
    with pytest.raises(TypeError):
        RAGOrchestrator(_Guard(), {}, {}, None)


def test_orchestrator_explicit_none_scanner_constructs_and_warns(caplog):
    with caplog.at_level(logging.WARNING, logger="gateway.rag_orchestrator"):
        orch = RAGOrchestrator(context_guard=_Guard(), vector_clients={}, config={},
                               input_scanner=None)

    assert orch is not None
    assert "RAG-24" in caplog.text


def test_orchestrator_accepts_a_real_scanner_without_warning(caplog):
    scanner = object()
    with caplog.at_level(logging.WARNING, logger="gateway.rag_orchestrator"):
        RAGOrchestrator(context_guard=_Guard(), vector_clients={}, config={},
                        input_scanner=scanner)

    assert "RAG-24" not in caplog.text
