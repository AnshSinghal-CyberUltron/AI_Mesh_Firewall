import test from "node:test";
import assert from "node:assert/strict";
import {
  consumeSSEStream,
  extractRoutingFromHeaders,
  inferTerminalBlockedStage,
  normalizeChatPipelineResult,
  pinnedModelRoutingPreferences,
  simulatorRoutingPreferences,
  attackSimulatorRoutingPreferences,
  chatCompletionBody,
} from "./liveGateway.js";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const encoder = new TextEncoder();

/** Build a minimal fetch-Response-like object streaming the given chunks. */
function sseResponse(chunks, contentType = "text/event-stream") {
  let i = 0;
  return {
    headers: new Headers({ "content-type": contentType }),
    body: {
      getReader: () => ({
        read: async () => {
          if (i < chunks.length) {
            const chunk = chunks[i++];
            const value = typeof chunk === "string" ? encoder.encode(chunk) : chunk;
            return { done: false, value };
          }
          return { done: true, value: undefined };
        },
      }),
    },
  };
}

const deltaEvent = (text) =>
  `data: ${JSON.stringify({ choices: [{ delta: { content: text } }] })}`;

// ---------------------------------------------------------------------------
// consumeSSEStream — processPart via complete \n\n-terminated events
// ---------------------------------------------------------------------------

test("parses \\n\\n-terminated data events and [DONE]", async () => {
  const result = await consumeSSEStream(
    sseResponse([`${deltaEvent("Hello")}\n\n${deltaEvent(" world")}\n\ndata: [DONE]\n\n`]),
  );
  assert.equal(result.isStream, true);
  assert.equal(result.aggregatedContent, "Hello world");
  assert.deepEqual(
    result.events.map((e) => e.type),
    ["data", "data", "done"],
  );
  assert.equal(result.terminalError, null);
});

test("buffers an event split across reader chunks", async () => {
  const full = `${deltaEvent("split")}\n\ndata: [DONE]\n\n`;
  const mid = Math.floor(full.length / 2);
  const result = await consumeSSEStream(sseResponse([full.slice(0, mid), full.slice(mid)]));
  assert.equal(result.aggregatedContent, "split");
  assert.deepEqual(result.events.map((e) => e.type), ["data", "done"]);
});

test("non-JSON data payload becomes a raw event, not a crash", async () => {
  const result = await consumeSSEStream(sseResponse(["data: not-json\n\n"]));
  assert.deepEqual(result.events, [{ type: "raw", payload: "not-json" }]);
  assert.equal(result.aggregatedContent, "");
});

test("non-data lines (comments / other fields) are ignored", async () => {
  const result = await consumeSSEStream(
    sseResponse([`: keep-alive\n\nevent: ping\n\n${deltaEvent("ok")}\n\n`]),
  );
  assert.equal(result.aggregatedContent, "ok");
  assert.equal(result.events.length, 1);
});

// ---------------------------------------------------------------------------
// consumeSSEStream — final-buffer flush (M-30): the last SSE event without a
// trailing \n\n must still be delivered.
// ---------------------------------------------------------------------------

test("final delta without trailing \\n\\n is flushed", async () => {
  const result = await consumeSSEStream(
    sseResponse([`${deltaEvent("first")}\n\n`, deltaEvent(" last")]),
  );
  assert.equal(result.aggregatedContent, "first last");
  assert.equal(result.events.length, 2);
});

test("terminal error frame without trailing \\n\\n survives", async () => {
  const errFrame = `data: ${JSON.stringify({ error: { type: "output_blocked", message: "blocked" } })}`;
  const result = await consumeSSEStream(
    sseResponse([`${deltaEvent("partial")}\n\n`, errFrame]),
  );
  assert.equal(result.aggregatedContent, "partial");
  assert.deepEqual(result.terminalError, { type: "output_blocked", message: "blocked" });
});

test("[DONE] without trailing \\n\\n is flushed as a done event", async () => {
  const result = await consumeSSEStream(
    sseResponse([`${deltaEvent("x")}\n\ndata: [DONE]`]),
  );
  assert.equal(result.aggregatedContent, "x");
  assert.deepEqual(result.events.at(-1), { type: "done" });
});

test("multi-byte character split across chunks is decoded by the flush drain", async () => {
  // "é" (U+00E9) is two UTF-8 bytes; split them across reads in the FINAL
  // unterminated event so the trailing decoder.decode() drain must finish it.
  const bytes = encoder.encode(deltaEvent("café"));
  const result = await consumeSSEStream(
    sseResponse([bytes.slice(0, bytes.length - 1), bytes.slice(bytes.length - 1)]),
  );
  assert.equal(result.aggregatedContent, "café");
});

test("missing body reader yields an empty stream result", async () => {
  const result = await consumeSSEStream({
    headers: new Headers({ "content-type": "text/event-stream" }),
    body: null,
  });
  assert.deepEqual(result, {
    isStream: true,
    events: [],
    aggregatedContent: "",
    terminalError: null,
    raw: "",
  });
});

