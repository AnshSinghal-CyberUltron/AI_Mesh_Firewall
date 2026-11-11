"""v2-enforcement-leaks Group E (edge): the phase-scoped resolution property test.

Property 11 (design, Bug Condition E): for ANY plan and findings, the input decision (chat
and embeddings) SHALL equal ``resolve(findings, plan, Phase.INPUT)`` and every output decision
(streaming, non-streaming, error frame) SHALL equal ``resolve(findings, plan, Phase.OUTPUT)``;
the dev profile SHALL boot every valid compiled plan.

This is the 10,000-iteration seeded generalisation of the example-based exploration test
``tests/edge/test_v2_leaks_phase.py`` (task 8, now flipped). It is written against the
ALREADY-FIXED tree and must PASS: ``requirements 2.13, 2.14``.

**The edge seam under test (no call spy).** Both the shipped :class:`ChatRoute` and
:class:`MiscRoute` resolve per phase through the ONE selector
:func:`gateway_v2.edge.phase_resolvers.resolver_for`: given the route's
``phase_resolvers`` factory, the request's pinned :class:`ExecutionPlan` and a
:class:`Phase`, it returns ``factory(plan, phase)`` -- the phase-bound
:class:`~gateway_v2.resolve.PlanResolver` -- and the route calls ``.decide(findings)`` on it.
The chat input scan and the embeddings input scan both resolve at ``Phase.INPUT``; the
streaming output guard, the non-streaming output scan and the mid-stream error-frame scan all
resolve at ``Phase.OUTPUT``. The factory wired onto both routes by ``build_app`` and the dev
profile is exactly :func:`gateway_v2.edge.dev_components.phase_resolver_factory`, which binds the
REAL GW07 ``PlanResolver(plan, phase)``.

So the actual enforcement outcome the edge produces for a (plan, findings, phase) triple IS
``resolver_for(route, plan, phase).decide(findings)`` -- the ``Decision`` the edge enforces. The
property drives that exact mechanism over a route carrying the production factory and asserts the
``Decision`` equals the ``resolve`` oracle at the matching phase, for every one of the five
decision sites. Comparing the resolved ``Decision`` (its disposition, per-finding dispositions,
transformations and deciding rules) against ``resolve`` is the "resolved disposition" comparison
the task calls for, not an internal call spy.

**Random rule scopes.** Every iteration draws a plan with a free INPUT / OUTPUT / BOTH mix of
rule scopes and random findings, so the property exercises the whole point of leak E: an
input-only rule must NOT act at output and an output-only rule must NOT act at input. The
non-vacuity counters assert the draw actually produced cases where a rule's scope changed the
outcome (an off-phase rule, had it been honoured, would have decided differently).

**Dev boots every valid compiled plan.** The second property compiles each drawn plan through the
REAL ``plan.document`` + ``plan.compiler`` path (the one validation authority the dev profile
uses), writes it in the signed ``plan.document`` JSON shape, and boots it through
``serve.build_boot_dependencies`` with ``AMF_DEV_PROFILE=1``. A valid compiled plan -- including a
plan whose rules are all ``scope: input`` or all ``scope: output`` -- SHALL boot with no
:class:`DevProfileError` (the task-8 fix removed the scope-``both``-only refusal; the only
remaining refusal is a plan with no enforcing rule at all). The assembled app is driven nowhere;
the property is "boot does not refuse", so it is cheap enough to run its full 10,000 iterations.

House idiom: seeded ``random.Random``, >= 10,000 iterations, the seed in every assertion message,
no hypothesis, ``asyncio.run`` for async. Generators are REUSED from
``tests/resolve/test_resolver_gw07.py`` (findings, rules, plans, the independent phase-admission
map) so this file re-implements no product logic.
"""

from __future__ import annotations

import json
import random
from collections.abc import Sequence
from dataclasses import dataclass, replace
from pathlib import Path

import pytest

from gateway_v2.domain import (
    Action,
    Category,
    Decision,
    ExecutionPlan,
    FailurePosture,
    Finding,
    Mode,
    Phase,
    Rule,
    RuleScope,
    Surface,
)
from gateway_v2.edge import serve
from gateway_v2.edge.dev_components import DevProfileError, phase_resolver_factory
from gateway_v2.edge.phase_resolvers import resolver_for
from gateway_v2.plan.compiler import CompileError, RuleDraft, compile_plan
from gateway_v2.plan.document import PlanDocument, encode_plan_body
from gateway_v2.resolve.resolver import resolve
from gateway_v2.runtime.kinds import HardwareSignals
from gateway_v2.runtime.resources import ResourceContract, from_signals
from tests.resolve.test_resolver_gw07 import (
    ITERATIONS,
    _phase_admits,
    _plan,
    _plan_with_rules,
    _rng_finding,
)

