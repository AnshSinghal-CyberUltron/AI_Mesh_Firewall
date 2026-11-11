"""The ``SemanticDetector`` orchestrator for semantic detection (GW10).

This module composes the pure GW10 algebra (role admission, language posture +
confusable fold, tokenizer-aware windowing, the per-request FPR-budget derivation,
and the max-score-per-category aggregation) with the shipped GW08 ``GuardBackend``
to produce ``Finding[]``. It is a THIN orchestrator over pure functions; the only
I/O is the single ``await backend.classify(...)`` call.

Resolution 1 (the sync/async bridge). The detector's real surface is
``async def classify_input(...)`` — the §10.4 async request lifecycle awaits it
directly and never blocks the event loop. For uniformity with the shipped SYNC
:class:`~gateway_v2.detect.base.Detector` protocol, a thin ``detect(text, context)``
shim wraps the async method via the house idiom ``def _run[T](coro) -> T:
return asyncio.run(coro)`` — used ONLY at a true sync boundary, never on the async
hot path.

Resolution 2 (role scoping). ``classify_input`` takes a sequence of typed
:class:`~gateway_v2.detect.semantic.roles.RoleScopedSegment` values; each rule's
``role_scope`` admits the roles it may fire on (user input by default). A rule
scoped to user input never fires on a system/assistant-only input (Property 7).

The orchestration order (design §Architecture):

1. **role-admit** the segments for the rule's role scope. If no segment is admitted,
   a user-scoped rule that saw no user input does NOT fire (Property 7) — the detector
   never fabricates a clean ``EXECUTED`` finding for input it never classified.
2. **confusable fold** (BEFORE the tokenizer) + **language posture**. An unsupported
   language yields a NON-CLEAN posture (``UNAVAILABLE``, ``confidence=None``) — never a
   silent clean ``EXECUTED`` pass (Property 6).
3. **windowing** the folded admitted text into the ``WindowPlan``; an over-length input
   raises :class:`~gateway_v2.detect.semantic.windowing.OverLengthError`, which the
   detector maps to a single ``UNAVAILABLE`` finding (an actionable local reject, never
   an HTTP type, never a silent middle-drop). ``W`` and the tokenizer hash are recorded.
4. exactly ONE ``await backend.classify(windows, budget)`` call with ALL windows — never
   a per-window serial loop (Property 4).
5. a ``BackendUnavailable``, a mid-batch budget expiry (checked against the injected
   ``Budget.remaining_s`` / injected clock), or a not-ready ``readiness()`` → a single
   ``Finding(status=UNAVAILABLE, confidence=None)`` carrying the rule's
   ``on_unavailable`` posture; NEVER a partial per-window result set presented as a
   complete clean classification (Property 5).
6. on success, ``aggregate_results`` folds the per-window ``GuardResult[]`` into the
   request-level max-score-per-category result and maps the owning window back to a
   ``Span``; ``derive_threshold(ceiling, per_window_fpr, W)`` gives the per-window
   confidence cut, and a category whose max score ``>=`` the derived threshold becomes an
   ``EXECUTED`` finding carrying that confidence and span (``Rule.threshold``, where set,
   is an operator floor applied over the derived threshold). The ``ceiling_exceeded``
   (C6 FLAG) condition is RECORDED on the emitted findings' evidence, never silently
   proceeded past and never resolved into a disposition by the detector.

The detector is backend-TOPOLOGY-BLIND: it drives the backend only through
``classify`` / ``readiness`` / ``capacity_hint`` and references no CPU / GPU /
TensorRT / Triton / batch / replica symbol. It NEVER mutates its input, NEVER decides
disposition, NEVER raises HTTP, and NEVER short-circuits the pipeline — it emits
``Finding[]`` only; the pure resolver at lifecycle step ⑤ consumes them.

Layering: imports only ``gateway_v2.domain``, the GW08 ``detect`` siblings
(``detect.base``, ``detect.guard.backend``, ``detect.guard.local_onnx``), and the
GW10 ``detect.semantic.*`` siblings. ``Rule`` / ``Phase`` / ``FailurePosture`` live in
``gateway_v2.domain.plan`` (the lowest layer — a legal import). It never imports
``edge`` / ``admit`` / ``plan`` (the compilation package).

House idiom: ``from __future__ import annotations``; a frozen+slots ``SemanticConfig``
value type (no capacity / threshold / ceiling / overlap / chars-per-token value is a
dataclass-field literal — every such value is a REQUIRED field the caller supplies from
the plan / config / ``ResourceContract``); the ``SemanticDetector`` holds instance state
(the backend handle, config, injected clock) so it is a plain class, not a frozen
dataclass (mirroring :class:`~gateway_v2.detect.guard.local_onnx.LocalOnnxBackend`);
tuple ``__all__``; no module-level mutable binding.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Final

from gateway_v2.detect.base import DetectorContext
from gateway_v2.detect.guard.backend import Budget, GuardBackend, GuardResult
from gateway_v2.detect.guard.local_onnx import BackendUnavailable
from gateway_v2.detect.semantic.aggregate import aggregate_results
from gateway_v2.detect.semantic.fpr_budget import derive_threshold
from gateway_v2.detect.semantic.language import (
    LanguagePosture,
    fold_confusables,
    identify_language,
    posture_for,
)
from gateway_v2.detect.semantic.roles import (
    DEFAULT_ROLE_SCOPE,
    RoleScope,
    RoleScopedSegment,
    SegmentRole,
    admit_segments,
)
from gateway_v2.detect.semantic.windowing import (
    OverLengthError,
    WindowPlan,
    window_text,
)
from gateway_v2.domain import (
    FailurePosture,
    Finding,
    FindingStatus,
    Rule,
)

__all__ = (
    "DETECTOR_NAME",
    "SemanticConfig",
    "SemanticDetector",
)

#: The stable detector name carried on every emitted ``Finding.detector`` field.
DETECTOR_NAME: Final = "semantic"


def _run[T](coro: Awaitable[T]) -> T:
    """Run an awaitable to completion at a true sync boundary.

    The house idiom from ``tests/state_control/test_lgw05c_writer.py:49``. Used ONLY
    by the sync :meth:`SemanticDetector.detect` shim — the async hot path awaits
    :meth:`SemanticDetector.classify_input` directly and never calls this.
    """

    return asyncio.run(coro)  # type: ignore[arg-type]


@dataclass(frozen=True, slots=True)
class SemanticConfig:
    """Injected per-detector configuration (no capacity/threshold field literal).

    Every value here comes from the plan / configuration / ``ResourceContract`` and
    is a REQUIRED field the caller supplies — none is a dataclass-field numeric
    literal, so ``lint/check_capacity_literals.py`` stays green.

    ``fpr_ceiling`` — the ``Per_Request_FPR_Ceiling`` from the plan (R3.1), a
        probability in ``(0, 1)``.
    ``per_window_fpr`` — the declared model per-window FPR operating point (R3.3), a
        probability in ``(0, 1)``; the real measured curve is deferred (R11.1).
    ``ruleset_version`` — the GW10 ruleset version string folded into
        ``detector_version`` so a finding's provenance covers the rules too.
    ``tokenizer_hash`` — the pinned ``Tokenizer_Hash`` (R1.4), recorded on every request
        and folded into ``detector_version``.
    ``token_band`` / ``overlap_tokens`` / ``chars_per_token`` / ``max_window_count`` —
        the windowing parameters passed straight to ``window_text`` (R1.6, R4.4, R8.4).
    """

    fpr_ceiling: float
    per_window_fpr: float
    ruleset_version: str
    tokenizer_hash: str
    token_band: int
    overlap_tokens: int
    chars_per_token: float
    max_window_count: int


class SemanticDetector:
    """The async semantic detector that satisfies the sync ``Detector`` protocol.

    A STATEFUL orchestrator (it holds the backend handle, the injected config, and an
    injected clock), so — like :class:`LocalOnnxBackend` — it is a plain class, not a
    frozen dataclass. It composes the pure GW10 algebra with the ONE
    ``await backend.classify`` call and emits ``Finding[]``; it decides no disposition.
    """

    #: Satisfies the ``Detector`` protocol attributes (set per-instance below).
    detector_version: str
    required_capabilities: frozenset[str]

    def __init__(
        self,
        backend: GuardBackend,
        config: SemanticConfig,
        *,
        role_scope: RoleScope = DEFAULT_ROLE_SCOPE,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._backend = backend
        self._config = config
        self._role_scope = role_scope
        self._clock = clock
        # The composite detector_version is a stable function of the pinned tokenizer
        # hash + ruleset version + the backend-advertised model hash (resolved lazily
        # from readiness() at classify time and refreshed into the attribute). Until a
        # readiness reply lands, the model-hash slot carries a declared "unknown" tag so
        # the attribute is never absent (the Detector protocol requires it present).
        self._model_hash = "unknown"
        self.detector_version = self._compose_version(self._model_hash)
        self.required_capabilities = frozenset({DETECTOR_NAME})

    def _compose_version(self, model_hash: str) -> str:
        """``f"semantic:{model_hash}:{tokenizer_hash}:{ruleset_version}"`` (R7.1)."""

        return (
            f"{DETECTOR_NAME}:{model_hash}:"
            f"{self._config.tokenizer_hash}:{self._config.ruleset_version}"
        )

    # ------------------------------------------------------------------ #
    # The primary async surface (Resolution 1; the §10.4 lifecycle awaits this).
    # ------------------------------------------------------------------ #
    async def classify_input(
        self,
        segments: Sequence[RoleScopedSegment],
        context: DetectorContext,
    ) -> tuple[Finding, ...]:
        """Classify role-tagged input and emit ``Finding[]``. Never mutates input.

        Runs the orchestration order documented at the module level. Emits a single
        ``UNAVAILABLE`` finding on an unsupported language, an over-length input, a
        ``BackendUnavailable`` / not-ready backend, or a mid-batch budget expiry — never
        a partial result presented as a complete clean classification. On a clean,
        in-budget success it emits one ``EXECUTED`` finding per semantic category whose
        max score meets the derived per-window threshold.
        """

        findings: list[Finding] = []
        for rule in context.rules:
            findings.extend(await self._classify_rule(segments, rule, context.budget))
        return tuple(findings)

    async def _classify_rule(
        self,
        segments: Sequence[RoleScopedSegment],
        rule: Rule,
        budget: Budget,
    ) -> tuple[Finding, ...]:
        """Classify one rule against the admitted segments. Pure orchestration."""

        # (1) Role admission. A user-scoped rule that saw no admitted segment does NOT
        #     fire — never a fabricated clean finding for input it never classified.
        admitted = admit_segments(segments, self._role_scope)
        if not admitted:
            return ()

        # Join the admitted text WITHOUT mutating any segment (strings are immutable;
        # a new joined string is built). The confusable fold runs BEFORE windowing.
        joined = "\n".join(segment.text for segment in admitted)
        folded = fold_confusables(joined)

        # (2) Language posture. An unsupported language is a NON-CLEAN posture.
        posture = posture_for(identify_language(folded))
        if not posture.is_clean_eligible:
            return (self._unavailable_finding(rule, self._unsupported_reason(posture)),)

        # (3) Windowing. Over-length is an actionable local reject mapped to UNAVAILABLE.
        try:
            plan = window_text(
                folded,
                token_band=self._config.token_band,
                overlap=self._config.overlap_tokens,
                chars_per_token=self._config.chars_per_token,
                tokenizer_hash=self._config.tokenizer_hash,
                max_window_count=self._config.max_window_count,
            )
        except OverLengthError as exc:
            return (
                self._unavailable_finding(
                    rule,
                    f"over_length:{exc.required_windows}>{exc.max_windows}",
                ),
            )

        # (5a) Readiness gate (reply-based). A not-ready backend never yields a clean
        #      result; it maps to a single UNAVAILABLE finding (Property 5).
        readiness = await self._backend.readiness()
        self._refresh_model_hash(readiness.model_hash)
        if not readiness.ready:
            return (self._unavailable_finding(rule, readiness.reason or "not_ready"),)

        # (4) Exactly ONE classify call with ALL windows — never a per-window loop.
        before_s = self._clock()
        try:
            results: Sequence[GuardResult] = await self._backend.classify(
                plan.windows, budget
            )
        except BackendUnavailable as exc:
            return (self._unavailable_finding(rule, exc.reason),)

        # (5b) Mid-batch budget expiry, checked against the injected clock + Budget.
        #      A classify that ran past its remaining budget is UNAVAILABLE, never a
        #      partial-as-complete clean result.
        if (self._clock() - before_s) > budget.remaining_s:
            return (self._unavailable_finding(rule, "mid_batch_timeout"),)

        # (6) Aggregate + apply the derived per-window threshold.
        return self._emit_findings(rule, results, plan)

    def _emit_findings(
        self,
        rule: Rule,
        results: Sequence[GuardResult],
        plan: WindowPlan,
    ) -> tuple[Finding, ...]:
        """Fold the per-window results and apply the derived threshold. Pure."""

        budget = derive_threshold(
            self._config.fpr_ceiling,
            self._config.per_window_fpr,
            plan.window_count,
        )
        # Rule.threshold, where set, is an operator floor over the derived cut — the
        # operator's explicit confidence bar is never silently overwritten.
        threshold = budget.derived_threshold
        if rule.threshold is not None:
            threshold = max(threshold, rule.threshold)

        # The ceiling_exceeded (C6 FLAG) condition is RECORDED on the emitted findings'
        # evidence — never silently proceeded past; the detector decides no disposition.
        evidence = self._ceiling_evidence(plan, budget.ceiling_exceeded)

        aggregated = aggregate_results(results, plan.spans)
        findings: list[Finding] = []
        for item in aggregated:
            if item.category is not rule.category:
                continue
            if item.score < threshold:
                continue
            findings.append(
                Finding(
                    detector=DETECTOR_NAME,
                    detector_version=self.detector_version,
                    category=item.category,
                    status=FindingStatus.EXECUTED,
                    confidence=item.score,
                    spans=(item.span,),
                    evidence=evidence,
                )
            )
        return tuple(findings)

    def _ceiling_evidence(self, plan: WindowPlan, ceiling_exceeded: bool) -> str:
        """Record W, the tokenizer hash, and the ceiling-exceeded condition (<=256)."""

        flag = "ceiling_exceeded" if ceiling_exceeded else "within_ceiling"
        return f"W={plan.window_count};tokenizer={plan.tokenizer_hash};fpr={flag}"[:256]

    def _unsupported_reason(self, posture: LanguagePosture) -> str:
        """The honest UNAVAILABLE reason for an unsupported language (Property 6)."""

        return f"unsupported_language:{posture.language}"

    def _unavailable_finding(self, rule: Rule, reason: str) -> Finding:
        """A single UNAVAILABLE finding carrying the rule's failure posture (R6).

        ``confidence`` is ``None`` (``Finding.__post_init__`` forbids a score on a
        non-EXECUTED status), so an UNAVAILABLE finding is structurally incapable of
        carrying a fabricated clean score. The rule's ``on_unavailable`` posture is
        recorded in the evidence; the resolver decides the disposition, not the detector.
        """

        posture_tag = (
            "fail_closed"
            if rule.on_unavailable is FailurePosture.FAIL_CLOSED
            else "fail_open"
        )
        return Finding(
            detector=DETECTOR_NAME,
            detector_version=self.detector_version,
            category=rule.category,
            status=FindingStatus.UNAVAILABLE,
            confidence=None,
            spans=(),
            evidence=f"reason={reason};on_unavailable={posture_tag}"[:256],
        )

    def _refresh_model_hash(self, model_hash: str) -> None:
        """Fold the backend-advertised ``model_hash`` into ``detector_version``.

        The backend owns the model identity (computed from the bytes it actually
        loaded); the detector reads it only through ``readiness()`` and never observes
        any backend topology detail.
        """

        if model_hash and model_hash != self._model_hash:
            self._model_hash = model_hash
            self.detector_version = self._compose_version(model_hash)

    # ------------------------------------------------------------------ #
    # The thin sync Detector-protocol shim (Resolution 1). A true sync boundary only.
    # ------------------------------------------------------------------ #
    def detect(self, text: str, context: DetectorContext) -> tuple[Finding, ...]:
        """Sync ``Detector`` shim: wrap ``text`` as a single USER segment, run async.

        Wraps :meth:`classify_input` via ``_run`` (``asyncio.run``). Used ONLY at a true
        sync boundary — the async §10.4 hot path awaits ``classify_input`` directly and
        never calls this.
        """

        return _run(
            self.classify_input(
                (RoleScopedSegment(SegmentRole.USER, text),),
                context,
            )
        )
