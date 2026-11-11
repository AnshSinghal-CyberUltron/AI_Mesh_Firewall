"""``withholds_until_decision`` predicate (strict-withhold-idle-timeout, task 3.1).

The edge layer uses ``withholds_until_decision(plan)`` to decide whether a
stream's idle clock must be driven by upstream liveness (a STRICT_WITHHOLD stream
with an enforcing output rule writes nothing while it accumulates, so the
write-based idle model would blame the client for the gateway's own silence).
This file pins that predicate two ways:

* a truth table over the five relevant plan shapes (INCREMENTAL; strict with an
  enforcing rule; strict with no output rules; strict whose only output rules are
  ``flag`` or ``mode=off``; strict with INPUT-scope-only rules), and
* a drift property (>= 100 randomized plans) that ``withholds_until_decision(plan)``
  equals the ``enforcing_output`` value ``egress.select.select_pipeline`` hands to
  ``StrictWithholdPipeline`` -- so the edge decision can never drift from the
  pipeline selection.

House idiom: seeded ``random.Random``, no ``hypothesis``. These are pure
predicate tests, so no ``asyncio.run`` is needed.
"""

from __future__ import annotations

import random

from gateway_v2.detect.holdback import TokenIndex
from gateway_v2.detect.holdback import scan as _holdback_scan
from gateway_v2.detect.windowing import max_pattern_length
from gateway_v2.domain import (
    Action,
    Category,
    ExecutionPlan,
    FailurePosture,
    Mode,
    Rule,
    RuleScope,
    StreamingMode,
    Surface,
)
from gateway_v2.egress.output_guard import (
    MinimalOutputResolver,
    enforcing_output_rules,
    withholds_until_decision,
)
from gateway_v2.egress.select import select_pipeline
from gateway_v2.egress.strict import StrictWithholdPipeline
from gateway_v2.egress.transport import ScanResultLike, TokenCounterProtocol
from gateway_v2.runtime.holdback_config import load_holdback_config
from gateway_v2.runtime.holdback_metrics import HoldbackMetrics
from gateway_v2.runtime.strict_metrics import StrictMetrics

# --------------------------------------------------------------------------- #
# Builders (mirror the shipped GW13 test helpers)
# --------------------------------------------------------------------------- #


def _rule(
    rule_id: str = "r1",
    *,
    scope: RuleScope = RuleScope.OUTPUT,
    mode: Mode = Mode.ENFORCE,
    action: Action = Action.REDACT,
    category: Category = Category.SECRET,
) -> Rule:
    return Rule(
        rule_id=rule_id,
        category=category,
        mode=mode,
        action=action,
        threshold=None,
        priority=0,
        scope=scope,
        on_unavailable=FailurePosture.FAIL_CLOSED,
        surfaces=frozenset({Surface.CHAT}),
    )


def _plan(
    *rules: Rule,
    mode: StreamingMode = StreamingMode.INCREMENTAL,
) -> ExecutionPlan:
    return ExecutionPlan(
        org_id="org-1",
        epoch=1,
        sequence=1,
        content_hash="0" * 32,
        compiled_at=0.0,
        rules=rules,
        required_detectors=frozenset(),
        streaming_mode=mode,
        integrity_locked=True,
    )


def _scan(
    buf: str,
    *,
    final: bool,
    hold_cap_tokens: int,
    token_index: TokenCounterProtocol,
    window: int,
) -> ScanResultLike:
    """Adapter so the real pure ``holdback.scan`` injects as a ``ScanProtocol``."""
    return _holdback_scan(
        buf,
        final=final,
        hold_cap_tokens=hold_cap_tokens,
        token_index=token_index,  # type: ignore[arg-type]
        window=window,
    )


# --------------------------------------------------------------------------- #
# Truth table
# --------------------------------------------------------------------------- #


def test_incremental_is_never_withholding() -> None:
    # INCREMENTAL writes each chunk as it arrives -> never withholding (3.1).
    plan = _plan(_rule(), mode=StreamingMode.INCREMENTAL)
    assert withholds_until_decision(plan) is False


def test_strict_with_enforcing_rule_withholds() -> None:
    # STRICT_WITHHOLD + an enforcing OUTPUT rule -> withholds until the decision.
    plan = _plan(
        _rule(scope=RuleScope.OUTPUT, mode=Mode.ENFORCE, action=Action.REDACT),
        mode=StreamingMode.STRICT_WITHHOLD,
    )
    assert withholds_until_decision(plan) is True


