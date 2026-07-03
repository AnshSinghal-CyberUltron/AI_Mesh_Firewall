import test from "node:test";
import assert from "node:assert/strict";
import {
  honestStageAction,
  buildHonestTraceStages,
  extractRealStages,
  extractFinalAction,
  normalizeStages,
  resolveTotalLatencyMs,
  formatPipelineDurationMs,
  parsePipelineDurationMs,
  latencyMsWithinTolerance,
  LATENCY_MS_PARITY_TOLERANCE,
  resolveTtftMs,
  resolveLatencyBreakdown,
  resolveLatencyHints,
  formatDominantStageLabel,
} from "./pipelineTrace.js";

// Robustness: the trace card (StageTimeline) renders `stage.action` per element, so a
// null/non-object stage would CRASH the whole card. normalizeStages drops non-renderable
// junk (and handles a null/non-array prop) without fabricating anything.
test("normalizeStages drops null / non-object stages and keeps valid ones", () => {
  const good = { name: "auth", action: "allow" };
  const good2 = { name: "input_scan", action: "redact" };
  assert.deepEqual(
    normalizeStages([good, null, "junk", undefined, 42, good2, {}]),
    [good, good2, {}],
  );
});

test("normalizeStages returns [] for null / non-array input (no crash)", () => {
  assert.deepEqual(normalizeStages(null), []);
  assert.deepEqual(normalizeStages(undefined), []);
  assert.deepEqual(normalizeStages("nope"), []);
  assert.deepEqual(normalizeStages({ stages: [] }), []);
});

test("normalizeStages does not mutate the input array", () => {
  const src = [{ action: "allow" }, null];
  const out = normalizeStages(src);
  assert.equal(src.length, 2, "input must not be mutated");
  assert.equal(out.length, 1);
});

// TRACE_UI_CONTRACT.md: each stage must render its OWN action; a redact whose
// scrubber was a no-op is displayed as flag (never a phantom redaction).

test("honestStageAction returns the stage's own action", () => {
  assert.equal(honestStageAction({ action: "block" }), "block");
  assert.equal(honestStageAction({ action: "redact" }), "redact");
  assert.equal(honestStageAction({}), "allow");
});

test("honestStageAction downgrades a no-op redact to flag", () => {
  assert.equal(honestStageAction({ action: "redact", redact_noop: true }), "flag");
  assert.equal(honestStageAction({ action: "redact", metadata: { redact_noop: true } }), "flag");
});

test("buildHonestTraceStages gives EACH stage its own badge (no global smear)", () => {
  const realStages = [
    { stage: "policy", action: "redact", detection_tier: "policy", matched_policy_names: ["PII Detection & Redaction"], latency_ms: 1.2 },
    { stage: "input_scan", action: "allow", detection_tier: "tier_1", latency_ms: 4.5 },
    { stage: "output_guard", action: "block", threat_type: "prompt_injection", latency_ms: 3.1 },
  ];
  const nodes = buildHonestTraceStages(realStages, "block", { promptSnippet: "hi" });
  const byId = Object.fromEntries(nodes.map((n) => [n.id, n]));
  // honest per-stage: three DIFFERENT badges, not one repeated
  assert.equal(byId.policy.badge, "redacted");
  assert.equal(byId.policy.highlightAction, "redact");
  assert.equal(byId.input_scan.badge, "allowed");
  assert.equal(byId.input_scan.highlight, false); // allow is not highlighted
  assert.equal(byId.output_guard.badge, "blocked");
  assert.equal(byId.output_guard.highlightAction, "block");
  // attribution surfaced as tags (rule/policy names, not raw PII)
  assert.ok(byId.policy.tags.some((t) => t.includes("PII Detection & Redaction")));
  // latency metric present
  assert.ok(byId.policy.metrics && byId.policy.metrics[0].value.includes("ms"));
  // final node reflects the parent final_action
  assert.equal(nodes[nodes.length - 1].id, "final_output");
  assert.equal(nodes[nodes.length - 1].badge, "blocked");
});

test("buildHonestTraceStages applies the redact->flag honesty rule end to end", () => {
  const nodes = buildHonestTraceStages(
    [{ stage: "policy", action: "redact", metadata: { redact_noop: true } }],
    "flag",
    {},
  );
  const policy = nodes.find((n) => n.id === "policy");
  assert.equal(policy.badge, "flagged");
  assert.equal(policy.highlightAction, "flag");
});

test("extractRealStages finds the trace under the zeroshield envelope", () => {
  const event = { zeroshield: { pipeline_trace: { stages: [{ stage: "policy", action: "allow" }], final_action: "allow" } } };
  assert.equal(extractRealStages(event).length, 1);
  assert.equal(extractFinalAction(event, "x"), "allow");
  // no trace -> empty, and final action falls back to event.action
  assert.deepEqual(extractRealStages({ action: "block" }), []);
  assert.equal(extractFinalAction({ action: "block" }, "x"), "block");
});

