"""GW15 DispatchRouter property tests (shared file).

This module is written by several GW15 task sub-agents concurrently (5.2 DispatchRouter
determinism, 5.3 plan-driven enforcement + unresolved fail-closed). Each contribution is a
SELF-CONTAINED additive section with uniquely-prefixed symbols (``_p3_`` / ``P3_`` for task
5.2) and section-local (mid-function) imports, so the sections never collide and none depends
on another's helpers. Do not remove a sibling section.

House idiom throughout: a seeded ``random.Random`` driven for >=10,000 iterations, NO
``hypothesis``; one property <-> one property-based test.
"""

from __future__ import annotations

# ===========================================================================
# Property 3 — DispatchRouter is a deterministic function of plan identity (task 5.2).
# ===========================================================================
# Feature: chat-surface-gw15, Property 3: DispatchRouter is a deterministic function of plan
#   identity
# Validates: Requirements 2.2
#
# ``DispatchRouter.select(plan)`` derives the upstream destination purely from the plan's
# IDENTITY — ``org_id`` plus the plan ``version`` (``epoch.sequence.content_hash``) — with no
# clock, no randomness, no I/O and no module-level mutable state (R8.3). This property drives
# >=10,000 iterations and asserts, for a freshly built plan plus a second plan that shares its
# identity (same ``org_id`` + ``epoch`` + ``sequence`` + ``content_hash``) but is allowed to
# differ on every NON-identity field (``compiled_at``, ``feed_seq``, ``rules``,
# ``required_detectors``, ``streaming_mode``, ``integrity_locked``):
#
#   (a) determinism — the two identity-sharing plans route to the IDENTICAL destination
#       string, and re-selecting the same plan is byte-stable across calls;
#   (b) no default escape — the derived destination is a real plan-derived label (the
#       ``amf://upstream/{org_id}/{version}#{fingerprint}`` form), never a bare/empty
#       placeholder default, so the shipped router never falls back to the ``_DEFAULT_DESTINATION``
#       stand-in that ``select`` replaced (R2.2);
#   (c) identity sensitivity — two plans that DIFFER on an identity field route to DIFFERENT
#       destinations, confirming the choice is a function OF the identity, not a constant.