def test_strict_with_no_output_rules_is_passthrough() -> None:
    # STRICT_WITHHOLD with no rules at all -> nothing to withhold (3.7).
    plan = _plan(mode=StreamingMode.STRICT_WITHHOLD)
    assert withholds_until_decision(plan) is False


def test_strict_with_only_flag_or_off_output_rules_is_passthrough() -> None:
    # STRICT_WITHHOLD whose only output rules are FLAG or mode=OFF are not
    # enforcing, so the stream is pass-through (3.7).
    flag = _rule("flag", scope=RuleScope.OUTPUT, mode=Mode.ENFORCE, action=Action.FLAG)
    off = _rule("off", scope=RuleScope.OUTPUT, mode=Mode.OFF, action=Action.BLOCK)
    plan = _plan(flag, off, mode=StreamingMode.STRICT_WITHHOLD)
    assert withholds_until_decision(plan) is False


def test_strict_with_input_scope_only_rules_is_passthrough() -> None:
    # STRICT_WITHHOLD whose enforcing rules are all INPUT-scope have no OUTPUT
    # enforcement, so the stream withholds nothing (3.7).
    in_rule = _rule("in", scope=RuleScope.INPUT, mode=Mode.ENFORCE, action=Action.BLOCK)
    plan = _plan(in_rule, mode=StreamingMode.STRICT_WITHHOLD)
    assert withholds_until_decision(plan) is False


# --------------------------------------------------------------------------- #
# Drift property: the predicate equals the pipeline's ``enforcing_output`` arg.
#
# Feature: strict-withhold-idle-timeout, Property 4 (predicate drift)
# Validates: Requirements 2.2, 3.1, 3.7
# --------------------------------------------------------------------------- #


def _common_deps() -> dict[str, object]:
    cfg, _ = load_holdback_config({})
    return dict(
        scanner=_scan,
        token_index=TokenIndex(),
        cfg=cfg,
        metrics=HoldbackMetrics(),
        window=max_pattern_length(),
        resolver=MinimalOutputResolver(),
        detector=None,
        max_buffer_bytes=1 << 20,
        strict_metrics=StrictMetrics(),
    )


def _random_plan(rng: random.Random) -> ExecutionPlan:
    """A plan with a random mode and a random, possibly empty, rule set."""
    mode = rng.choice(
        (StreamingMode.INCREMENTAL, StreamingMode.STRICT_WITHHOLD),
    )
    scopes = (RuleScope.OUTPUT, RuleScope.BOTH, RuleScope.INPUT)
    modes = (Mode.ENFORCE, Mode.MONITOR, Mode.OFF)
    actions = (
        Action.REDACT,
        Action.BLOCK,
        Action.REWRITE,
        Action.FLAG,
        Action.ALLOW,
    )
    rules = tuple(
        _rule(
            f"r{i}",
            scope=rng.choice(scopes),
            mode=rng.choice(modes),
            action=rng.choice(actions),
        )
        for i in range(rng.randint(0, 4))
    )
    return _plan(*rules, mode=mode)


def test_predicate_matches_select_pipeline_enforcing_output() -> None:
    """``withholds_until_decision`` tracks the strict pipeline's ``enforcing_output``.

    For a STRICT_WITHHOLD plan, ``select_pipeline`` builds a
    ``StrictWithholdPipeline`` whose ``_enforcing_output`` is
    ``bool(enforcing_output_rules(plan))``; the predicate must equal exactly that.
    For an INCREMENTAL plan the factory never builds a strict pipeline and the
    predicate must be False. Pinning the two together is what keeps the edge's
    liveness decision from drifting from the pipeline's withhold decision.
    """
    rng = random.Random(0xF1_1D1E)
    saw_strict_true = saw_strict_false = saw_incremental = False
    for _ in range(200):
        plan = _random_plan(rng)
        pipeline = select_pipeline(plan, **_common_deps())  # type: ignore[arg-type]
        predicate = withholds_until_decision(plan)

        if plan.streaming_mode is StreamingMode.STRICT_WITHHOLD:
            assert isinstance(pipeline, StrictWithholdPipeline)
            # The exact value select_pipeline resolved into the strict pipeline.
            assert predicate == pipeline._enforcing_output
            assert predicate == bool(enforcing_output_rules(plan))
            if predicate:
                saw_strict_true = True
            else:
                saw_strict_false = True
        else:
            # INCREMENTAL: no strict pipeline, predicate is always False.
            assert not isinstance(pipeline, StrictWithholdPipeline)
            assert predicate is False
            saw_incremental = True

    # The random space actually exercised all three outcomes.
    assert saw_strict_true
    assert saw_strict_false
    assert saw_incremental
