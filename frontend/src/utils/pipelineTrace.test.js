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
  resolveRoutingDecision,
  resolvePipelineInputOutput,
  deriveStageVerdict,
  derivePipelineDetections,
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

test("honestStageAction keeps allow when scan_outcome is analyzed", () => {
  assert.equal(
    honestStageAction({ action: "allow", scan_outcome: "analyzed", redact_noop: true, latency_ms: 4.5 }),
    "allow",
  );
  assert.equal(
    honestStageAction({ action: "redact", scan_outcome: "analyzed", redact_noop: true }),
    "allow",
  );
});

test("honestStageAction renders 0ms allow as skip, not allow", () => {
  assert.equal(honestStageAction({ action: "allow", latency_ms: 0 }), "skip");
  assert.equal(honestStageAction({ action: "allow", latency_ms: 1.2 }), "allow");
  assert.equal(honestStageAction({ action: "block", latency_ms: 0 }), "block");
  assert.equal(honestStageAction({ action: "skip", latency_ms: 0 }), "skip");
});

test("honestStageAction keeps model_routing allow even at 0ms", () => {
  assert.equal(
    honestStageAction({ name: "model_routing", action: "allow", latency_ms: 0 }),
    "allow",
  );
  assert.equal(
    honestStageAction({ name: "route", action: "allow", latency_ms: 0 }),
    "allow",
  );
});

test("honestStageAction flags unenforced injection BLOCK rec (scan 251434)", () => {
  assert.equal(
    honestStageAction({
      name: "input_scan",
      action: "allow",
      latency_ms: 1649.2,
      threat_type: "prompt_injection",
      recommended_action: "block",
    }),
    "flag",
  );
});

test("honestStageAction keeps PII allow when tier-2 recommended block", () => {
  assert.equal(
    honestStageAction({
      name: "input_scan",
      action: "allow",
      latency_ms: 12,
      threat_type: "pii",
      recommended_action: "block",
      scan_outcome: "analyzed",
    }),
    "allow",
  );
});

test("deriveStageVerdict scores 251434-style allow+injection rec as flag", () => {
  const v = deriveStageVerdict([
    { name: "auth", action: "allow", latency_ms: 0.8 },
    {
      name: "input_scan",
      action: "allow",
      latency_ms: 1649.2,
      threat_type: "prompt_injection",
      recommended_action: "block",
    },
    { name: "model_output", action: "allow", latency_ms: 4388.5 },
  ]);
  assert.equal(v.action, "flag");
  assert.equal(v.score, 60);
});

test("derivePipelineDetections reads injection from stage when telemetry is null", () => {
  const d = derivePipelineDetections(
    [{ name: "input_scan", action: "allow", threat_type: "prompt_injection", recommended_action: "block" }],
    { prompt_injection_detected: false, jailbreak_detected: false, pii_detected: false },
  );
  assert.equal(d.promptInjection, true);
  assert.equal(d.jailbreak, false);
});

test("buildHonestTraceStages badges a 0ms allow stage as skipped", () => {
  const nodes = buildHonestTraceStages(
    [
      { stage: "kill_switch", action: "allow", latency_ms: 0 },
      { stage: "input_scan", action: "allow", latency_ms: 4.5 },
    ],
    "allow",
    {},
  );
  const byId = Object.fromEntries(nodes.map((n) => [n.id, n]));
  assert.equal(byId.kill_switch.badge, "skipped");
  assert.equal(byId.input_scan.badge, "allowed");
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

test("resolveRoutingDecision merges trace root and model_routing stage (PIPELINE-0021)", () => {
  const routing = resolveRoutingDecision({
    pipelineTrace: {
      requested_model: "auto",
      routed_model: "Haiku",
      routing: {
        route_destination: "llm",
        routing_reason: "Adjudicator pick",
        decision_source: "policy_adjudicator",
        weights: { latency: 0.4 },
        org_routing_enabled: true,
      },
      stages: [
        {
          name: "model_routing",
          action: "allow",
          decision_factors: ["compliance=GDPR"],
          routing_score: 0.9,
          candidate_count: 2,
        },
      ],
    },
  });
  assert.ok(routing);
  assert.equal(routing.requested_model, "auto");
  assert.equal(routing.routed_model, "Haiku");
  assert.equal(routing.route_destination, "llm");
  assert.equal(routing.route_destination_label, "LLM inference");
  assert.deepEqual(routing.decision_factors, ["compliance=GDPR"]);
  assert.deepEqual(routing.weights, { latency: 0.4 });
  assert.equal(routing.routing_score, 0.9);
  assert.equal(routing.candidate_count, 2);
  assert.equal(routing.org_routing_enabled, true);
});

test("resolvePipelineInputOutput blocked shows withheld output (PIPELINE-0022)", () => {
  const io = resolvePipelineInputOutput({
    pipelineTrace: {
      final_action: "block",
      input_text: "my ssn ***-**-6789",
      prompt_submitted: "my ssn ***-**-6789",
      output_text: "",
      output_withheld: true,
      output_withheld_reason: "Response withheld — request blocked at input scan",
    },
  });
  assert.equal(io.inputText, "my ssn ***-**-6789");
  assert.equal(io.outputWithheld, true);
  assert.match(io.outputText, /withheld/i);
  assert.equal(io.inputWasRedacted, false);
});

test("resolvePipelineInputOutput withheld output guard shows operator preview (PIPELINE-0030)", () => {
  const io = resolvePipelineInputOutput({
    meta: { raw_output: "Could you clarify what processing you need?" },
    pipelineTrace: {
      final_action: "block",
      input_text: "user record",
      output_text: "Could you clarify what processing you need?",
      output_withheld: true,
      output_withheld_reason: "Response withheld — output guard blocked delivery to client",
    },
  });
  assert.equal(io.outputWithheld, true);
  assert.match(io.outputText, /withheld/i);
  assert.equal(io.outputWithheldPreview, "Could you clarify what processing you need?");
});

test("resolvePipelineInputOutput redact shows before/after (PIPELINE-0022)", () => {
  const io = resolvePipelineInputOutput({
    pipelineTrace: {
      final_action: "redact",
      input_text: "email user@example.com",
      prompt_submitted: "email u***@example.com",
      input_was_redacted: true,
      input_text_before: "email user@example.com",
      input_text_after: "email u***@example.com",
      output_text: "ok",
      output_withheld: false,
    },
  });
  assert.equal(io.inputWasRedacted, true);
  assert.equal(io.inputBefore, "email user@example.com");
  assert.equal(io.inputAfter, "email u***@example.com");
  assert.equal(io.outputText, "ok");
  assert.equal(io.outputWithheld, false);
});
