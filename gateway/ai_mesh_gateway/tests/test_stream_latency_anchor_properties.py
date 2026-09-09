"""Property tests for the streaming timing algebra (Feature: honest-stream-latency-metric, task 2.3).

These property tests exercise the request-accept-anchored streaming total and the addon
split it drives in ``build_stream_trace_frame`` (production change task 2, already applied in
stream_orchestration.py). They reuse the injectable-clock harness + Hypothesis timing strategy
scaffolded in task 1 (``test_stream_latency_anchor.py``).

Clock model (from the task-1 harness): ``patch_stream_clock`` pins
``stream_orchestration.time.perf_counter`` to the synthetic stream-close instant, so both the
live ``StreamRunMetrics.duration_ms`` read and the frame's own request-accept-anchored total
resolve deterministically against ``timing.close_ts``.

Addon-input model (from the production recompute, stream_orchestration.py):
``build_stream_trace_frame`` recomputes the addon via
``compute_addon_split({model_output_ms: _stage_latency_ms(pt, "model_output"),
output_guardrail_ms: _stage_latency_ms(pt, "output_guardrail"), tier2_ms: pt["t_t2_ms"]},
elapsed_ms)`` where ``pt`` is the reconciled trace. With no ``trace_build_kwargs`` on the ctx,
``_rebuilt_stream_trace`` returns the passed ``pipeline_trace_base`` unchanged, so a base whose
``model_output`` stage carries ``latency_ms == duration_ms - ttft_ms`` (the provider generation
time the design preserves) feeds the real production addon algebra its ``model_output_ms``.
"""
from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from ai_mesh_gateway.stream_orchestration import build_stream_trace_frame

from test_stream_latency_anchor import (
    InjectedTiming,
    make_ctx,
    make_metrics,
    parse_pipeline_trace,
    patch_stream_clock,
    timing_inputs,
)

# ±5 ms tolerance (design Testing Strategy, aligned with Requirement 8).
TOL_MS = 5.0
# Minimum iterations per property (design: >= 100).
ITERATIONS = 200
# A fixed positive floor for the request-accept epoch so ``ctx.start_time`` is TRUTHY and the
# re-anchored path is exercised. When ``start_time`` is 0/falsy the production code takes the
# R1.5 fail-safe (total = duration_ms) — that branch is covered by the edge tests (task 2.5),
# not the re-anchoring property tests here. The task-1 strategy can draw base == 0.0, so shift
# every epoch up by this floor (preserving all gaps/spans) to keep start_time > 0.
_START_FLOOR_S = 10.0


def _truthy_start(timing: InjectedTiming) -> InjectedTiming:
    """Shift a generated timeline up by a fixed positive floor so ``start_time`` is truthy.

    Adds the same constant to all four epochs, so pre_ms / ttft_ms / duration_ms / model_out_ms
    / total_ms are all preserved exactly — only the (arbitrary-origin) base moves off zero.
    """
    return InjectedTiming(
        start_time=timing.start_time + _START_FLOOR_S,
        provider_start_ts=timing.provider_start_ts + _START_FLOOR_S,
        first_token_ts=timing.first_token_ts + _START_FLOOR_S,
        close_ts=timing.close_ts + _START_FLOOR_S,
    )


def anchored_timing(**kw):
    """``timing_inputs`` shifted so ``start_time`` is always > 0 (re-anchored path)."""
    return timing_inputs(**kw).map(_truthy_start)