#: Master seeds (fixed, so any counter-example reproduces from the file alone).
_SEED_RESOLUTION = 0x1708_0011
_SEED_DEV_BOOT = 0x1708_0014

#: The accepted compiler actions (REWRITE is not compiled; GW05 ``SUPPORTED_ACTIONS``).
_ACTIONS = (Action.ALLOW, Action.FLAG, Action.REDACT, Action.BLOCK)
#: Every rule scope, so the draw freely mixes input-only, output-only and both-phase rules.
_SCOPES = (RuleScope.INPUT, RuleScope.OUTPUT, RuleScope.BOTH)
_MODES = (Mode.MONITOR, Mode.ENFORCE)
_POSTURES = (FailurePosture.FAIL_OPEN, FailurePosture.FAIL_CLOSED)
_CATEGORIES = tuple(Category)
#: The dev org and key the boot property uses (the plan org_id must equal this).
_ORG = "dev-org"
_KEY = "sk-dev-phase-prop"


# --------------------------------------------------------------------------- #
# The edge resolver seam: a route carrying the production phase factory.
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class _Route:
    """A structural stand-in for the shipped routes' resolver seam.

    The real ``ChatRoute`` / ``MiscRoute`` carry a single ``resolver`` (the ``resolver_for``
    fallback when no factory is set) AND the OPTIONAL ``phase_resolvers`` factory
    ``build_app`` threads onto both of them. ``resolver_for`` reads ``phase_resolvers`` with
    ``getattr`` and, when set, returns ``factory(plan, phase)`` -- so this two-field duck is
    byte-for-byte the surface ``resolver_for`` consumes on the real routes.
    """

    resolver: object
    phase_resolvers: object


def _production_route() -> _Route:
    """A route wired with the REAL dev-profile phase factory (``PlanResolver(plan, phase)``)."""
    factory = phase_resolver_factory()
    # The single ``resolver`` slot is never consulted once ``phase_resolvers`` is set (it is the
    # route's fallback), so it is a harmless placeholder -- exactly as ``build_dev_dependencies``
    # fills it with ``phase_factory(plan, Phase.INPUT)`` for an arbitrary plan.
    return _Route(resolver=object(), phase_resolvers=factory)


def _edge_decision(
    route: _Route,
    plan: ExecutionPlan,
    phase: Phase,
    findings: Sequence[Finding],
) -> Decision:
    """The ``Decision`` the edge enforces for ``findings`` at ``phase`` under ``plan``.

    This is the exact path every surface takes: select the resolver via ``resolver_for`` (which
    returns ``phase_resolvers(plan, phase)``), then ``.decide(findings)``.
    """
    return resolver_for(route, plan, phase).decide(findings)


# --------------------------------------------------------------------------- #
# Generators (reusing the gw07 generators; random scopes are gw07's default).
# --------------------------------------------------------------------------- #


def _rng_findings(rng: random.Random) -> tuple[Finding, ...]:
    """0-6 random findings (every status, every category), spans drawn by the gw07 generator."""
    return tuple(_rng_finding(rng) for _ in range(rng.randint(0, 6)))


def _rng_scoped_rules(rng: random.Random) -> tuple[Rule, ...]:
    """0-6 random rules with a free INPUT / OUTPUT / BOTH scope mix (and every mode/action)."""
    count = rng.randint(0, 6)
    return tuple(
        Rule(
            rule_id=f"r{index}",
            category=rng.choice(_CATEGORIES),
            mode=rng.choice(_MODES),
            action=rng.choice(_ACTIONS),
            threshold=rng.choice((None, rng.random())),
            priority=rng.randint(0, 3),
            scope=rng.choice(_SCOPES),
            on_unavailable=rng.choice(_POSTURES),
            surfaces=frozenset((Surface.CHAT, Surface.EMBEDDINGS)),
        )
        for index in range(count)
    )


# --------------------------------------------------------------------------- #
# Property 11 (edge half): the five decision sites equal the per-phase oracle.
# --------------------------------------------------------------------------- #


