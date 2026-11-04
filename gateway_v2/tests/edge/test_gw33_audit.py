"""GW33 task 9.3/9.5: review flag, canary booleans and bytes action on the audit record."""
from __future__ import annotations

import json
import random

from gateway_v2.audit.record import AuditPhase, AuditRecord
from gateway_v2.domain import (
    Action,
    Category,
    Disposition,
    Phase,
    StreamingMode,
)
from gateway_v2.edge.audit_emit import emit_chat_audit, note_stream_output, review_item_wanted
from gateway_v2.edge.canary_inject import CanaryTicket
from gateway_v2.edge.dev_components import DeterministicFindings
from gateway_v2.edge.request_holds import RequestHolds
from tests.e2e.gw33_support import TOKEN, plan_of, rule, run, split

_INCR = StreamingMode.INCREMENTAL
_STRICT = StreamingMode.STRICT_WITHHOLD
_TEXT = "ssn 123-45-6789 and host 10.0.0.9 here"


def _audit(result, ticket=None):  # type: ignore[no-untyped-def]
    holds = RequestHolds()
    holds.note_tenant("org")
    holds.note_request("zs-test")
    if ticket is not None:
        holds.note_canary(ticket)
    note_stream_output(holds, result.stats, "1.1.x")
    records: list[AuditRecord] = []
    emit_chat_audit(lambda r: records.append(r) is None, holds, None, console={"source": "x"})
    return [r for r in records if r.phase is AuditPhase.OUTPUT][0]


def test_property_8_review_flag_parity_across_modes() -> None:
    """Feature: gw33-output-parity, Property 8: Review flag consistency. Validates 4.5, 4.6."""
    # Feature: gw33-output-parity, Property 8: Review flag consistency
    rng = random.Random(33_008)
    for _ in range(40):
        flagged = {}
        for mode in (_INCR, _STRICT):
            plan = plan_of(mode, rule("out_ip_leak", Category.IP_LEAK, Action.FLAG,
                                      "output.ip_leak"))
            result = run(plan, split(_TEXT, rng))
            flagged[mode] = (
                result.stats.review_required,  # type: ignore[attr-defined]
                _audit(result).detail["review_required"],
                result.stats.output_action,  # type: ignore[attr-defined]
            )
        assert flagged[_INCR] == flagged[_STRICT] == (True, True, "flag")
        clean = run(plan_of(_INCR, rule("out_ip_leak", Category.IP_LEAK, Action.FLAG,
                                        "output.ip_leak")), split("nothing to see", rng))
        assert clean.stats.review_required is False  # type: ignore[attr-defined]


def test_the_output_record_carries_flags_codes_and_bytes_action_but_no_content() -> None:
    plan = plan_of(_STRICT, rule("out_ip_leak", Category.IP_LEAK, Action.REWRITE, "output.ip_leak",
                                 template="[internal address]"))
    result = run(plan, [("output_content", _TEXT)])
    record = _audit(result, CanaryTicket(TOKEN))
    detail = record.detail
    assert detail["review_required"] is True and detail["bytes_action"] == "rewrite"
    assert detail["canary_checked"] is True and detail["canary_leaked"] is False
    serialized = json.dumps(record.as_mapping(), default=str)
    for secret in ("10.0.0.9", "[internal address]", TOKEN):
        assert secret not in serialized, secret


def test_a_flag_only_canary_next_to_a_redact_in_another_channel_reports_both() -> None:
    """M-2: the audit aggregate keeps the canary leak, the redact bytes, rules and codes."""
    base = DeterministicFindings(Phase.OUTPUT, 1.0)
    for mode in (_INCR, _STRICT):
        plan = plan_of(
            mode,
            rule("out_canary", Category.CANARY, Action.FLAG, "output.canary"),
            rule("r-pii", Category.PII, Action.REDACT),
        )
        result = run(plan, [("output_reasoning", f"thinking {TOKEN} here"),
                            ("output_content", "my ssn is 123-45-6789 ok")],
                     ticket=CanaryTicket(TOKEN), base=base)
        stats = result.stats
        assert stats.canary_leaked is True  # type: ignore[attr-defined]
        assert stats.bytes_action == "redact"  # type: ignore[attr-defined]
        assert stats.review_required is True  # type: ignore[attr-defined]
        assert "123-45-6789" not in result.channel("output_content")
        decision = stats.output_decision  # type: ignore[attr-defined]
        assert {"out_canary", "r-pii"} <= set(decision.deciding_rules)
        assert decision.disposition is Disposition.REDACT
        record = _audit(result, CanaryTicket(TOKEN))
        assert record.detail["canary_leaked"] is True and record.detail["bytes_action"] == "redact"


def test_review_queue_policy_gates_only_redact_and_rewrite_items() -> None:
    plan = plan_of(_STRICT, rule("r", Category.PII, Action.REDACT))
    base = DeterministicFindings(Phase.OUTPUT, 1.0)
    decision = run(plan, [("output_content", "ssn 123-45-6789")], base=base).stats.output_decision  # type: ignore[attr-defined]
    assert decision.review_required and decision.disposition is Disposition.REDACT
    assert review_item_wanted(decision, lambda: True) is True
    assert review_item_wanted(decision, lambda: False) is False  # in-record flag is still set
    flag = run(plan_of(_STRICT, rule("r", Category.PII, Action.FLAG),
                       rule("other", Category.SECRET, Action.REDACT)),
               [("output_content", "ssn 123-45-6789")], base=base).stats.output_decision  # type: ignore[attr-defined]
    assert review_item_wanted(flag, lambda: False) is True  # FLAG and BLOCK always queue