def _base_with_model_output(model_out_ms: float, post_ms: float = 0.0) -> dict:
    """A pipeline_trace_base whose ``model_output`` / ``output_guardrail`` stage latencies are
    the provider generation time and the post-model gateway time.

    ``build_stream_trace_frame`` recomputes the addon from
    ``_stage_latency_ms(pt, "model_output")`` / ``_stage_latency_ms(pt, "output_guardrail")``.
    With no ``trace_build_kwargs`` on the ctx, ``_rebuilt_stream_trace`` returns this base
    unchanged, so these are exactly the ``model_output_ms`` / ``output_guardrail_ms`` fed to
    ``compute_addon_split`` against the re-anchored total.
    """
    return {
        "stages": [
            {"name": "input_scan", "action": "allow", "latency_ms": 0.0},
            {"name": "model_output", "action": "allow", "latency_ms": round(model_out_ms, 1)},
            {"name": "output_guardrail", "action": "allow", "latency_ms": round(post_ms, 1)},
        ]
    }


def _drive(timing: InjectedTiming, *, post_ms: float = 0.0) -> dict:
    """Drive the real ``build_stream_trace_frame`` under the injected clock and return the
    parsed ``pipeline_trace`` dict, with the base's ``model_output`` stage set to the provider
    generation time (``duration_ms - ttft_ms``) so the production addon recompute has a real
    ``model_output_ms`` input.

    Uses a per-call ``pytest.MonkeyPatch()`` context (not the function-scoped ``monkeypatch``
    fixture) so the injected clock is set and UNDONE around each generated Hypothesis example —
    the fixture-with-@given health-check pitfall the harness docstring warns about.
    """
    ctx = make_ctx(timing)
    metrics = make_metrics(timing)
    base = _base_with_model_output(timing.model_out_ms, post_ms=post_ms)
    with pytest.MonkeyPatch.context() as mp:
        patch_stream_clock(mp, timing.close_ts)
        frame = build_stream_trace_frame(ctx, metrics, None, pipeline_trace_base=base)
    return parse_pipeline_trace(frame)


# ── Property 1 ────────────────────────────────────────────────────────────────
# Feature: honest-stream-latency-metric, Property 1: Streaming total is the full wall-clock
# from the request-accept epoch — total_latency_ms equals close - start_time, and exceeds the
# provider-only duration_ms by exactly the gateway pre-provider delay P (provider_start_ts -
# start_time).
@settings(max_examples=ITERATIONS, deadline=None)
@given(timing=anchored_timing())
def test_property1_total_is_full_wall_clock(timing: InjectedTiming) -> None:
    pt = _drive(timing)
    total = pt["total_latency_ms"]
    # total ≈ close - start_time (the full request-accept -> close wall-clock).
    assert abs(total - timing.total_ms) <= TOL_MS, (
        f"total_latency_ms diverged: got {total}, expected close-start {timing.total_ms} "
        f"(P={timing.pre_ms}, ttft={timing.ttft_ms}, gen={timing.model_out_ms})"
    )
    # total - duration_ms ≈ P (gateway pre-provider delay). Includes the near-zero-P case (R1.4).
    assert abs((total - timing.duration_ms) - timing.pre_ms) <= TOL_MS, (
        f"total - duration_ms diverged from P: total={total}, duration_ms={timing.duration_ms}, "
        f"P={timing.pre_ms}"
    )