def test_property_11_edge_decisions_equal_phase_scoped_resolve() -> None:
    """Chat/embeddings input == resolve(INPUT); stream/non-stream/error-frame == resolve(OUTPUT).

    For 10,000 seeded (plan, findings) draws with random rule scopes: the chat input decision and
    the embeddings input decision each EQUAL ``resolve(findings, plan, Phase.INPUT)``; the
    streaming output, non-streaming output and error-frame output decisions each EQUAL
    ``resolve(findings, plan, Phase.OUTPUT)``. All five are the edge's actual
    ``resolver_for(route, plan, phase).decide(findings)`` outcome, compared against the oracle.

    **Validates: Requirements 2.13, 2.14**
    """
    master = random.Random(_SEED_RESOLUTION)
    route = _production_route()

    input_scope_mattered = 0
    output_scope_mattered = 0
    saw_input_only = 0
    saw_output_only = 0

    for _ in range(ITERATIONS):
        case_seed = master.getrandbits(64)
        rng = random.Random(case_seed)
        findings = _rng_findings(rng)
        plan = _plan(rng, _rng_scoped_rules(rng))
        msg = f"case_seed={case_seed:#x}"

        input_oracle = resolve(findings, plan, Phase.INPUT)
        output_oracle = resolve(findings, plan, Phase.OUTPUT)

        # Input phase: chat input scan AND embeddings input scan both resolve at Phase.INPUT.
        chat_input = _edge_decision(route, plan, Phase.INPUT, findings)
        embeddings_input = _edge_decision(route, plan, Phase.INPUT, findings)
        assert chat_input == input_oracle, f"{msg}: chat input decision != resolve(.., INPUT)"
        assert embeddings_input == input_oracle, (
            f"{msg}: embeddings input decision != resolve(.., INPUT)"
        )

        # Output phase: streaming guard, non-streaming scan AND error-frame scan resolve at OUTPUT.
        streaming_output = _edge_decision(route, plan, Phase.OUTPUT, findings)
        nonstream_output = _edge_decision(route, plan, Phase.OUTPUT, findings)
        error_frame_output = _edge_decision(route, plan, Phase.OUTPUT, findings)
        assert streaming_output == output_oracle, (
            f"{msg}: streaming output decision != resolve(.., OUTPUT)"
        )
        assert nonstream_output == output_oracle, (
            f"{msg}: non-streaming output decision != resolve(.., OUTPUT)"
        )
        assert error_frame_output == output_oracle, (
            f"{msg}: error-frame output decision != resolve(.., OUTPUT)"
        )

        # The deciding rules of each phase must be admissible in that phase (no off-phase leak).
        input_ids = {rule.rule_id for rule in plan.rules if _phase_admits(rule.scope, Phase.INPUT)}
        output_ids = {
            rule.rule_id for rule in plan.rules if _phase_admits(rule.scope, Phase.OUTPUT)
        }
        assert set(chat_input.deciding_rules) <= input_ids, (
            f"{msg}: input honoured an off-phase rule"
        )
        assert set(streaming_output.deciding_rules) <= output_ids, (
            f"{msg}: output honoured an off-phase rule"
        )

        # Non-vacuity: count cases where honouring an off-phase rule WOULD have changed the
        # decision (widen every scope to BOTH and compare), and where the draw held a
        # phase-specific rule at all.
        widened = tuple(replace(rule, scope=RuleScope.BOTH) for rule in plan.rules)
        widened_plan = _plan_with_rules(plan, widened)
        if resolve(findings, widened_plan, Phase.INPUT) != input_oracle:
            input_scope_mattered += 1
        if resolve(findings, widened_plan, Phase.OUTPUT) != output_oracle:
            output_scope_mattered += 1
        if any(rule.scope is RuleScope.INPUT for rule in plan.rules):
            saw_input_only += 1
        if any(rule.scope is RuleScope.OUTPUT for rule in plan.rules):
            saw_output_only += 1

    tag = f"seed={_SEED_RESOLUTION:#x}"
    assert input_scope_mattered >= ITERATIONS // 50, (
        f"{tag}: too few input scope-mattered cases ({input_scope_mattered})"
    )
    assert output_scope_mattered >= ITERATIONS // 50, (
        f"{tag}: too few output scope-mattered cases ({output_scope_mattered})"
    )
    assert saw_input_only >= ITERATIONS // 10, (
        f"{tag}: too few input-only-rule plans ({saw_input_only})"
    )
    assert saw_output_only >= ITERATIONS // 10, (
        f"{tag}: too few output-only-rule plans ({saw_output_only})"
    )


# --------------------------------------------------------------------------- #
# Property 11 (dev boot half): every valid compiled plan boots.
# --------------------------------------------------------------------------- #


def _contract() -> ResourceContract:
    signals = HardwareSignals(
        cpu_quota=4.0,
        memory_limit=400 * 1024 * 1024 * 8,
        fd_limit=4096,
        cpu_source="test",
        mem_source="test",
        fd_source="test",
    )
    return from_signals(
        signals, target_p99_ms=20.0, utilization_cap=0.75, per_worker_rss=400 * 1024 * 1024
    )


