"""The ``select_pipeline`` factory — one dispatch keyed off the streaming mode (GW13, C2).

``select_pipeline`` is the single, shared dispatch the edge uses so both the
streaming and non-streaming chat surfaces get the right pipeline for the
resolved plan without duplicating the wiring. It reads ``plan.streaming_mode``
and constructs either the shipped INCREMENTAL ``StreamPipeline`` or the GW13
``StrictWithholdPipeline`` — keeping the ONE ``OutputResolver`` seam and the ONE
injected ``Detector`` identical for both (R1.3/R1.4). Both returned pipelines
expose a uniform ``run(chunks, send, killed)`` coroutine returning a stats value
carrying ``produced_mode`` (``StreamStats`` / ``StrictStats``), so the edge
driver treats them uniformly (R5.3).

**The plan is already resolved at the call site.** ``edge/routes.py`` builds the
pipeline from a resolved ``ExecutionPlan`` (``ChatRoute._build_pipeline(plan:
ExecutionPlan)``), so this factory takes a resolved ``ExecutionPlan`` and the
branch is a simple ``plan.streaming_mode is StreamingMode.STRICT_WITHHOLD``
check. The unresolved-plan guard (``Undetermined`` / ``PlanUnavailable`` /
``PlanUnknownTenant`` → ``EnforcementUndetermined``) is NOT re-implemented here:
the resolved markers do not carry ``streaming_mode`` and would never reach this
factory, and each ``for_plan`` still performs that fail-closed guard, so both
branches remain fail-closed on an unresolved plan.

Layering (import-linter ``layers`` contract in ``pyproject.toml``). This module
lives in ``egress`` and imports only ``gateway_v2.domain``, ``gateway_v2.runtime``
(``HoldbackConfig`` / ``HoldbackMetrics`` / ``StrictMetrics``), and the sibling
``gateway_v2.egress`` modules — never ``detect``. The completed-match source is
the INJECTED ``Detector``, passed straight through to both pipelines.
"""

from __future__ import annotations

import time
from collections.abc import Callable

from gateway_v2.domain import ExecutionPlan, StreamingMode
from gateway_v2.egress.output_guard import OutputResolver
from gateway_v2.egress.stream import Detector, StreamPipeline
from gateway_v2.egress.strict import StrictWithholdPipeline
from gateway_v2.egress.transport import ScanProtocol, TokenCounterProtocol
from gateway_v2.runtime.holdback_config import HoldbackConfig
from gateway_v2.runtime.holdback_metrics import HoldbackMetrics
from gateway_v2.runtime.strict_metrics import StrictMetrics

__all__ = ("select_pipeline",)


def select_pipeline(
    plan: ExecutionPlan,
    *,
    # INCREMENTAL deps (shipped StreamPipeline).
    scanner: ScanProtocol,
    token_index: TokenCounterProtocol,
    cfg: HoldbackConfig,
    metrics: HoldbackMetrics,
    window: int,
    # The shared one-resolver seam + injected completed-match source.
    resolver: OutputResolver,
    detector: Detector | None,
    # STRICT deps (StrictWithholdPipeline).
    max_buffer_bytes: int,
    strict_metrics: StrictMetrics,
    clock: Callable[[], int] = time.perf_counter_ns,
) -> StreamPipeline | StrictWithholdPipeline:
    """Return the pipeline the resolved plan selected, with the seam identical for both.

    When ``plan.streaming_mode`` is ``StreamingMode.STRICT_WITHHOLD`` this returns
    ``StrictWithholdPipeline.for_plan(...)`` (passing the STRICT deps
    ``max_buffer_bytes`` / ``strict_metrics`` and the shared ``resolver`` /
    ``detector``); otherwise it returns ``StreamPipeline.for_plan(...)`` with the
    full INCREMENTAL dep set. The ``resolver`` and ``detector`` seams are passed
    to whichever pipeline is built, so both modes resolve through the ONE
    ``OutputResolver`` at ``Phase.OUTPUT`` and read completed matches from the ONE
    injected ``Detector`` (R1.3/R1.4). Each ``for_plan`` performs the fail-closed
    unresolved-plan guard before construction, so an undetermined enforcement
    state never defaults to pass-through.
    """
    if plan.streaming_mode is StreamingMode.STRICT_WITHHOLD:
        return StrictWithholdPipeline.for_plan(
            plan,
            resolver=resolver,
            max_buffer_bytes=max_buffer_bytes,
            metrics=strict_metrics,
            clock=clock,
            detector=detector,
        )
    return StreamPipeline.for_plan(
        plan,
        scanner=scanner,
        resolver=resolver,
        token_index=token_index,
        cfg=cfg,
        metrics=metrics,
        window=window,
        clock=clock,
        detector=detector,
    )