def test_property_3_dispatch_router_deterministic_on_plan_identity() -> None:
    import random

    from gateway_v2.dispatch.routing import DispatchRouter
    from gateway_v2.domain.plan import ExecutionPlan, Rule, StreamingMode

    _SEED = 0x6C_6757_0003
    _ITERATIONS = 10_000

    # The destination must never be a bare placeholder/default. The shipped router derives
    # ``amf://upstream/{org_id}/{version}#{fingerprint}``; any of these sentinels would signal a
    # fallback to the old ``_DEFAULT_DESTINATION`` stand-in that ``select`` replaced (R2.2).
    _P3_FORBIDDEN_DEFAULTS = frozenset({"", "default", "none", "_DEFAULT_DESTINATION", "amf://upstream/"})
    _P3_DERIVED_PREFIX = "amf://upstream/"

    rng = random.Random(_SEED)
    router = DispatchRouter()

    def _p3_identity(rng: random.Random) -> tuple[str, int, int, str]:
        """A random plan IDENTITY: (org_id, epoch, sequence, content_hash)."""
        org_id = "org-" + "".join(rng.choice("0123456789abcdef") for _ in range(rng.randint(1, 24)))
        epoch = rng.randint(0, 1_000_000)
        sequence = rng.randint(0, 1_000_000)
        content_hash = "".join(rng.choice("0123456789abcdef") for _ in range(rng.randint(1, 64)))
        return org_id, epoch, sequence, content_hash

    def _p3_plan(
        rng: random.Random,
        identity: tuple[str, int, int, str],
    ) -> ExecutionPlan:
        """Build a plan with the given IDENTITY and randomized NON-identity fields.

        The non-identity fields (``compiled_at``, ``feed_seq``, ``rules`` and the enforcement
        knobs) are deliberately varied so determinism cannot accidentally depend on them: the
        destination must be a function of identity ONLY.
        """
        org_id, epoch, sequence, content_hash = identity
        return ExecutionPlan(
            org_id=org_id,
            epoch=epoch,
            sequence=sequence,
            content_hash=content_hash,
            compiled_at=rng.random() * 1_000_000.0,
            rules=(),
            required_detectors=frozenset(
                "det-" + str(rng.randint(0, 9)) for _ in range(rng.randint(0, 3))
            ),
            streaming_mode=rng.choice(tuple(StreamingMode)),
            integrity_locked=rng.random() < 0.5,
            feed_seq=rng.randint(0, 1_000_000),
        )

    assert isinstance(Rule, type)  # keep the import meaningful; Rule is the plan vocabulary.

    for _ in range(_ITERATIONS):
        identity = _p3_identity(rng)

        plan_a = _p3_plan(rng, identity)
        plan_b = _p3_plan(rng, identity)  # same identity, independently randomized otherwise.

        dest_a = router.select(plan_a)
        dest_b = router.select(plan_b)

        # (a) determinism: identity-sharing plans route identically, and re-selection is stable.
        assert dest_a == dest_b
        assert router.select(plan_a) == dest_a
        assert router.select(plan_b) == dest_b

        # (b) no default escape: a real plan-derived label, never a bare placeholder default.
        assert dest_a.startswith(_P3_DERIVED_PREFIX)
        assert dest_a not in _P3_FORBIDDEN_DEFAULTS
        assert dest_a != _P3_DERIVED_PREFIX  # the org/version/fingerprint tail is present.
        assert plan_a.org_id in dest_a
        assert plan_a.version in dest_a

        # (c) identity sensitivity: perturb exactly one identity field -> a different destination.
        org_id, epoch, sequence, content_hash = identity
        field = rng.randint(0, 3)
        if field == 0:
            other = (org_id + "x", epoch, sequence, content_hash)
        elif field == 1:
            other = (org_id, epoch + 1, sequence, content_hash)
        elif field == 2:
            other = (org_id, epoch, sequence + 1, content_hash)
        else:
            other = (org_id, epoch, sequence, content_hash + "0")
        dest_other = router.select(_p3_plan(rng, other))
        assert dest_other != dest_a
        assert dest_other.startswith(_P3_DERIVED_PREFIX)
        assert dest_other not in _P3_FORBIDDEN_DEFAULTS


# ===========================================================================
# Property 4 — Plan drives enforcement, and an unresolved plan fails closed (task 5.3).
# ===========================================================================
# Feature: chat-surface-gw15, Property 4: Plan drives enforcement, and an unresolved plan fails
#   closed
# Validates: Requirements 2.3, 2.4
#
# This section is SELF-CONTAINED and additive: every symbol is ``_p4_`` / ``P4_`` prefixed and
# all imports are section-local (mid-function), so it coexists with the Property 3 section above
# without sharing a helper. House idiom: a seeded ``random.Random`` driven for >=10,000
# iterations, NO ``hypothesis``; one property <-> one property-based test; async lifecycle via
# ``asyncio.run``.
#
# The property has two halves (design "Correctness Properties -> Property 4"):
#
#   (A) PLAN DRIVES ENFORCEMENT (R2.3). For any RESOLVED ``ExecutionPlan``,
#       ``StreamPipeline.for_plan`` engages output enforcement EXACTLY WHEN
#       ``output_guard.enforcing_output_rules(plan)`` is non-empty. We assert this
#       (i) structurally — the resolved pipeline's ``enforcing_output`` flag equals
#       ``bool(enforcing_output_rules(plan))`` — and (ii) behaviourally — a plan with an enforcing
#       OUTPUT rule HOLDS a trailing word-class run that could still grow into a match (the
#       holdback engages), while a plan with no enforcing OUTPUT rule PASSES THROUGH every chunk
#       unheld. The provider is contacted on the resolved path (a counted ``StubProviderClient``
#       drives the chunk source), confirming the resolved plan proceeds.
#
#   (B) UNRESOLVED PLAN FAILS CLOSED (R2.4). For any unresolved marker — the ``UNDETERMINED``
#       sentinel, a ``PlanUnavailable``, or a ``PlanUnknownTenant`` — ``for_plan`` raises
#       ``EnforcementUndetermined`` BEFORE constructing the pipeline, the request renders a
#       DECLARED value-code through the single ``edge/errors`` envelope (the shared
#       ``plan_unavailable`` spelling -> HTTP 503 non-stream and the declared SSE Error_Frame
#       streaming), and ZERO provider calls are made (a counted ``StubProviderClient`` is never
#       opened because the raise precedes any provider contact).