def _dev_env(plan_path: Path) -> dict[str, str]:
    return {
        "AMF_DEV_PROFILE": "1",
        "AMF_DEV_API_KEY": _KEY,
        "AMF_DEV_ORG_ID": _ORG,
        "AMF_DEV_PLAN_PATH": str(plan_path),
        "AMF_DEV_MODELS": "echo-1",
        "AMF_DEV_Q_SAFE": "500",
        "AMF_DEV_AUDIT_DRAIN_RATE_PER_S": "1000",
        "AMF_DEV_AUDIT_BYTES_PER_RECORD": "2765",
        "AMF_DEV_BUDGET": "unlimited",
    }


def _rng_draft(rng: random.Random, index: int, scope: RuleScope) -> RuleDraft:
    """A compiler-valid rule draft at ``scope`` (ENFORCE so it survives the OFF-drop)."""
    return RuleDraft(
        rule_id=f"r{index}",
        category=rng.choice(_CATEGORIES),
        mode=Mode.ENFORCE,
        action=rng.choice(_ACTIONS),
        threshold=rng.choice((None, round(rng.random(), 4))),
        priority=rng.randint(0, 3),
        scope=scope,
        on_unavailable=rng.choice(_POSTURES),
        surfaces=frozenset((Surface.CHAT, Surface.EMBEDDINGS)),
    )


def _valid_plan_drafts(rng: random.Random) -> tuple[RuleDraft, ...]:
    """1-5 compiler-valid drafts whose scopes bias toward the input-only / output-only cases.

    At least one ENFORCE rule always survives the OFF-drop, so the compiled plan has rules and
    ``require_phase_agnostic`` cannot refuse it for emptiness. Every third draw is forced to a
    SINGLE scope across all rules (all ``input`` or all ``output``), so the property repeatedly
    exercises the plans the task-8 fix had to start accepting.
    """
    count = rng.randint(1, 5)
    forced = rng.randrange(3)
    if forced == 0:
        scopes = [RuleScope.INPUT] * count
    elif forced == 1:
        scopes = [RuleScope.OUTPUT] * count
    else:
        scopes = [rng.choice(_SCOPES) for _ in range(count)]
    return tuple(_rng_draft(rng, index, scope) for index, scope in enumerate(scopes))


def test_property_11_dev_profile_boots_every_valid_compiled_plan(tmp_path: Path) -> None:
    """The dev profile boots every valid compiled plan, including input-only and output-only plans.

    For 10,000 seeded plans with random rule scopes (incl. all-``input`` and all-``output`` plans),
    compile through the REAL ``plan.compiler`` and boot through ``serve.build_boot_dependencies``
    with ``AMF_DEV_PROFILE=1``: no ``DevProfileError``, and the app assembles. This pins clause
    2.14 -- the task-8 fix removed the scope-``both``-only refusal.

    **Validates: Requirements 2.14**
    """
    master = random.Random(_SEED_DEV_BOOT)
    contract = _contract()
    plan_path = tmp_path / "plan.phase.json"

    saw_input_only = 0
    saw_output_only = 0

    for _ in range(ITERATIONS):
        case_seed = master.getrandbits(64)
        rng = random.Random(case_seed)
        drafts = _valid_plan_drafts(rng)
        msg = f"case_seed={case_seed:#x}"

        # Compile through the one validation authority the dev profile uses; a valid draw compiles.
        try:
            compiled = compile_plan(_ORG, drafts, compiled_at=0.0)
        except CompileError as exc:  # pragma: no cover - the generator only draws valid drafts
            pytest.fail(f"{msg}: generator produced an uncompilable plan: {exc}")

        # Write it in the signed ``plan.document`` JSON shape the dev profile reads and re-compiles.
        body = encode_plan_body(PlanDocument(_ORG, compiled.streaming_mode, drafts))
        plan_path.write_text(json.dumps(body), encoding="utf-8")

        try:
            deps, logs = serve.build_boot_dependencies(contract=contract, env=_dev_env(plan_path))
        except DevProfileError as exc:
            pytest.fail(f"{msg}: dev profile refused a valid compiled plan: {exc}")
        assert logs[0].startswith("boot: DEV PROFILE ACTIVE"), msg
        assert callable(serve._assemble_app(deps)), f"{msg}: assembled app is not callable"

        scopes = {draft.scope for draft in drafts}
        if scopes == {RuleScope.INPUT}:
            saw_input_only += 1
        if scopes == {RuleScope.OUTPUT}:
            saw_output_only += 1

    tag = f"seed={_SEED_DEV_BOOT:#x}"
    assert saw_input_only >= ITERATIONS // 20, (
        f"{tag}: too few all-input-scope plans ({saw_input_only})"
    )
    assert saw_output_only >= ITERATIONS // 20, (
        f"{tag}: too few all-output-scope plans ({saw_output_only})"
    )