# ── Property 2 ────────────────────────────────────────────────────────────────
# Feature: honest-stream-latency-metric, Property 2: The addon differs from provider TTFT by
# the gateway pre-model time — with P chosen large (>> ttft, so |P| >> 5 ms), t_addon_pre_ms is
# not equal to ttft_ms; they differ by more than 5 ms so the addon never degenerates to TTFT.
@settings(max_examples=ITERATIONS, deadline=None)
@given(
    # Force a large gateway pre-provider P and a modest TTFT so P and ttft are well separated
    # ( |P - ttft| > 5 ms ). allow_near_zero_pre=False keeps P in [min_pre_ms, max_pre_ms].
    timing=anchored_timing(
        min_pre_ms=200.0,
        max_pre_ms=5000.0,
        min_ttft_ms=0.0,
        max_ttft_ms=50.0,
        allow_near_zero_pre=False,
    )
)
def test_property2_addon_differs_from_ttft_by_pre_time(timing: InjectedTiming) -> None:
    pt = _drive(timing)
    addon = pt["t_addon_pre_ms"]
    # ttft_ms is stamped only when a provider token was seen (metrics.ttft_ms > 0); when absent
    # (R3.3) it reads as 0 — and the large-P addon still differs from it by > 5 ms.
    ttft = pt.get("ttft_ms", 0.0)
    # The addon carries the gateway pre-provider term P that pure TTFT lacks, so addon != ttft.
    assert abs(addon - ttft) > TOL_MS, (
        f"addon degenerated to TTFT: t_addon_pre_ms={addon}, ttft_ms={ttft} "
        f"(P={timing.pre_ms}, expected |addon-ttft| > {TOL_MS})"
    )
    # After the fix the addon is ≈ P + Y (gateway pre-provider + provider TTFT), which differs
    # from ttft_ms (== Y) by ≈ P. Verify the separation tracks P within tolerance.
    assert abs((addon - ttft) - timing.pre_ms) <= TOL_MS, (
        f"addon - ttft diverged from P: addon={addon}, ttft={ttft}, P={timing.pre_ms}"
    )


# ── Property 3 ────────────────────────────────────────────────────────────────
# Feature: honest-stream-latency-metric, Property 3: The addon split is the total-minus-model
# algebra — t_addon_pre_ms == max(0, total_latency_ms - model_output_ms - t_addon_post_ms),
# computed against the request-accept-anchored total.
@settings(max_examples=ITERATIONS, deadline=None)
@given(
    timing=anchored_timing(),
    # A non-negative post-model gateway time (output_guardrail); include 0 (clean stream).
    post_ms=st.floats(min_value=0.0, max_value=2000.0, allow_nan=False, allow_infinity=False),
)
def test_property3_addon_split_is_total_minus_model_algebra(
    timing: InjectedTiming, post_ms: float
) -> None:
    pt = _drive(timing, post_ms=post_ms)
    total = pt["total_latency_ms"]
    model_out = timing.model_out_ms  # the model_output stage latency fed to the split
    post = pt["t_addon_post_ms"]
    addon = pt["t_addon_pre_ms"]
    expected = max(0.0, total - model_out - post)
    assert abs(addon - expected) <= TOL_MS, (
        f"addon != max(0, total - model_output - post): t_addon_pre_ms={addon}, "
        f"total={total}, model_output_ms={model_out}, t_addon_post_ms={post}, "
        f"expected={expected}"
    )