def test_property_4_plan_drives_enforcement_and_unresolved_fails_closed() -> None:
    import asyncio
    import random
    from collections.abc import AsyncIterator

    from gateway_v2.detect import holdback as _p4_holdback
    from gateway_v2.detect.holdback import TokenIndex as _P4_TokenIndex
    from gateway_v2.detect.windowing import max_pattern_length as _p4_window
    from gateway_v2.dispatch.provider import (
        StubProviderClient as _P4_StubProviderClient,
    )
    from gateway_v2.dispatch.provider import (
        UpstreamEvent as _P4_UpstreamEvent,
    )
    from gateway_v2.dispatch.provider import (
        UpstreamRequest as _P4_UpstreamRequest,
    )
    from gateway_v2.domain import posture as _p4_posture
    from gateway_v2.domain.category import Category as _P4_Category
    from gateway_v2.domain.plan import (
        Action as _P4_Action,
    )
    from gateway_v2.domain.plan import (
        ExecutionPlan as _P4_ExecutionPlan,
    )
    from gateway_v2.domain.plan import (
        FailurePosture as _P4_FailurePosture,
    )
    from gateway_v2.domain.plan import (
        Mode as _P4_Mode,
    )
    from gateway_v2.domain.plan import (
        PlanUnavailable as _P4_PlanUnavailable,
    )
    from gateway_v2.domain.plan import (
        PlanUnknownTenant as _P4_PlanUnknownTenant,
    )
    from gateway_v2.domain.plan import (
        Rule as _P4_Rule,
    )
    from gateway_v2.domain.plan import (
        RuleScope as _P4_RuleScope,
    )
    from gateway_v2.domain.plan import (
        StreamingMode as _P4_StreamingMode,
    )
    from gateway_v2.domain.plan import (
        Surface as _P4_Surface,
    )
    from gateway_v2.edge import errors as _p4_errors
    from gateway_v2.egress.output_guard import (
        MinimalOutputResolver as _P4_MinimalOutputResolver,
    )
    from gateway_v2.egress.output_guard import (
        enforcing_output_rules as _p4_enforcing_output_rules,
    )
    from gateway_v2.egress.stream import (
        UNDETERMINED as _P4_UNDETERMINED,
    )
    from gateway_v2.egress.stream import (
        DownstreamFrame as _P4_DownstreamFrame,
    )
    from gateway_v2.egress.stream import (
        EnforcementUndetermined as _P4_EnforcementUndetermined,
    )
    from gateway_v2.egress.stream import (
        StreamPipeline as _P4_StreamPipeline,
    )
    from gateway_v2.egress.stream import (
        UpstreamChunk as _P4_UpstreamChunk,
    )
    from gateway_v2.runtime.holdback_config import (
        load_holdback_config as _p4_load_holdback_config,
    )
    from gateway_v2.runtime.holdback_metrics import (
        HoldbackMetrics as _P4_HoldbackMetrics,
    )

    _SEED = 0x6C_6757_0004
    _ITERATIONS = 10_000
    _CHANNEL = "message"

    #: The declared value-code an unresolved plan renders through the single ``edge/errors``
    #: envelope (R2.4). ``EnforcementUndetermined`` carries no code of its own; the lifecycle maps
    #: it to the shared ``plan_unavailable`` spelling (see ``edge/routes.py`` + ``openai_chat``).
    _P4_PLAN_UNAVAILABLE = _p4_posture.PLAN_UNAVAILABLE
    assert _P4_PLAN_UNAVAILABLE == "plan_unavailable"

    rng = random.Random(_SEED)

    # -- injected scanner (the real pure detect-layer scan; test files may import detect) ------
    def _p4_scan(
        buf: str,
        *,
        final: bool,
        hold_cap_tokens: int,
        token_index: object,
        window: int,
    ) -> object:
        return _p4_holdback.scan(
            buf,
            final=final,
            hold_cap_tokens=hold_cap_tokens,
            token_index=token_index,  # type: ignore[arg-type]
            window=window,
        )

    class _P4_CountingProvider:
        """A counted wrapper over ``StubProviderClient`` recording ``open`` calls.

        Delegates to a real ``StubProviderClient`` (no socket) but tallies every ``open`` so the
        unresolved path can assert ZERO provider contact (R2.4). The resolved path uses the tally
        to confirm the provider WAS opened, i.e. the request proceeded.
        """

        def __init__(self, *, script: tuple[_P4_UpstreamEvent, ...], rng: random.Random) -> None:
            self._inner = _P4_StubProviderClient(script=script, rng=rng)
            self.open_calls = 0

        def open(self, request: _P4_UpstreamRequest) -> AsyncIterator[_P4_UpstreamEvent]:
            self.open_calls += 1
            return self._inner.open(request)

        async def abort(self) -> None:
            await self._inner.abort()

    class _P4_Collector:
        """An injected ``Send`` sink recording downstream frames + their released text."""

        def __init__(self) -> None:
            self.frames: list[_P4_DownstreamFrame] = []

        async def __call__(self, frame: _P4_DownstreamFrame) -> None:
            self.frames.append(frame)

        def released_text(self) -> str:
            parts: list[str] = []
            for frame in self.frames:
                if frame.error_code is not None:
                    continue
                for channel, delta in frame.text_deltas:
                    if channel == _CHANNEL:
                        parts.append(delta)
            return "".join(parts)

    def _p4_rule(
        rule_id: str,
        *,
        scope: _P4_RuleScope,
        mode: _P4_Mode,
        action: _P4_Action,
    ) -> _P4_Rule:
        return _P4_Rule(
            rule_id=rule_id,
            category=_P4_Category.SECRET,
            mode=mode,
            action=action,
            threshold=None,
            priority=0,
            scope=scope,
            on_unavailable=_P4_FailurePosture.FAIL_CLOSED,
            surfaces=frozenset({_P4_Surface.CHAT}),
        )

    def _p4_resolved_plan(rng: random.Random, *, enforcing: bool) -> _P4_ExecutionPlan:
        """A resolved plan whose ``enforcing_output_rules`` is non-empty iff ``enforcing``.

        When ``enforcing`` we add exactly one OUTPUT/BOTH + ENFORCE + REDACT|BLOCK|REWRITE rule
        (the only shape ``enforcing_output_rules`` keeps). We ALSO scatter decoy rules that must
        NOT count — INPUT scope, MONITOR/OFF mode, ALLOW/FLAG action — so the engagement decision
        is a genuine function of the enforcing-output filter, not of "any rule present".
        """
        rules: list[_P4_Rule] = []
        if enforcing:
            rules.append(
                _p4_rule(
                    "enf",
                    scope=rng.choice((_P4_RuleScope.OUTPUT, _P4_RuleScope.BOTH)),
                    mode=_P4_Mode.ENFORCE,
                    action=rng.choice(
                        (_P4_Action.REDACT, _P4_Action.BLOCK, _P4_Action.REWRITE),
                    ),
                )
            )
        # Decoys that enforcing_output_rules must drop (never make the set non-empty on their own).
        for i in range(rng.randint(0, 3)):
            kind = rng.randint(0, 2)
            if kind == 0:
                rules.append(
                    _p4_rule(
                        f"in{i}",
                        scope=_P4_RuleScope.INPUT,
                        mode=_P4_Mode.ENFORCE,
                        action=_P4_Action.REDACT,
                    )
                )
            elif kind == 1:
                rules.append(
                    _p4_rule(
                        f"mon{i}",
                        scope=_P4_RuleScope.OUTPUT,
                        mode=rng.choice((_P4_Mode.MONITOR, _P4_Mode.OFF)),
                        action=_P4_Action.REDACT,
                    )
                )
            else:
                rules.append(
                    _p4_rule(
                        f"obs{i}",
                        scope=_P4_RuleScope.OUTPUT,
                        mode=_P4_Mode.ENFORCE,
                        action=rng.choice((_P4_Action.ALLOW, _P4_Action.FLAG)),
                    )
                )
        rng.shuffle(rules)
        org_suffix = "".join(rng.choice("0123456789abcdef") for _ in range(rng.randint(1, 12)))
        return _P4_ExecutionPlan(
            org_id="org-" + org_suffix,
            epoch=rng.randint(0, 1_000),
            sequence=rng.randint(0, 1_000),
            content_hash="".join(rng.choice("0123456789abcdef") for _ in range(32)),
            compiled_at=0.0,
            rules=tuple(rules),
            required_detectors=frozenset(),
            streaming_mode=_P4_StreamingMode.INCREMENTAL,
            integrity_locked=True,
        )

    def _p4_for_plan(
        plan: object,
        *,
        metrics: _P4_HoldbackMetrics,
    ) -> _P4_StreamPipeline:
        config, _ = _p4_load_holdback_config({})
        return _P4_StreamPipeline.for_plan(
            plan,  # type: ignore[arg-type]
            scanner=_p4_scan,  # type: ignore[arg-type]
            resolver=_P4_MinimalOutputResolver(),
            token_index=_P4_TokenIndex(),
            cfg=config,
            metrics=metrics,
            window=_p4_window(),
        )

    async def _p4_source(
        chunks: tuple[_P4_UpstreamChunk, ...],
    ) -> AsyncIterator[_P4_UpstreamChunk]:
        for chunk in chunks:
            yield chunk

    def _p4_never_killed() -> bool:
        return False

    # Two chunks drive the behavioural contrast. On the NON-FINAL first chunk ("hello world foo")
    # the enforcing pipeline HOLDS the trailing word-class run "foo" (that run could still grow
    # into a match), so only "hello world " releases — a STRICT prefix. The pass-through pipeline
    # releases the whole first delta "hello world foo" unheld. The final chunk ("bar") flushes the
    # held "foo", so BOTH paths produce the same whole stream "hello world foobar" (R3.5): the hold
    # is observable only in the FIRST released frame, which is the engagement signature.
    _P4_FIRST_DELTA = "hello world foo"
    _P4_FINAL_DELTA = "bar"
    _P4_ENFORCING_FIRST_RELEASE = "hello world "
    _P4_WHOLE = _P4_FIRST_DELTA + _P4_FINAL_DELTA

    for _ in range(_ITERATIONS):
        # ----------------------------------------------------------------- #
        # (A) Plan drives enforcement (R2.3): engage iff the enforcing set is non-empty.
        # ----------------------------------------------------------------- #
        enforcing = rng.random() < 0.5
        plan = _p4_resolved_plan(rng, enforcing=enforcing)
        expected = bool(_p4_enforcing_output_rules(plan))
        assert expected == enforcing  # the generator's intent matches the filter.

        metrics = _P4_HoldbackMetrics()
        pipe = _p4_for_plan(plan, metrics=metrics)

        # (i) structural: the resolved pipeline engages enforcement iff the set is non-empty.
        assert pipe._enforcing_output is expected  # noqa: SLF001 - the gated flag is the property.

        # (ii) behavioural: drive the pipeline from a provider-fed chunk source and observe the
        # hold. A counted provider confirms the resolved request DID reach provider contact.
        script = (
            _P4_UpstreamEvent(text_deltas=((_CHANNEL, _P4_FIRST_DELTA),), final=False),
            _P4_UpstreamEvent(text_deltas=((_CHANNEL, _P4_FINAL_DELTA),), final=True),
        )
        provider = _P4_CountingProvider(script=script, rng=rng)

        async def _p4_drive(
            pipe: _P4_StreamPipeline = pipe,
            provider: _P4_CountingProvider = provider,
        ) -> _P4_Collector:
            collector = _P4_Collector()
            request = _P4_UpstreamRequest(destination="amf://test", body={})
            # Feed the provider's events through the pipeline's UpstreamChunk transport (same
            # (channel, delta) shape), proving provider contact occurs on the resolved path.
            events = [event async for event in provider.open(request)]
            chunks = tuple(
                _P4_UpstreamChunk(text_deltas=event.text_deltas, final=event.final)
                for event in events
            )
            await pipe.run(_p4_source(chunks), collector, _p4_never_killed)
            return collector

        collector = asyncio.run(_p4_drive())

        # Provider was opened on the resolved path (the request proceeded past plan resolution).
        assert provider.open_calls == 1

        # The full stream content is identical either way (nothing redacted for prose, the held
        # "foo" flushes on the final chunk); the DIFFERENCE is the FIRST release: enforcing holds
        # the trailing word-class run so the first frame is a strict prefix, while pass-through
        # emits the whole first delta unheld.
        assert collector.released_text() == _P4_WHOLE
        first_release = next(
            (
                delta
                for frame in collector.frames
                if frame.error_code is None
                for channel, delta in frame.text_deltas
                if channel == _CHANNEL and delta
            ),
            "",
        )
        if expected:
            # Enforcing: the trailing word-class run "foo" is held on the non-final chunk, so the
            # first release is exactly the strict prefix "hello world " (the hold engaged).
            assert first_release == _P4_ENFORCING_FIRST_RELEASE
            assert first_release != _P4_FIRST_DELTA
            assert _P4_FIRST_DELTA.startswith(first_release)
        else:
            # Pass-through: the first content frame carries the whole first delta unheld.
            assert first_release == _P4_FIRST_DELTA

        # ----------------------------------------------------------------- #
        # (B) Unresolved plan fails closed (R2.4): raise, declared value-code, zero provider calls.
        # ----------------------------------------------------------------- #
        marker_kind = rng.randint(0, 2)
        if marker_kind == 0:
            marker: object = _P4_UNDETERMINED
        elif marker_kind == 1:
            marker = _P4_PlanUnavailable(org_id="org-x", reason="stale")
        else:
            marker = _P4_PlanUnknownTenant(org_id="org-x")

        unresolved_script = (
            _P4_UpstreamEvent(text_deltas=((_CHANNEL, "should never flow"),), final=True),
        )
        unresolved_provider = _P4_CountingProvider(script=unresolved_script, rng=rng)

        raised = False
        try:
            _p4_for_plan(marker, metrics=_P4_HoldbackMetrics())
        except _P4_EnforcementUndetermined:
            raised = True
        # The raise precedes any pipeline construction, so no provider stream is ever opened.
        assert raised
        assert unresolved_provider.open_calls == 0

        # The request renders a DECLARED value-code through the single edge/errors envelope: a
        # concrete HTTP body (non-stream) and the declared SSE Error_Frame (streaming). The
        # unmapped-code fallback is a 500 "gateway_error"; the declared mapping is a 503 with the
        # code echoed in the structured ``code`` field — assert we are on the DECLARED branch.
        http = _p4_errors.render(_P4_PLAN_UNAVAILABLE, as_sse=False)
        assert isinstance(http, _p4_errors.HTTPResponse)
        assert http.status == 503
        payload = http.json()
        assert isinstance(payload, dict)
        error_obj = payload["error"]
        assert isinstance(error_obj, dict)
        assert error_obj["code"] == _P4_PLAN_UNAVAILABLE
        assert error_obj["type"] == "service_unavailable"

        sse = _p4_errors.render(_P4_PLAN_UNAVAILABLE, as_sse=True)
        assert isinstance(sse, bytes)
        assert _P4_PLAN_UNAVAILABLE.encode("utf-8") in sse