test("non-SSE content type takes the JSON (isStream:false) path", async () => {
  const payload = { error: { code: "content_blocked" } };
  const result = await consumeSSEStream({
    headers: new Headers({ "content-type": "application/json" }),
    text: async () => JSON.stringify(payload),
  });
  assert.equal(result.isStream, false);
  assert.deepEqual(result.data, payload);
  assert.deepEqual(result.terminalError, payload.error);
});

// ---------------------------------------------------------------------------
// getHeaderValue (M-31) via extractRoutingFromHeaders — case-insensitive reads
// on both real Headers instances and plain-object fallbacks.
// ---------------------------------------------------------------------------

const EXPECTED_ROUTING = {
  requested_model: "gpt-4o",
  original_model: "gpt-4o",
  selected_model: "zeroshield-guard-120b",
  routed_model: "zeroshield-guard-120b",
  routing_reason: "kill_switch reroute",
  decision_source: "kill_switch",
  policy_summary: "policy: reroute high-risk",
  rerouted: true,
};

test("extractRoutingFromHeaders reads a real Headers instance (any casing)", () => {
  const headers = new Headers({
    // Headers normalizes names to lowercase internally; .get() is CI.
    "X-ZEROSHIELD-ORIGINAL-MODEL": "gpt-4o",
    "x-zeroshield-routed-model": "zeroshield-guard-120b",
    "X-ZeroShield-Routing-Reason": "kill_switch reroute",
    "X-ZeroShield-Routing-Source": "kill_switch",
    "X-ZeroShield-Routing-Policy-Summary": "policy: reroute high-risk",
    "X-ZeroShield-Rerouted": "true",
  });
  assert.deepEqual(extractRoutingFromHeaders(headers), EXPECTED_ROUTING);
});

test("extractRoutingFromHeaders reads a plain object with exact-case keys", () => {
  const headers = {
    "X-ZeroShield-Original-Model": "gpt-4o",
    "X-ZeroShield-Routed-Model": "zeroshield-guard-120b",
    "X-ZeroShield-Routing-Reason": "kill_switch reroute",
    "X-ZeroShield-Routing-Source": "kill_switch",
    "X-ZeroShield-Routing-Policy-Summary": "policy: reroute high-risk",
    "X-ZeroShield-Rerouted": "true",
  };
  assert.deepEqual(extractRoutingFromHeaders(headers), EXPECTED_ROUTING);
});

test("extractRoutingFromHeaders reads a plain object with lowercase keys", () => {
  const headers = {
    "x-zeroshield-original-model": "gpt-4o",
    "x-zeroshield-routed-model": "zeroshield-guard-120b",
    "x-zeroshield-routing-reason": "kill_switch reroute",
    "x-zeroshield-routing-source": "kill_switch",
    "x-zeroshield-routing-policy-summary": "policy: reroute high-risk",
    "x-zeroshield-rerouted": "true",
  };
  assert.deepEqual(extractRoutingFromHeaders(headers), EXPECTED_ROUTING);
});

test("extractRoutingFromHeaders falls back to a CI key scan for odd casings", () => {
  const headers = {
    "X-ZEROSHIELD-Original-MODEL": "gpt-4o",
    "x-ZeroShield-routed-MODEL": "zeroshield-guard-120b",
    "X-zeroshield-Routing-reason": "kill_switch reroute",
    "X-ZEROSHIELD-ROUTING-SOURCE": "kill_switch",
    "x-zeroShield-routing-policy-summary": "policy: reroute high-risk",
    "X-ZeroShield-REROUTED": "true",
  };
  assert.deepEqual(extractRoutingFromHeaders(headers), EXPECTED_ROUTING);
});

test("extractRoutingFromHeaders handles null/missing headers gracefully", () => {
  assert.deepEqual(extractRoutingFromHeaders(null), {});
  const sparse = extractRoutingFromHeaders({});
  assert.equal(sparse.selected_model, "");
  assert.equal(sparse.rerouted, false);
});

test("output_guardrail latency uses output_guardrail_ms from stage metrics", () => {
  const result = normalizeChatPipelineResult(
    {
      stage_metrics_ms: { output_guardrail_ms: 42.3 },
      choices: [{ message: { content: "hello" } }],
    },
    200,
    {},
  );
  const og = result.stages.find((s) => s.name === "output_guardrail");
  assert.ok(og);
  assert.equal(og.latency_ms, 42.3);
});

test("500 after output guard ran does not show Output guard not evaluated", () => {
  const result = normalizeChatPipelineResult(
    { stage_metrics_ms: { output_guardrail_ms: 12.5 } },
    500,
    {},
  );
  const og = result.stages.find((s) => s.name === "output_guardrail");
  assert.ok(og);
  assert.equal(og.action, "error");
  assert.match(og.detail, /after output guard ran/i);
  assert.notEqual(og.detail, "Output guard not evaluated");
});

