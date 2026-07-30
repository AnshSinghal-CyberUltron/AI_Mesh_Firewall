"""ROOT-CAUSE (2026-07-30): a clean tool call was BLOCKED under a block posture.

An enforcing ``detector`` rule (e.g. "Detector all (input)" action=block) arms the
render-leak MASK floor whenever it is APPLICABLE to the scan — regardless of whether its
class actually matched the payload. So a clean call (matched_rule_ids empty, finding_count=0)
reached the block decision with ``render_floor="block"`` armed and ``_enforce_blocks`` blocked
it with ZERO findings — making a blanket block posture over-block every clean call.

Fix: the block-posture FLOOR (``_enforce_blocks``) applies only to an ACTUAL match; a bare
armed render_floor with no matched rule falls through to the mask floor (which never blocks).
A real (encoded) threat still matches via _evaluate_rule_mcp and blocks.
"""
import pytest
import mcp_scan_orchestrator as orch
from policy_engine import EvaluationResult

CLEAN = {"query": "zeroshield firewall stars"}


def _run(ev, enforcement, payload=CLEAN):
    with pytest.MonkeyPatch().context() as mp:
        mp.setattr(orch, "evaluate_mcp_policies", lambda *a, **k: ev)
        return orch._mcp_policy_pass_sync(
            payload,
            policies=[{"id": 1}],
            serialized=orch._safe_json(payload),
            scan_direction="input",
            enforcement=enforcement,
            tool_name="search_repositories",
            actor=None,
        )


def test_render_floor_armed_no_match_does_not_block_under_block_posture():
    # Enforcing detector rule APPLICABLE (render_floor armed) but class did NOT match this
    # clean payload (matched_rule_ids empty). A block posture must NOT block it.
    ev = EvaluationResult(action="monitor", matched_rule_ids=[], render_floor="block")
    new_payload, findings, blocked, rfields, redacted = _run(ev, "block")
    assert blocked is False, "clean call over-blocked by a bare render_floor under block posture"
    assert new_payload == CLEAN
    assert findings == []


def test_real_match_still_blocks_under_block_posture():
    # A genuine class match under a block posture still blocks (the floor applies to a match).
    ev = EvaluationResult(action="block", matched_rule_ids=[1], render_floor="block")
    _, _, blocked, _, _ = _run(ev, "block")
    assert blocked is True


def test_block_floor_over_a_matched_redact_rule_still_blocks():
    # block posture is a FLOOR over any MATCHED rule (even one authored redact).
    ev = EvaluationResult(action="redact", matched_rule_ids=[1])
    _, _, blocked, _, _ = _run(ev, "block")
    assert blocked is True


def test_explicit_block_rule_action_blocks_even_under_tag():
    # An explicit block rule ACTION implies a match and blocks under a non-monitor posture.
    ev = EvaluationResult(action="block", matched_rule_ids=[1])
    _, _, blocked, _, _ = _run(ev, "tag")
    assert blocked is True


def test_render_floor_only_under_tag_does_not_block():
    # Under tag too, a bare armed render_floor (no match) never blocks (mask-only floor).
    ev = EvaluationResult(action="monitor", matched_rule_ids=[], render_floor="block")
    _, _, blocked, _, _ = _run(ev, "tag")
    assert blocked is False