test("resolveTotalLatencyMs prefers pipeline_trace.total_latency_ms over meta.latency_ms", () => {
  const ms = resolveTotalLatencyMs({
    pipelineTrace: { total_latency_ms: 13555, stage_latency_sum_ms: 120, overhead_ms: 13435 },
    meta: { latency_ms: 0 },
  });
  assert.equal(ms, 13555);
});

test("resolveTotalLatencyMs falls back to stage_latency_sum_ms + overhead_ms", () => {
  const ms = resolveTotalLatencyMs({
    pipelineTrace: { stage_latency_sum_ms: 50.2, overhead_ms: 12.3 },
    meta: { latency_ms: 0 },
  });
  assert.equal(ms, 62.5);
});

test("resolveTotalLatencyMs uses meta.latency_ms when trace absent", () => {
  assert.equal(resolveTotalLatencyMs({ meta: { latency_ms: 420 } }), 420);
});

test("formatPipelineDurationMs renders rounded ms", () => {
  assert.equal(formatPipelineDurationMs(13555.44), "13555.4ms");
  assert.equal(formatPipelineDurationMs(null), "--");
});

test("parsePipelineDurationMs reads leading ms from Duration labels", () => {
  assert.equal(parsePipelineDurationMs("13607.1ms"), 13607.1);
  assert.equal(
    parsePipelineDurationMs("13607.1ms (stages 120ms + overhead 13487ms)"),
    13607.1,
  );
  assert.equal(parsePipelineDurationMs("--"), null);
});

test("latencyMsWithinTolerance allows 0.1ms rounding delta (PIPELINE-0018)", () => {
  assert.equal(LATENCY_MS_PARITY_TOLERANCE, 0.1);
  assert.equal(latencyMsWithinTolerance(13607.14, 13607.1), true);
  assert.equal(latencyMsWithinTolerance(13607.14, 13607.0), false);
  assert.equal(
    latencyMsWithinTolerance(
      resolveTotalLatencyMs({
        pipelineTrace: { total_latency_ms: 14860.94, stage_latency_sum_ms: 100, overhead_ms: 10 },
        meta: { latency_ms: 0 },
      }),
      parsePipelineDurationMs(formatPipelineDurationMs(14860.94)),
    ),
    true,
  );
});

test("resolveTtftMs reads ttft from pipeline_trace then zeroshield", () => {
  assert.equal(resolveTtftMs({ pipelineTrace: { ttft_ms: 88.2 } }), 88.2);
  assert.equal(resolveTtftMs({ zeroshield: { ttft_ms: 41 } }), 41);
  assert.equal(resolveTtftMs({ meta: { ttft_ms: 9 } }), 9);
});

test("resolveLatencyHints uses backend latency_breakdown when present", () => {
  const hints = resolveLatencyHints({
    pipelineTrace: {
      total_latency_ms: 8000,
      latency_breakdown: {
        hints: [{
          stage: "model_output",
          severity: "high",
          message: "Model Output took 7710.0ms (96% of total).",
          actions: ["Switch to a smaller or faster model for this workload."],
        }],
      },
    },
  });
  assert.equal(hints.length, 1);
  assert.equal(hints[0].stage, "model_output");
  assert.ok(hints[0].actions[0].includes("faster model"));
});

test("resolveLatencyHints falls back to stage latencies for legacy traces", () => {
  const hints = resolveLatencyHints({
    pipelineTrace: {
      total_latency_ms: 1000,
      stages: [
        { name: "input_scan", latency_ms: 600 },
        { name: "model_output", latency_ms: 300 },
      ],
    },
  });
  assert.equal(hints.length, 1);
  assert.equal(hints[0].stage, "input_scan");
});

test("resolveLatencyBreakdown includes by_stage and dominant fields", () => {
  const bd = resolveLatencyBreakdown({
    pipelineTrace: {
      total_latency_ms: 7710,
      stage_latency_sum_ms: 7700,
      overhead_ms: 10,
      latency_breakdown: {
        dominant_stage: "model_output",
        dominant_latency_ms: 7710,
        dominant_share_pct: 99.9,
        by_stage: [{ stage: "model_output", latency_ms: 7710, share_pct: 99.9 }],
        hints: [{ stage: "model_output", severity: "high", message: "x", actions: ["y"] }],
      },
    },
  });
  assert.equal(bd.dominant_stage, "model_output");
  assert.equal(bd.hints.length, 1);
  assert.equal(bd.by_stage.length, 1);
});

test("resolveLatencyBreakdown works for legacy traces without stage_latency_sum_ms", () => {
  const bd = resolveLatencyBreakdown({
    pipelineTrace: {
      total_latency_ms: 14860.9,
      stages: [
        { name: "model_output", latency_ms: 12128 },
        { name: "input_scan", latency_ms: 1200 },
      ],
    },
  });
  assert.ok(bd);
  assert.equal(bd.hints.length, 1);
  assert.equal(bd.hints[0].stage, "model_output");
  assert.ok(bd.by_stage.length >= 2);
});
