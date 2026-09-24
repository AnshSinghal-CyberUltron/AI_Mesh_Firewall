"""LGW02-1..5 — parity harness gates."""

from __future__ import annotations

from dataclasses import replace

import pytest

from gateway_v2.contracts.parity.c2 import coverage_gaps, coverage_report, generate_c2
from gateway_v2.contracts.parity.c3 import backtick_blocks, score_v1
from gateway_v2.contracts.parity.clock import FrozenClock
from gateway_v2.contracts.parity.differ import classify
from gateway_v2.contracts.parity.ledger import LedgerTimestampError, lookup
from gateway_v2.contracts.parity.replay import replay, replay_twice
from gateway_v2.contracts.parity.types import C2_TARGET, LedgerEntry
from gateway_v2.contracts.parity.v1_oracle import v1_disposition

CLOCK = FrozenClock(epoch=1_704_067_200)
RUN_AT = 1_704_067_200


def test_lgw02_1_v1_self_replay_identical() -> None:
    records = generate_c2(C2_TARGET, CLOCK)
    assert len(records) == C2_TARGET
    assert coverage_gaps(records) == ()
    assert min(coverage_report(records).values()) > 0
    first, second = replay_twice(records, CLOCK, v1_disposition)
    assert len(first) == C2_TARGET
    for left, right in zip(first, second, strict=True):
        result = classify(left, right, (), RUN_AT, RUN_AT)
        assert result.bucket == "IDENTICAL", result


def test_lgw02_2_disposition_change_without_ledger_is_unexpected() -> None:
    rec = generate_c2(1, CLOCK)[0]
    left = replay(rec, CLOCK, lambda _t: "allow")
    right = replace(left, disposition="redact", transformations=("mask",))
    result = classify(right, left, (), RUN_AT, None)
    assert result.bucket == "UNEXPECTED"
    assert result.c1_gap is None


def test_lgw02_3_backdated_ledger_rejected() -> None:
    rec = generate_c2(1, CLOCK)[0]
    left = replay(rec, CLOCK, lambda _t: "allow")
    right = replace(left, disposition="redact", transformations=("mask",))
    signature = (
        f"disp:{left.disposition}->{right.disposition}"
        f"|tx:{','.join(left.transformations)}->{','.join(right.transformations)}"
    )
    entry = LedgerEntry(
        rule_id="P8-BACKTICK",
        row_id="10.2.2-benign-inline-code",
        c3_score="fpr=1.00-backtick",
        diff_signature=signature,
        commit_timestamp=100,
    )
    with pytest.raises(LedgerTimestampError, match="post-dates"):
        lookup(signature, (entry,), run_started_at=1_000, git_commit_time=5_000)
    with pytest.raises(LedgerTimestampError, match="back-dated"):
        lookup(signature, (entry,), run_started_at=5_000, git_commit_time=4_000)
    with pytest.raises(LedgerTimestampError, match="post-dates"):
        classify(left, right, (entry,), run_started_at=1_000, git_commit_time=5_000)


def test_lgw02_4_sdk_field_change_is_wire() -> None:
    rec = generate_c2(1, CLOCK)[0]
    left = replay(rec, CLOCK, lambda _t: "allow")
    right = replace(left, client_object="chat.completion.chunk")
    result = classify(left, right, (), RUN_AT, None)
    assert result.bucket == "WIRE"
    assert result.c1_gap is not None
    assert "object" in result.c1_gap


def test_lgw02_5_c3_reproduces_backtick_and_paraphrase() -> None:
    assert backtick_blocks() == 5
    scores = {item.family: item for item in score_v1() if item.label == "malicious"}
    paraphrase = scores["paraphrase"]
    assert paraphrase.n >= 10
    assert paraphrase.rate == 0.0
    benign = {item.family: item for item in score_v1() if item.label == "benign"}
    assert benign["developer_traffic"].flagged >= 5
    assert benign["general_benign"].rate == 0.0