# ── Property 4 ────────────────────────────────────────────────────────────────
# Feature: honest-stream-latency-metric, Property 4: TTFT is the provider delta and is
# independent of the request-accept epoch — ttft_ms == (first_token_ts - provider_start_ts) *
# 1000, invariant across start_time, and distinct from t_addon_pre_ms.
@settings(max_examples=ITERATIONS, deadline=None)
@given(
    # Force ttft > 0 (a token WAS seen, so ttft_ms is stamped) and a large P (>> ttft) so ttft
    # is distinct from the addon. min_ttft_ms >= 1 guarantees the ttft_ms field is present.
    timing=anchored_timing(
        min_pre_ms=200.0,
        max_pre_ms=5000.0,
        min_ttft_ms=1.0,
        max_ttft_ms=50.0,
        allow_near_zero_pre=False,
    ),
    # A second, distinct request-accept epoch offset (ms) applied to the SAME provider timing.
    start_shift_ms=st.floats(min_value=1.0, max_value=5000.0, allow_nan=False, allow_infinity=False),
)
def test_property4_ttft_is_provider_delta_invariant_across_start(
    timing: InjectedTiming, start_shift_ms: float
) -> None:
    # ttft_ms == (first_token_ts - provider_start_ts) * 1000 (a token was seen here).
    pt = _drive(timing)
    ttft = pt["ttft_ms"]
    expected_ttft = (timing.first_token_ts - timing.provider_start_ts) * 1000
    assert abs(ttft - expected_ttft) <= TOL_MS, (
        f"ttft_ms != provider delta: got {ttft}, expected {expected_ttft}"
    )
    # Distinct from t_addon_pre_ms (P is large here so the addon carries the extra P term).
    assert abs(ttft - pt["t_addon_pre_ms"]) > TOL_MS, (
        f"ttft_ms coincides with t_addon_pre_ms: ttft={ttft}, addon={pt['t_addon_pre_ms']}"
    )

    # Invariance across the request-accept epoch: hold the provider timing (provider_start_ts,
    # first_token_ts, and the provider-stream duration) fixed while shifting start_time EARLIER
    # by start_shift_ms. duration_ms is anchored on provider_start_ts and close_ts is unchanged,
    # so the provider-stream duration is identical; only the request-accept epoch moves. The
    # +_START_FLOOR_S floor keeps the shifted start_time > 0 (still the re-anchored path).
    shift_s = start_shift_ms / 1000.0
    timing_b = InjectedTiming(
        start_time=timing.start_time - shift_s,
        provider_start_ts=timing.provider_start_ts,
        first_token_ts=timing.first_token_ts,
        close_ts=timing.close_ts,
    )
    ttft_a = pt["ttft_ms"]
    ttft_b = _drive(timing_b)["ttft_ms"]
    assert abs(ttft_a - ttft_b) <= TOL_MS, (
        f"ttft_ms not invariant across start_time: {ttft_a} vs {ttft_b} "
        f"(start shifted by {start_shift_ms} ms)"
    )


# ── Property 5 ────────────────────────────────────────────────────────────────
# Feature: honest-stream-latency-metric, Property 5: model_output_ms is provider generation
# time and is invariant under re-anchoring — model_output_ms == duration_ms - ttft_ms, and
# holding the provider timing fixed while varying start_time leaves model_output_ms unchanged.
@settings(max_examples=ITERATIONS, deadline=None)
@given(
    # Ensure duration_ms > ttft_ms (generation time positive) by giving a positive generation gap.
    timing=anchored_timing(min_gen_ms=1.0, max_gen_ms=60000.0),
    start_shift_ms=st.floats(min_value=1.0, max_value=5000.0, allow_nan=False, allow_infinity=False),
)
def test_property5_model_output_is_generation_invariant_across_start(
    timing: InjectedTiming, start_shift_ms: float
) -> None:
    # The model_output stage latency the frame feeds to compute_addon_split IS the provider
    # generation time duration_ms - ttft_ms (design: model_output_ms preserved). The frame
    # reads it back via _stage_latency_ms; assert the base we supply equals the algebra and
    # that the reconciled trace preserves it (invariant under the re-anchored total).
    pt_a = _drive(timing)
    model_out_a = next(
        s["latency_ms"] for s in pt_a["stages"] if s.get("name") == "model_output"
    )
    expected = timing.duration_ms - timing.ttft_ms
    assert abs(model_out_a - expected) <= TOL_MS, (
        f"model_output_ms != duration_ms - ttft_ms: got {model_out_a}, expected {expected}"
    )

    # Invariance across the request-accept epoch: shift start_time (and close by the same delta
    # to hold the provider-stream duration constant); model_output_ms must not change.
    shift_s = start_shift_ms / 1000.0
    timing_b = InjectedTiming(
        start_time=timing.start_time - shift_s,
        provider_start_ts=timing.provider_start_ts,
        first_token_ts=timing.first_token_ts,
        close_ts=timing.close_ts,
    )
    pt_b = _drive(timing_b)
    model_out_b = next(
        s["latency_ms"] for s in pt_b["stages"] if s.get("name") == "model_output"
    )
    assert abs(model_out_a - model_out_b) <= TOL_MS, (
        f"model_output_ms not invariant across start_time: {model_out_a} vs {model_out_b}"
    )