test("simulatorRoutingPreferences honours org routing_enabled (PIPELINE-0030)", () => {
  assert.deepEqual(
    simulatorRoutingPreferences("nvidia/nemotron-3-super-120b-a12b:free", { orgRoutingEnabled: true }),
    { enable_routing: true, preferred_model: "nvidia/nemotron-3-super-120b-a12b:free" },
  );
  assert.deepEqual(
    simulatorRoutingPreferences("nvidia/nemotron-3-super-120b-a12b:free", { orgRoutingEnabled: false }),
    pinnedModelRoutingPreferences("nvidia/nemotron-3-super-120b-a12b:free"),
  );
  assert.equal(simulatorRoutingPreferences("auto", { orgRoutingEnabled: true }), null);
});

test("attackSimulatorRoutingPreferences omits enable_routing until firewall config is ready", () => {
  assert.equal(
    attackSimulatorRoutingPreferences("gpt-4o-mini", { orgRoutingEnabled: true, configReady: false }),
    null,
  );
  assert.deepEqual(
    attackSimulatorRoutingPreferences("gpt-4o-mini", { orgRoutingEnabled: true, configReady: true }),
    { enable_routing: true, preferred_model: "gpt-4o-mini" },
  );
  assert.deepEqual(
    attackSimulatorRoutingPreferences("gpt-4o-mini", { orgRoutingEnabled: false, configReady: true }),
    pinnedModelRoutingPreferences("gpt-4o-mini"),
  );
});

test("upstream 503 after PII scan ALLOW maps to model_output, not input_scan", () => {
  const zs = {
    action: "error",
    threat_type: "pii",
    detection_tier: "tier_1",
    confidence: 0.85,
    guard_action: "allow",
    matched_patterns: ["email_smart_masked"],
  };
  const data = {
    code: 503,
    final_action: "error",
    zeroshield: zs,
    pipeline_trace: {
      stages: [
        { name: "input_scan", action: "allow", threat_type: "pii", tier: "tier_1" },
        { name: "model_output", action: "error" },
      ],
      total_latency_ms: 1272.3,
    },
  };
  assert.equal(inferTerminalBlockedStage(data, 503, zs, "error"), "model_output");
  const result = normalizeChatPipelineResult(data, 503, {});
  assert.equal(result.final_action, "error");
  assert.equal(result.blocked_by, "model_output");
  assert.notEqual(result.detection_checkpoint, "input_scan");
});

test("isolation_target_uncallable maps to kill_switch, not model_output", () => {
  assert.equal(
    inferTerminalBlockedStage(
      { code: "isolation_target_uncallable", blocked_by: "kill_switch" },
      503,
      {},
      "error",
    ),
    "kill_switch",
  );
});

test("circuit_breaker_open maps to circuit_breaker, not a generic model_output error", () => {
  assert.equal(
    inferTerminalBlockedStage({ code: "circuit_breaker_open" }, 503, {}, "error"),
    "circuit_breaker",
  );
});

test("model_isolated maps to kill_switch stage", () => {
  assert.equal(
    inferTerminalBlockedStage({ code: "model_isolated", blocked_by: "kill_switch" }, 503, {}, "error"),
    "kill_switch",
  );
});

test("scan-only chat body sends max_tokens=0, inference omits the sentinel", () => {
  const scan = chatCompletionBody({ prompt: "hi", model: "gpt-4o-mini", runInference: false });
  assert.equal(scan.max_tokens, 0);
  const infer = chatCompletionBody({ prompt: "hi", model: "gpt-4o-mini", runInference: true, maxTokens: 512 });
  assert.equal(infer.max_tokens, 512);
});

test("503 without pipeline_trace does not badge input_scan ALLOW / no threats", () => {
  const result = normalizeChatPipelineResult(
    { error: { message: "The inference provider is temporarily unavailable.", type: "upstream_error", code: 503 } },
    503,
    {},
  );
  assert.equal(result.final_action, "error");
  const input = result.stages.find((s) => s.name === "input_scan");
  assert.ok(input);
  assert.equal(input.action, "skip");
  assert.match(input.detail, /omitted from error response/i);
});

test("503 with input_scan redact trace keeps REDACT and overall ERROR", () => {
  const stages = [
    "auth", "rate_limit", "policy", "input_scan", "kill_switch",
    "model_routing", "model_input", "model_output", "output_guardrail",
  ].map((name) => ({
    name,
    action: name === "input_scan" ? "redact" : name === "model_output" ? "error" : name === "output_guardrail" ? "skip" : "allow",
    latency_ms: 1,
    detail: name === "input_scan" ? "PII detected in prompt: ssn, email" : "",
    threat_type: name === "input_scan" ? "pii" : "",
    tier: name === "input_scan" ? "tier_1" : "",
  }));
  const result = normalizeChatPipelineResult(
    {
      final_action: "error",
      pipeline_trace: { stages, total_latency_ms: 46 },
      zeroshield: { action: "error", threat_type: "pii", detection_tier: "tier_1", confidence: 0.85 },
    },
    503,
    {},
  );
  assert.equal(result.final_action, "error");
  const input = result.stages.find((s) => s.name === "input_scan");
  assert.equal(input.action, "redact");
  assert.equal(input.threat_type, "pii");
  const output = result.stages.find((s) => s.name === "model_output");
  assert.equal(output.action, "error");
});
