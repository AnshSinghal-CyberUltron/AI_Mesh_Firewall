"""Streaming-latency ANCHOR harness + strategies (Feature: honest-stream-latency-metric).

SCAFFOLDING ONLY (task 1). This module builds the reusable clock-injection harness,
the Hypothesis timing-input strategy, and the SSE-frame drive/parse helper that the
later property (2.3), acceptance (2.4), and edge (2.5) test sub-tasks consume. It does
NOT contain the property/acceptance/edge assertions themselves and does NOT touch
production code.

Clock model (confirmed against stream_orchestration.py):
  - ``StreamRunMetrics.duration_ms`` reads the clock LIVE:
        end = time.perf_counter(); return (end - provider_start_ts) * 1000
    where ``time`` is the ``time`` module imported INTO ``stream_orchestration``.
  - ``build_stream_trace_frame`` also reads ``time.perf_counter()`` (via the same module
    ``time``) for the ``ctx.start_time`` fallback and — after the task-2 fix — as the
    request-accept-anchored total.
  So patching ``stream_orchestration.time.perf_counter`` to a controlled stream-close
  instant makes BOTH the live ``duration_ms`` read and the frame's own clock read
  deterministic, which is exactly what the timing algebra tests need.

Real signatures matched from source (do not guess):
  - build_stream_trace_frame(ctx, metrics, base_zeroshield, *, stream_id="",
        stream_model="", error=False, pipeline_trace_base=None) -> str
        (returns an SSE frame string: ``data: {json}\\n\\n``).
  - StreamLaunchContext(body, redacted_prompt, org_slug, model, ..., start_time: float,
        trace_build_kwargs: dict | None, ...).
  - StreamRunMetrics(provider_start_ts: float, first_token_ts: float, ...);
        .ttft_ms property = (first_token_ts - provider_start_ts) * 1000 (0.0 if either <= 0);
        .duration_ms property = (perf_counter() - provider_start_ts) * 1000 (0.0 if
        provider_start_ts <= 0).
  - compute_addon_split(metrics: dict | None, total_ms: float) -> dict with keys
        {t_addon_pre_ms, t_addon_post_ms, t_t2_ms}.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable

from hypothesis import strategies as st

from ai_mesh_gateway import stream_orchestration
from ai_mesh_gateway.pipeline_trace import compute_addon_split
from ai_mesh_gateway.stream_orchestration import (
    StreamLaunchContext,
    StreamRunMetrics,
    build_stream_trace_frame,
)

__all__ = [
    "InjectedTiming",
    "make_ctx",
    "make_metrics",
    "patch_stream_clock",
    "timing_inputs",
    "drive_stream_trace",
    "parse_pipeline_trace",
    "compute_addon_split",
]


# ── injected-timing record ──────────────────────────────────────────────────


@dataclass(frozen=True)
class InjectedTiming:
    """A fully-ordered synthetic four-epoch timeline for one streaming finalization.

    All values are ``time.perf_counter()``-style seconds (monotonic, arbitrary origin).

        start_time         request-accept epoch (StreamLaunchContext.start_time)
        provider_start_ts  provider SSE opens (StreamRunMetrics.provider_start_ts)
        first_token_ts     first model token (StreamRunMetrics.first_token_ts)
        close_ts           stream-close instant (the value time.perf_counter() returns
                           at frame-build time — injected via patch_stream_clock)

    Invariant for an ordered/valid timeline:
        start_time <= provider_start_ts <= first_token_ts <= close_ts

    Derived spans (milliseconds), matching the design's algebra:
        pre_ms       = (provider_start_ts - start_time) * 1000     # gateway pre-provider P
        ttft_ms      = (first_token_ts - provider_start_ts) * 1000 # provider TTFT Y
        duration_ms  = (close_ts - provider_start_ts) * 1000       # provider-stream-only
        model_out_ms = duration_ms - ttft_ms                       # provider generation G
        total_ms     = (close_ts - start_time) * 1000              # request-accept -> close
    """

    start_time: float
    provider_start_ts: float
    first_token_ts: float
    close_ts: float

    @property
    def pre_ms(self) -> float:
        return (self.provider_start_ts - self.start_time) * 1000

    @property
    def ttft_ms(self) -> float:
        return (self.first_token_ts - self.provider_start_ts) * 1000

    @property
    def duration_ms(self) -> float:
        return (self.close_ts - self.provider_start_ts) * 1000

    @property
    def model_out_ms(self) -> float:
        return self.duration_ms - self.ttft_ms

    @property
    def total_ms(self) -> float:
        return (self.close_ts - self.start_time) * 1000


# ── context / metrics builders ───────────────────────────────────────────────


def make_ctx(
    timing: InjectedTiming | None = None,
    *,
    start_time: float | None = None,
    trace_build_kwargs: dict | None = None,
    **overrides: Any,
) -> StreamLaunchContext:
    """Build a StreamLaunchContext with a synthetic request-accept ``start_time``.

    ``start_time`` is taken from ``timing`` (if given) or the explicit ``start_time``
    kwarg; ``timing`` wins when both are present. Any StreamLaunchContext field can be
    overridden via ``**overrides`` (e.g. ``start_time=0`` for the R1.5 fail-safe edge).
    Mirrors the field defaults used by the sibling stream-trace tests.
    """
    defaults: dict[str, Any] = dict(
        body={"model": "gpt-4o-mini"},
        redacted_prompt=None,
        org_slug="acme",
        model="gpt-4o-mini",
        request_id="zs-stream-latency-anchor",
    )
    if timing is not None:
        defaults["start_time"] = timing.start_time
    elif start_time is not None:
        defaults["start_time"] = start_time
    if trace_build_kwargs is not None:
        defaults["trace_build_kwargs"] = trace_build_kwargs
    defaults.update(overrides)
    return StreamLaunchContext(**defaults)


def make_metrics(
    timing: InjectedTiming | None = None,
    *,
    provider_start_ts: float | None = None,
    first_token_ts: float | None = None,
    **overrides: Any,
) -> StreamRunMetrics:
    """Build a StreamRunMetrics with synthetic provider stream timing.

    ``provider_start_ts`` / ``first_token_ts`` come from ``timing`` (if given) or the
    explicit kwargs. NOTE ``duration_ms`` is NOT stored on the metrics object — it is a
    property that reads ``time.perf_counter()`` live, so it only becomes deterministic
    under ``patch_stream_clock`` (which pins the close instant). Set ``first_token_ts=0``
    (or leave provider_start_ts at 0) to exercise the missing-provider-timing edge (R3.3),
    which makes ``ttft_ms == 0.0``.
    """
    m = StreamRunMetrics()
    if timing is not None:
        m.provider_start_ts = timing.provider_start_ts
        m.first_token_ts = timing.first_token_ts
    if provider_start_ts is not None:
        m.provider_start_ts = provider_start_ts
    if first_token_ts is not None:
        m.first_token_ts = first_token_ts
    m.completed = True
    for key, value in overrides.items():
        setattr(m, key, value)
    return m


# ── injectable clock ──────────────────────────────────────────────────────────


def patch_stream_clock(monkeypatch, close_ts: float) -> None:
    """Pin ``time.perf_counter()`` (as used inside stream_orchestration) to ``close_ts``.

    ``StreamRunMetrics.duration_ms`` and ``build_stream_trace_frame`` both read the clock
    through the ``time`` module imported into ``stream_orchestration`` (source:
    ``end = time.perf_counter()`` in ``duration_ms``; ``time.perf_counter()`` in the frame
    builder). Patching the ``perf_counter`` attribute on that module's ``time`` object
    makes the stream-close instant deterministic, so ``duration_ms`` (which reads live) and
    the frame's total both resolve against ``close_ts``.

    ``time.time()`` (used only for the ``created`` field) is intentionally left alone.
    """
    monkeypatch.setattr(
        stream_orchestration.time, "perf_counter", lambda: close_ts, raising=True
    )


# ── Hypothesis timing-input strategy ──────────────────────────────────────────


@st.composite
def timing_inputs(
    draw,
    *,
    min_pre_ms: float = 0.0,
    max_pre_ms: float = 5000.0,
    min_ttft_ms: float = 0.0,
    max_ttft_ms: float = 5000.0,
    min_gen_ms: float = 0.0,
    max_gen_ms: float = 60000.0,
    allow_near_zero_pre: bool = True,
) -> InjectedTiming:
    """Generate an ORDERED, valid four-epoch timeline as an ``InjectedTiming``.

    Guarantees ``start_time <= provider_start_ts <= first_token_ts <= close_ts`` by
    drawing non-negative *gaps* (in ms) and accumulating them from a base epoch:

        start_time        = base
        provider_start_ts = start_time        + pre_ms  / 1000   (P, gateway pre-provider)
        first_token_ts    = provider_start_ts + ttft_ms / 1000   (Y, provider TTFT)
        close_ts          = first_token_ts    + gen_ms  / 1000   (G, provider generation)

    Requirement 1.4 needs a NEAR-ZERO gateway pre-provider ``P`` (< 1 ms); when
    ``allow_near_zero_pre`` is True the strategy can draw ``pre_ms`` in [0, 1), so callers
    get both the ordered/valid timelines and the near-zero-``P`` boundary case. Bounds are
    parameterizable so a specific property can force, e.g., a large ``P`` (Property 2) or a
    ttft/pre separation of >= 50 ms (the Requirement 8 acceptance test).
    """
    # A finite, non-negative monotonic base epoch (perf_counter has an arbitrary origin).
    base = draw(st.floats(min_value=0.0, max_value=1_000_000.0, allow_nan=False, allow_infinity=False))

    pre_choices = []
    if allow_near_zero_pre:
        # Near-zero gateway pre-provider window for R1.4 (P in [0, 1) ms).
        pre_choices.append(st.floats(min_value=0.0, max_value=1.0, exclude_max=True, allow_nan=False, allow_infinity=False))
    pre_choices.append(
        st.floats(min_value=min_pre_ms, max_value=max_pre_ms, allow_nan=False, allow_infinity=False)
    )
    pre_ms = draw(st.one_of(*pre_choices))
    ttft_ms = draw(
        st.floats(min_value=min_ttft_ms, max_value=max_ttft_ms, allow_nan=False, allow_infinity=False)
    )
    gen_ms = draw(
        st.floats(min_value=min_gen_ms, max_value=max_gen_ms, allow_nan=False, allow_infinity=False)
    )

    start_time = base
    provider_start_ts = start_time + pre_ms / 1000.0
    first_token_ts = provider_start_ts + ttft_ms / 1000.0
    close_ts = first_token_ts + gen_ms / 1000.0
    return InjectedTiming(
        start_time=start_time,
        provider_start_ts=provider_start_ts,
        first_token_ts=first_token_ts,
        close_ts=close_ts,
    )


# ── SSE-frame drive + parse ────────────────────────────────────────────────────


def _default_pipeline_trace_base() -> dict:
    """A minimal 9-stage-shaped pipeline_trace base for the frame builder to reconcile.

    ``build_stream_trace_frame`` only reconciles latency onto ``pipeline_trace_base`` when
    it is truthy; a base with a ``stages`` list exercises the stage-sum / overhead / total
    reconciliation path. Kept deliberately small — later sub-tasks may pass richer bases.
    """
    return {
        "stages": [
            {"name": "input_scan", "action": "allow", "latency_ms": 0.0},
            {"name": "model_output", "action": "allow", "latency_ms": 0.0},
            {"name": "output_guardrail", "action": "allow", "latency_ms": 0.0},
        ]
    }


def drive_stream_trace(
    monkeypatch,
    timing: InjectedTiming,
    *,
    ctx: StreamLaunchContext | None = None,
    metrics: StreamRunMetrics | None = None,
    base_zeroshield: dict | None = None,
    pipeline_trace_base: dict | None = None,
    frame_kwargs: dict | None = None,
) -> dict:
    """Drive ``build_stream_trace_frame`` under the injected clock and return the parsed
    ``pipeline_trace`` dict.

    Builds a ctx (synthetic ``start_time``) and metrics (synthetic provider timing) from
    ``timing`` unless the caller supplies their own, pins the stream-close instant via
    ``patch_stream_clock(monkeypatch, timing.close_ts)`` so ``duration_ms`` + the frame's
    total are deterministic, calls the real ``build_stream_trace_frame``, and parses the
    ``pipeline_trace`` out of the returned SSE frame string.
    """
    if ctx is None:
        ctx = make_ctx(timing)
    if metrics is None:
        metrics = make_metrics(timing)
    if pipeline_trace_base is None:
        pipeline_trace_base = _default_pipeline_trace_base()

    patch_stream_clock(monkeypatch, timing.close_ts)
    frame = build_stream_trace_frame(
        ctx,
        metrics,
        base_zeroshield,
        pipeline_trace_base=pipeline_trace_base,
        **(frame_kwargs or {}),
    )
    return parse_pipeline_trace(frame)


def parse_pipeline_trace(sse_frame: str) -> dict:
    """Strip the SSE framing (``data: {json}\\n\\n``) and return ``payload["pipeline_trace"]``.

    Mirrors the parse convention in test_stream_governance_fidelity.py
    (``json.loads(raw.strip()[6:])``): the frame is ``data: `` + a JSON object + ``\\n\\n``.
    """
    payload = json.loads(sse_frame.strip()[6:])
    return payload["pipeline_trace"]


def parse_frame(sse_frame: str) -> dict:
    """Return the full parsed terminal SSE frame payload (for shape assertions in 2.5)."""
    return json.loads(sse_frame.strip()[6:])


# ── Requirement 8 synthetic-timing acceptance test (task 2.4) ──────────────────
#
# Feature: honest-stream-latency-metric — the primary acceptance proof for Requirement 8.
# Constructs a streaming finalization with a synthetic gateway pre-provider time and a
# synthetic provider TTFT that differ by >= 50 ms, drives the REAL build_stream_trace_frame
# through the injected clock, and asserts (within +/-5 ms) that the re-anchored total, the
# addon, and model_output_ms track the honest wall-clock algebra rather than collapsing to
# the provider TTFT. On any failed assertion the diagnostic reports WHICH timing value
# diverged from its expected bound (R8.6).

_R8_TOLERANCE_MS = 5.0  # R8.2 / R8.3 / R8.5 bound
_R8_TTFT_SEPARATION_MS = 5.0  # R8.4 lower bound for addon != ttft


def _stage_latency_ms(pt: dict, stage_name: str) -> float:
    """Read a stage's latency_ms out of the parsed pipeline_trace.

    model_output_ms is emitted as the ``model_output`` stage's ``latency_ms``
    (build_pipeline_trace maps ``stage_metrics['model_output_ms']`` onto that stage);
    it is not a top-level pipeline_trace key. This mirrors how the production
    ``build_stream_trace_frame`` reads it back via its own ``_stage_latency_ms``.
    """
    for stage in pt.get("stages") or []:
        if isinstance(stage, dict) and stage.get("name") == stage_name:
            return float(stage.get("latency_ms") or 0.0)
    return 0.0


def _r8_timing() -> InjectedTiming:
    """A synthetic four-epoch timeline where gateway pre-provider time and provider TTFT
    differ by >= 50 ms (R8.1).

    The addon algebra after re-anchoring is ``t_addon_pre_ms = total - model_output - post
    = (P + Y + G) - G - 0 = P + Y`` (design "Why the addon becomes gateway pre-time"). For
    R8.3 (addon ~= gateway pre-provider time P, +/-5 ms) to hold WITH R8.1 (|P - Y| >= 50 ms),
    the provider TTFT Y is kept small (3 ms) so ``P + Y ~= P``:

        gateway pre-provider P = provider_start_ts - start_time = 120 ms
        provider TTFT        Y = first_token_ts - provider_start_ts = 3 ms   (|P - Y| = 117 ms >= 50)
        provider generation  G = close_ts - first_token_ts = 400 ms
        duration_ms          = Y + G = 403 ms
        model_output_ms      = duration_ms - ttft_ms = 400 ms
        total (close-start)  = P + Y + G = 523 ms
        t_addon_pre_ms       = total - model_output - post = 523 - 400 - 0 = 123 ~= P (+/-5)
    """
    base = 10_000.0  # arbitrary monotonic origin (perf_counter has no fixed epoch)
    start_time = base
    provider_start_ts = start_time + 120.0 / 1000.0  # P = 120 ms
    first_token_ts = provider_start_ts + 3.0 / 1000.0  # Y = 3 ms (TTFT << P)
    close_ts = first_token_ts + 400.0 / 1000.0  # G = 400 ms
    return InjectedTiming(
        start_time=start_time,
        provider_start_ts=provider_start_ts,
        first_token_ts=first_token_ts,
        close_ts=close_ts,
    )


def test_requirement8_synthetic_timing_addon_tracks_gateway_not_ttft(monkeypatch):
    """R8.1-8.6: synthetic pre-time vs TTFT (>= 50 ms apart) proves the addon is honest.

    Drives the real build_stream_trace_frame with an injected clock and asserts (+/-5 ms)
    the four Requirement-8 bounds; each assertion names the diverging timing value (R8.6).
    """
    timing = _r8_timing()

    # Guard the fixture invariant (R8.1): synthetic pre-time and TTFT differ by >= 50 ms.
    assert abs(timing.pre_ms - timing.ttft_ms) >= 50.0, (
        "fixture invariant violated: gateway pre-provider time "
        f"({timing.pre_ms:.3f} ms) and provider TTFT ({timing.ttft_ms:.3f} ms) must "
        "differ by >= 50 ms"
    )

    # trace_build_kwargs makes _rebuilt_stream_trace run the real build_pipeline_trace,
    # which derives + emits model_output_ms (= duration_ms - ttft_ms) as a top-level
    # trace key; build_stream_trace_frame then recomputes t_addon_pre_ms against the
    # re-anchored elapsed_ms. Without it the rebuild is a no-op and model_output_ms /
    # the addon are absent — so the acceptance test must exercise the full rebuild path.
    ctx = make_ctx(
        timing,
        trace_build_kwargs={"prompt": "hi", "stage_metrics": {}, "final_action": "allow"},
    )
    pt = drive_stream_trace(monkeypatch, timing, ctx=ctx)

    total = pt["total_latency_ms"]
    ttft = pt["ttft_ms"]
    model_output = _stage_latency_ms(pt, "model_output")  # model_output_ms lives on the stage
    addon = pt["t_addon_pre_ms"]

    expected_total = timing.total_ms  # close_ts - start_time
    expected_pre = timing.pre_ms  # gateway pre-provider P
    expected_model_output = timing.model_out_ms  # duration_ms - ttft_ms

    # R8.2: total_latency_ms ~= full wall-clock (close - start_time), +/-5 ms.
    assert abs(total - expected_total) <= _R8_TOLERANCE_MS, (
        "timing value diverged: total_latency_ms "
        f"({total:.3f} ms) is not within +/-{_R8_TOLERANCE_MS} ms of the expected "
        f"full wall-clock close-start ({expected_total:.3f} ms); "
        f"delta={abs(total - expected_total):.3f} ms"
    )

    # R8.3: t_addon_pre_ms ~= synthetic gateway pre-provider time, +/-5 ms.
    assert abs(addon - expected_pre) <= _R8_TOLERANCE_MS, (
        "timing value diverged: t_addon_pre_ms "
        f"({addon:.3f} ms) is not within +/-{_R8_TOLERANCE_MS} ms of the expected "
        f"gateway pre-provider time ({expected_pre:.3f} ms); "
        f"delta={abs(addon - expected_pre):.3f} ms"
    )

    # R8.4: since synthetic pre-time and TTFT differ by > 5 ms, the addon must differ
    # from the provider TTFT by > 5 ms (the addon never degenerates to TTFT).
    assert abs(addon - ttft) > _R8_TTFT_SEPARATION_MS, (
        "timing value diverged: t_addon_pre_ms "
        f"({addon:.3f} ms) must differ from ttft_ms ({ttft:.3f} ms) by more than "
        f"{_R8_TTFT_SEPARATION_MS} ms (addon must not collapse to provider TTFT); "
        f"delta={abs(addon - ttft):.3f} ms"
    )

    # R8.5: model_output_ms ~= provider generation time (duration_ms - ttft_ms), +/-5 ms.
    assert abs(model_output - expected_model_output) <= _R8_TOLERANCE_MS, (
        "timing value diverged: model_output_ms "
        f"({model_output:.3f} ms) is not within +/-{_R8_TOLERANCE_MS} ms of the "
        f"expected provider generation time duration-ttft "
        f"({expected_model_output:.3f} ms); "
        f"delta={abs(model_output - expected_model_output):.3f} ms"
    )

# ── Edge / fail-safe and shape unit tests (task 2.5) ───────────────────────────
#
# Feature: honest-stream-latency-metric — the fail-safe branches (Error Handling section
# of the design) and the backward-compatible trace/frame shape (Requirement 5). These
# exercise the REAL build_stream_trace_frame through the injected-clock harness (task 1),
# NOT the timing algebra (that is covered by the property tests 2.3 and the R8 acceptance
# test 2.4).
#
# Validates: Requirements 1.5, 2.5, 3.3, 4.4, 5.1, 5.2, 5.3, 5.4, 5.5

# The TOP-LEVEL latency keys the pipeline_trace MUST retain after the anchoring change
# (Requirement 5.1 / 5.4). Each MUST be present and numeric; none renamed or removed.
#
# NOTE on model_output_ms (R5.1): provider generation time is carried on the
# ``model_output`` STAGE (``stages[].latency_ms``), NOT as a top-level pipeline_trace key
# (build_pipeline_trace / build_stream_trace_frame never emit a top-level model_output_ms).
# The shape test therefore asserts model_output_ms via the stage (``_model_output_stage_ms``)
# and the remaining seven keys at the top level — matching the real production trace shape
# while still covering "model_output_ms retained + numeric".
_REQUIRED_LATENCY_KEYS = (
    "total_latency_ms",
    "ttft_ms",
    "stage_latency_sum_ms",
    "overhead_ms",
    "t_addon_pre_ms",
    "t_addon_post_ms",
    "t_t2_ms",
)


def _model_output_stage_ms(pt: dict) -> float | None:
    """Return the ``model_output`` stage's latency_ms from a pipeline_trace, else None.

    Provider generation time (model_output_ms) is a STAGE latency in the production trace,
    not a top-level key — mirrors stream_orchestration._stage_latency_ms.
    """
    for stage in pt.get("stages") or []:
        if isinstance(stage, dict) and stage.get("name") == "model_output":
            try:
                return float(stage.get("latency_ms") or 0.0)
            except (TypeError, ValueError):
                return 0.0
    return None


def _shape_pipeline_trace_base() -> dict:
    """A pipeline_trace base carrying every TOP-LEVEL required latency key (all numeric)
    plus a ``model_output`` stage (provider generation time lives there, R5.1), so the
    frame's reconciliation runs and the emitted trace can be asserted a superset of the
    pre-change key set (R5.1-5.4). Deliberately carries NO top-level ``model_output_ms`` —
    production never emits one, so requiring it would test a fabricated contract."""
    return {
        "total_latency_ms": 0.0,
        "ttft_ms": 0.0,
        "stage_latency_sum_ms": 0.0,
        "overhead_ms": 0.0,
        "t_addon_pre_ms": 0.0,
        "t_addon_post_ms": 0.0,
        "t_t2_ms": 0.0,
        "stages": [
            {"name": "input_scan", "action": "allow", "latency_ms": 0.0},
            {"name": "model_output", "action": "allow", "latency_ms": 0.0},
            {"name": "output_guardrail", "action": "allow", "latency_ms": 0.0},
        ],
    }


def test_unset_start_time_falls_back_to_duration_ms(monkeypatch):
    """R1.5: ctx.start_time = 0 -> total_latency_ms == duration_ms, non-negative, no raise.

    When the request-accept epoch is unset/falsy the frame builder falls back to the
    provider-stream-only duration_ms (never re-anchoring on a missing epoch), reports a
    non-negative total, and finalizes without raising.
    """
    timing = InjectedTiming(
        start_time=5_000.0,
        provider_start_ts=5_000.0 + 120.0 / 1000.0,  # P = 120 ms (would be included if anchored)
        first_token_ts=5_000.0 + 150.0 / 1000.0,  # ttft = 30 ms
        close_ts=5_000.0 + 550.0 / 1000.0,  # duration = 430 ms, total = 550 ms
    )
    # ctx.start_time = 0 forces the R1.5 fail-safe fallback branch. Build the ctx WITHOUT
    # ``timing`` so the explicit start_time=0 is honoured (make_ctx lets timing.start_time
    # win when a timing is passed); the provider stream timing still comes from ``timing``
    # via the metrics + the pinned clock.
    ctx = make_ctx(start_time=0)
    metrics = make_metrics(timing)
    assert ctx.start_time == 0  # confirm the fail-safe precondition is actually exercised

    pt = drive_stream_trace(monkeypatch, timing, ctx=ctx, metrics=metrics)

    total = pt["total_latency_ms"]
    # duration_ms = (close_ts - provider_start_ts) * 1000, deterministic under the pinned clock.
    expected_duration_ms = round(timing.duration_ms, 2)
    assert total == expected_duration_ms, (
        f"R1.5: with start_time=0 the total must fall back to duration_ms "
        f"({expected_duration_ms} ms), got {total} ms"
    )
    assert total >= 0, f"R1.5: total_latency_ms must be non-negative, got {total} ms"
    # It must NOT have re-anchored on close - 0 (~= close_ts * 1000, a huge value).
    assert total < timing.total_ms + 1.0, (
        "R1.5: fallback total must equal duration_ms, not a request-accept-anchored value"
    )


def test_missing_first_token_ttft_absent_not_substituted(monkeypatch):
    """R3.3: first_token_ts = 0 -> ttft_ms absent/null, never substituted.

    When provider stream timing is unavailable (first_token_ts <= 0) metrics.ttft_ms is
    0.0, so the frame stamps NO ttft_ms and does not substitute the request-accept epoch
    (or any other timestamp) for it. A base without a ttft_ms key must stay without one.
    """
    timing = InjectedTiming(
        start_time=7_000.0,
        provider_start_ts=7_000.0 + 100.0 / 1000.0,
        first_token_ts=0.0,  # missing provider first-token timing -> ttft_ms == 0.0
        close_ts=7_000.0 + 500.0 / 1000.0,
    )
    metrics = make_metrics(timing, first_token_ts=0.0)
    assert metrics.ttft_ms == 0.0  # property invariant under the missing-timing edge

    # A base WITHOUT a ttft_ms key: the frame must not introduce/substitute one.
    base = {
        "stages": [
            {"name": "input_scan", "action": "allow", "latency_ms": 0.0},
            {"name": "model_output", "action": "allow", "latency_ms": 0.0},
            {"name": "output_guardrail", "action": "allow", "latency_ms": 0.0},
        ]
    }
    pt = drive_stream_trace(monkeypatch, timing, metrics=metrics, pipeline_trace_base=base)

    # ttft_ms must be absent (not stamped) — and if a consumer added a null it must not be
    # a substituted request-accept value.
    assert pt.get("ttft_ms") in (None, 0, 0.0), (
        f"R3.3: ttft_ms must be absent/null when provider timing is missing, got "
        f"{pt.get('ttft_ms')!r}"
    )
    # Positively assert it was NOT substituted with the request-accept-derived total/pre.
    assert "ttft_ms" not in pt, (
        "R3.3: the frame must not stamp a ttft_ms when metrics.ttft_ms is 0.0 "
        "(no substitution from start_time)"
    )


def test_ttft_ge_duration_yields_zero_model_output(monkeypatch):
    """R4.4: ttft_ms >= duration_ms -> model_output_ms == 0 (generation not fabricated).

    When ttft_ms >= duration_ms the provider generation time is underivable, so
    _rebuilt_stream_trace does NOT setdefault model_output_ms and it stays 0 — the
    generation time is never fabricated. Driven through the trace REBUILD path (a
    trace_build_kwargs stashed on ctx) so build_pipeline_trace materialises a
    model_output stage.
    """
    # ttft (100 ms) >= duration (100 ms) -> model_output underivable.
    timing = InjectedTiming(
        start_time=9_000.0,
        provider_start_ts=9_000.0 + 50.0 / 1000.0,  # P = 50 ms
        first_token_ts=9_000.0 + 150.0 / 1000.0,  # ttft = 100 ms
        close_ts=9_000.0 + 150.0 / 1000.0,  # duration = 100 ms (== ttft) -> not > ttft
    )
    assert timing.ttft_ms >= timing.duration_ms  # the R4.4 precondition

    # A full trace_build_kwargs so _rebuilt_stream_trace runs build_pipeline_trace and
    # materialises a model_output stage (the rebuild is a no-op without stashed kwargs).
    ctx = make_ctx(
        timing,
        trace_build_kwargs={"prompt": "hi", "stage_metrics": {}, "final_action": "allow"},
    )
    metrics = make_metrics(timing)

    pt = drive_stream_trace(monkeypatch, timing, ctx=ctx, metrics=metrics)

    # model_output_ms is the model_output STAGE latency (not a top-level key). Because
    # ttft_ms >= duration_ms, _rebuilt_stream_trace does NOT setdefault it, so it stays 0.
    model_output_ms = _model_output_stage_ms(pt)
    assert model_output_ms == 0, (
        f"R4.4: model_output_ms (model_output stage latency) must be 0 when ttft_ms "
        f"({timing.ttft_ms:.1f} ms) >= duration_ms ({timing.duration_ms:.1f} ms), "
        f"got {model_output_ms}"
    )


def test_model_output_exceeds_total_clamps_addon_to_zero(monkeypatch):
    """R2.5: model_output_ms > total_latency_ms -> t_addon_pre_ms == 0, other keys intact.

    When the computed addon would be negative (model generation reported larger than the
    total) compute_addon_split clamps t_addon_pre_ms to 0, and the remaining latency
    measurements are preserved (not discarded).
    """
    timing = InjectedTiming(
        start_time=11_000.0,
        provider_start_ts=11_000.0 + 5.0 / 1000.0,
        first_token_ts=11_000.0 + 10.0 / 1000.0,
        close_ts=11_000.0 + 40.0 / 1000.0,  # total ~= 40 ms
    )
    # A base whose model_output stage latency (5000 ms) far exceeds the ~40 ms total, so
    # the frame's addon recompute yields a NEGATIVE pre that must clamp to 0.
    base = {
        "stages": [
            {"name": "input_scan", "action": "allow", "latency_ms": 0.0},
            {"name": "model_output", "action": "allow", "latency_ms": 5000.0},
            {"name": "output_guardrail", "action": "allow", "latency_ms": 0.0},
        ]
    }
    pt = drive_stream_trace(monkeypatch, timing, pipeline_trace_base=base)

    assert pt["t_addon_pre_ms"] == 0, (
        f"R2.5: t_addon_pre_ms must clamp to 0 when model_output_ms exceeds the total, "
        f"got {pt['t_addon_pre_ms']}"
    )
    # Other latency keys remain intact (not discarded) and numeric.
    for key in ("total_latency_ms", "t_addon_post_ms", "t_t2_ms", "stage_latency_sum_ms", "overhead_ms"):
        assert key in pt, f"R2.5: latency key {key!r} must be preserved alongside the clamped addon"
        assert isinstance(pt[key], (int, float)) and not isinstance(pt[key], bool), (
            f"R2.5: latency key {key!r} must stay numeric, got {type(pt[key]).__name__}"
        )
    # The total itself is still the re-anchored honest wall-clock (not discarded/zeroed).
    assert pt["total_latency_ms"] > 0


def test_built_frame_retains_all_latency_keys_and_shape(monkeypatch):
    """R5.1-5.5: the built pipeline_trace keeps every required latency key (numeric, none
    renamed/removed), the top-level SSE frame keys are unchanged, and choices == [].

    Uses a base carrying every pre-change latency key so the emitted trace can be asserted
    a superset with each value numeric; then asserts the terminal frame's own top-level
    shape (R5.5).
    """
    timing = InjectedTiming(
        start_time=13_000.0,
        provider_start_ts=13_000.0 + 80.0 / 1000.0,
        first_token_ts=13_000.0 + 110.0 / 1000.0,  # ttft = 30 ms
        close_ts=13_000.0 + 500.0 / 1000.0,
    )
    base = _shape_pipeline_trace_base()
    pre_change_keys = set(base) - {"stages"}  # the value-bearing latency keys we must retain

    metrics = make_metrics(timing)
    patch_stream_clock(monkeypatch, timing.close_ts)
    frame_str = build_stream_trace_frame(
        make_ctx(timing),
        metrics,
        None,  # base_zeroshield
        pipeline_trace_base=base,
    )

    # Full-frame shape (R5.5): a chat.completion.chunk with choices == [], a zeroshield
    # object, and a pipeline_trace object — no added/removed/renamed top-level keys vs the
    # pre-change terminal frame.
    frame = parse_frame(frame_str)
    assert frame["object"] == "chat.completion.chunk"
    assert frame["choices"] == [], f"R5.5: choices must be an empty array, got {frame['choices']!r}"
    assert isinstance(frame["zeroshield"], dict), "R5.5: frame must carry a zeroshield object"
    assert isinstance(frame["pipeline_trace"], dict), "R5.5: frame must carry a pipeline_trace object"
    assert set(frame.keys()) == {
        "id",
        "object",
        "created",
        "model",
        "choices",
        "zeroshield",
        "pipeline_trace",
    }, f"R5.5: unexpected top-level frame keys: {sorted(frame.keys())}"

    pt = frame["pipeline_trace"]
    # R5.3: emitted key set is a superset of the pre-change key set (zero removed keys).
    assert pre_change_keys.issubset(set(pt)), (
        f"R5.3: emitted trace must retain every pre-change key; "
        f"missing={pre_change_keys - set(pt)}"
    )
    # R5.1 / R5.2 / R5.4: every top-level required latency key present (correct name) and numeric.
    for key in _REQUIRED_LATENCY_KEYS:
        assert key in pt, f"R5.1/R5.2: required latency key {key!r} missing (renamed or removed)"
        assert isinstance(pt[key], (int, float)) and not isinstance(pt[key], bool), (
            f"R5.4: latency key {key!r} must be numeric, got {type(pt[key]).__name__}"
        )
    # R5.1 / R5.4 for model_output_ms: provider generation time is retained as the
    # model_output STAGE latency (numeric), not a top-level key (production shape).
    model_output_ms = _model_output_stage_ms(pt)
    assert model_output_ms is not None, (
        "R5.1: the model_output stage (provider generation time / model_output_ms) must be retained"
    )
    assert isinstance(model_output_ms, (int, float)) and not isinstance(model_output_ms, bool), (
        f"R5.4: model_output stage latency must be numeric, got {type(model_output_ms).__name__}"
    )
