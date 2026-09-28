"""LGW04-1, LGW04-3, LGW04-5. Shuffle and audit-skip live tests are GW15."""

from __future__ import annotations

import random
from dataclasses import FrozenInstanceError

import pytest

from gateway_v2.domain import (
    FRESH_MS,
    PG_GRACE_MS,
    RELAXED_HOLDBACK_CLASSES,
    Category,
    Decision,
    DecisionRecord,
    Disposition,
    Finding,
    FindingDisposition,
    FindingStatus,
    InFlightKill,
    Phase,
    RequestContext,
    StageStep,
    apply_stage,
    most_restrictive,
    unscanned_channels,
)
from gateway_v2.domain.category import parse_category


def _context() -> RequestContext:
    return RequestContext(
        request_id="req-1",
        org_id="org-1",
        phase=Phase.INPUT,
        input_text="hello",
        findings=(),
        plan_version="v1",
    )


def test_lgw04_1_request_context_is_frozen() -> None:
    context = _context()
    with pytest.raises(FrozenInstanceError):
        context.org_id = "other"  # type: ignore[misc]


def test_lgw04_3_unknown_category_rejected() -> None:
    with pytest.raises(TypeError, match="unknown category"):
        Finding(
            detector="semantic",
            detector_version="1",
            category="injection",  # type: ignore[arg-type]
            status=FindingStatus.EXECUTED,
            confidence=0.9,
            spans=(),
            evidence=None,
        )


def test_prompt_injection_is_the_only_spelling() -> None:
    assert parse_category("prompt_injection") is Category.PROMPT_INJECTION
    with pytest.raises(TypeError):
        parse_category("injection")


def test_skipped_and_unavailable_are_distinct_in_the_audit_record() -> None:
    skipped = Finding(
        detector="semantic",
        detector_version="1",
        category=Category.PROMPT_INJECTION,
        status=FindingStatus.SKIPPED,
        confidence=None,
        spans=(),
        evidence=None,
    )
    executed = Finding(
        detector="pii.email",
        detector_version="1",
        category=Category.PII,
        status=FindingStatus.EXECUTED,
        confidence=0.99,
        spans=(),
        evidence=None,
    )
    unavailable = Finding(
        detector="semantic",
        detector_version="1",
        category=Category.JAILBREAK,
        status=FindingStatus.UNAVAILABLE,
        confidence=None,
        spans=(),
        evidence=None,
    )
    decision = Decision(
        disposition=Disposition.ALLOW,
        per_finding=(),
        transformations=(),
        findings=(skipped, executed, unavailable),
        plan_version="v1",
        deciding_rules=(),
        unavailable_detectors=("semantic",),
    )
    record = DecisionRecord(request_id="req-1", plan_version="v1", decision=decision)
    assert record.statuses() == (
        FindingStatus.SKIPPED,
        FindingStatus.EXECUTED,
        FindingStatus.UNAVAILABLE,
    )
    assert FindingStatus.SKIPPED is not FindingStatus.EXECUTED


def test_block_survives_a_redact_on_another_finding() -> None:
    items = (Disposition.REDACT, Disposition.BLOCK, Disposition.FLAG)
    assert most_restrictive(items) is Disposition.BLOCK


def test_lgw04_5_ten_thousand_sequences_are_a_pure_fold() -> None:
    rng = random.Random(4)
    categories = tuple(Category)
    statuses = (
        (FindingStatus.EXECUTED, 0.5),
        (FindingStatus.SKIPPED, None),
        (FindingStatus.UNAVAILABLE, None),
    )
    start = _context()
    for _ in range(10_000):
        built: list[StageStep] = []
        for index in range(rng.randint(1, 4)):
            status, confidence = rng.choice(statuses)
            built.append(
                StageStep(
                    detector=f"d{index}",
                    detector_version="1",
                    category=categories[rng.randrange(len(categories))],
                    status=status,
                    confidence=confidence,
                )
            )
        folded = start
        replay = start
        for step in built:
            folded = apply_stage(folded, step)
            replay = apply_stage(replay, step)
        assert folded == replay
        assert len(folded.findings) == len(built)
        assert start.findings == ()


def test_owner_locks_are_the_v3_values() -> None:
    assert FRESH_MS == 5_000
    assert PG_GRACE_MS == 16_000
    assert InFlightKill.CUT_NEXT_CHUNK.value == "cut_next_chunk"
    assert RELAXED_HOLDBACK_CLASSES == frozenset({"uuid", "sha256", "url", "base64"})
    assert unscanned_channels() == ()


def test_decision_rejects_a_weaker_request_disposition() -> None:
    with pytest.raises(ValueError, match="most restrictive"):
        Decision(
            disposition=Disposition.ALLOW,
            per_finding=(
                FindingDisposition(detector="pii", disposition=Disposition.BLOCK),
            ),
            transformations=(),
            findings=(),
            plan_version="v1",
            deciding_rules=(),
            unavailable_detectors=(),
        )
