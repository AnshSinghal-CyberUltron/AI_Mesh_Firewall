/**
 * Helpers for Module 1 UI panels that exercise the live gateway (no /v1/simulator/*).
 */

import {
  formatDecisionSource,
  formatDetectionTier,
  formatModelDisplayName,
  formatRoutingReason,
  isRoutingReroute,
} from "../constants/zeroshieldBrand.js";
import { resolveTotalLatencyMs, resolveTtftMs, formatRouteDestination } from "./pipelineTrace.js";

export function chatCompletionBody({
  prompt,
  model = "auto",
  runInference = true,
  routingPreferences = null,
  maxTokens = 512,
  stream = false,
}) {
  const body = {
    model,
    messages: [{ role: "user", content: prompt }],
    max_tokens: maxTokens,
  };
  if (stream) {
    body.stream = true;
  }
  if (!runInference) {
    // "scan-only / no-inference" probe: OMIT max_tokens entirely (absent →
    // gateway treats as no-inference). Previously this sent max_tokens=0, which
    // the gateway's request validation rejects as "'max_tokens' must be a
    // positive integer" (400) — so every probe surfaced a confusing block in the
    // pipeline trace instead of the input-scan verdict.
    delete body.max_tokens;
  }
  if (routingPreferences && typeof routingPreferences === "object") {
    body.routing_preferences = routingPreferences;
  }
  return body;
}

/** Pin the selected model: disable org routing for simulator / SDK callers. */
export function pinnedModelRoutingPreferences(model) {
  const name = String(model || "").trim();
  if (!name || name.toLowerCase() === "auto") {
    return null;
  }
  return { enable_routing: false, preferred_model: name };
}

/**
 * Attack / isolation simulators: honour org routing_enabled from firewall config.
 * When org routing is on, send enable_routing:true (model is a preference hint).
 * When off, pin the selected model (SDK parity).
 */
export function simulatorRoutingPreferences(model, { orgRoutingEnabled = true } = {}) {
  const name = String(model || "").trim();
  if (!name || name.toLowerCase() === "auto") {
    return null;
  }
  if (orgRoutingEnabled) {
    return { enable_routing: true, preferred_model: name };
  }
  return pinnedModelRoutingPreferences(name);
}

/**
 * Parse an OpenAI-compatible SSE response body into discrete events.
 * Reason: output-guard and stream governance paths only emit on text/event-stream;
 * the simulator must consume chunks the same way production clients do.
 */
export async function consumeSSEStream(response) {
  const contentType = response.headers.get("content-type") || "";
  if (!contentType.includes("text/event-stream")) {
    const text = await response.text();
    let data = null;
    try {
      data = text ? JSON.parse(text) : null;
    } catch {
      data = { raw: text };
    }
    return {
      isStream: false,
      events: [],
      aggregatedContent: "",
      terminalError: data?.error || data,
      raw: text,
      data,
    };
  }

  const reader = response.body?.getReader();
  if (!reader) {
    return { isStream: true, events: [], aggregatedContent: "", terminalError: null, raw: "" };
  }

  const decoder = new TextDecoder();
  let buffer = "";
  const events = [];
  let aggregatedContent = "";
  let terminalError = null;
  let terminalTracePayload = null;

  // Parse a single SSE "event block" (already split on the \n\n boundary).
  // Returns nothing; mutates events/aggregatedContent/terminalError in place.
  const processPart = (part) => {
    const line = part.trim();
    if (!line.startsWith("data: ")) return;
    const payload = line.slice(6).trim();
    if (payload === "[DONE]") {
      events.push({ type: "done" });
      return;
    }
    let parsed;
    try {
      parsed = JSON.parse(payload);
    } catch {
      events.push({ type: "raw", payload });
      return;
    }
    events.push({ type: "data", payload: parsed });
    if (parsed?.error) {
      terminalError = parsed.error;
    }
    // M-51 terminal trace frame: empty choices + zeroshield/pipeline_trace.
    if (parsed?.pipeline_trace || parsed?.zeroshield) {
      terminalTracePayload = parsed;
    }
    const delta = parsed?.choices?.[0]?.delta?.content;
    if (typeof delta === "string") {
      aggregatedContent += delta;
    } else {
      const reasoning = parsed?.choices?.[0]?.delta?.reasoning_content;
      if (typeof reasoning === "string" && reasoning) {
        aggregatedContent += reasoning;
      }
    }
  };

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const parts = buffer.split("\n\n");
    buffer = parts.pop() || "";
    for (const part of parts) {
      processPart(part);
    }
  }

  // Flush any trailing buffer: if the stream ends without a final \n\n
  // (common when the gateway closes after the last delta or an error frame),
  // the last event would otherwise be silently dropped. Drain the decoder
  // and process whatever remains so terminal errors / final deltas survive.
  buffer += decoder.decode();
  if (buffer.trim()) {
    processPart(buffer);
  }

  return {
    isStream: true,
    events,
    aggregatedContent,
    terminalError,
    terminalTracePayload,
    raw: "",
  };
}

/** Map streamed chat completion into Attack Simulator pipeline shape. */
export function normalizeStreamChatPipelineResult(
  sseResult,
  httpStatus,
  responseHeaders,
  context = {},
) {
  const headers = responseHeaders || {};
  // Read case-insensitively: real fetch Headers.get() is already CI, but a
  // plain-object fallback (tests / proxies) is not — so try both casings.
  const readHeader = (key) => getHeaderValue(headers, key);
  const scanMode = readHeader("x-zeroshield-stream-scan-mode");
  // Always thread responseHeaders into the downstream context so the
  // X-ZeroShield-* routing telemetry survives BOTH the success and the 4xx
  // error path (extractRoutingFromHeaders reads context.responseHeaders).
  // Prefer the explicit arg; fall back to whatever the caller already set.
  const ctx = { ...context, responseHeaders: responseHeaders || context.responseHeaders };
  const terminalErr = sseResult?.terminalError;
  const blockedInStream = Boolean(
    terminalErr
    && (terminalErr.type === "output_blocked" || terminalErr.code === "output_blocked"),
  );

  if (httpStatus === 403 || (httpStatus >= 400 && !sseResult?.isStream)) {
    return normalizeChatPipelineResult(sseResult?.data || {}, httpStatus, ctx);
  }

  const terminal = sseResult?.terminalTracePayload;
  const terminalZs = terminal?.zeroshield || {};
  const synthetic = {
    choices: sseResult?.aggregatedContent
      ? [{ message: { content: sseResult.aggregatedContent } }]
      : [],
    pipeline_trace: terminal?.pipeline_trace,
    zeroshield: {
      ...terminalZs,
      action: blockedInStream ? "block" : terminalErr ? "error" : (terminalZs.action || "allow"),
      threat_type: blockedInStream ? (terminalErr?.type || "output_blocked") : (terminalZs.threat_type || ""),
      stream: true,
      stream_scan_mode: scanMode,
    },
    error: terminalErr || undefined,
  };

  const normalized = normalizeChatPipelineResult(synthetic, blockedInStream ? 403 : httpStatus, ctx);
  const ttftMs = resolveTtftMs({
    pipelineTrace: terminal?.pipeline_trace,
    zeroshield: synthetic.zeroshield,
    meta: synthetic.zeroshield,
  });
  return {
    ...normalized,
    stream: true,
    stream_events: sseResult?.events?.length || 0,
    stream_scan_mode: scanMode,
    aggregated_content: sseResult?.aggregatedContent || "",
    ttft_ms: ttftMs ?? normalized.ttft_ms,
    final_action: blockedInStream
      ? "block"
      : terminalErr
        ? "error"
        : normalized.final_action,
  };
}

/** Stage order aligned with gateway/simulator_routes.py pipeline visualization. */
const PIPELINE_STAGE_ORDER = [
  "auth",
  "rate_limit",
  "policy",
  "input_scan",
  "kill_switch",
  "model_routing",
  "model_input",
  "model_output",
  "output_guardrail",
];

export function estimateRequestTokens(prompt, maxTokens = 512) {
  const promptTokens = Math.max(1, Math.floor(String(prompt || "").length / 4));
  const completionBudget = Math.max(0, Number(maxTokens) || 0);
  return Math.max(1, promptTokens + completionBudget);
}

function extractErrorPayload(data) {
  const nested = data?.error;
  if (nested && typeof nested === "object") {
    return {
      message: nested.message || data.message || "",
      type: String(nested.type || ""),
      code: String(nested.code ?? data.code ?? ""),
    };
  }
  return {
    message: data?.message || (typeof nested === "string" ? nested : ""),
    type: "",
    code: String(data?.code ?? ""),
  };
}

function isUpstreamProviderError(data, httpStatus) {
  const err = extractErrorPayload(data);
  const blob = `${err.type} ${err.message}`.toLowerCase();
  if (httpStatus === 502) return true;
  return (
    blob.includes("missing credentials")
    || blob.includes("litellm")
    || blob.includes("internalservererror")
    || blob.includes("upstream llm")
  );
}

function isUpstreamProviderRateLimit(data, httpStatus) {
  const err = extractErrorPayload(data);
  const blob = `${err.type} ${err.message}`.toLowerCase();
  if (data?.rate_limit?.scope === "gateway_tpm") return false;
  if (
    data?.code === "rate_limit_exceeded"
    || data?.code === "burst_limit_exceeded"
    || data?.code === "global_rate_limit_exceeded"
    || data?.code === "org_rate_limit_exceeded"
  ) {
    return false;
  }
  return (
    httpStatus === 429
    && (
      blob.includes("litellm")
      || blob.includes("openai")
      || blob.includes("quota")
      || blob.includes("anthropic")
      || err.type.toLowerCase().includes("ratelimit")
    )
  );
}

const INFERENCE_SETUP_CODES = new Set([
  "no_provider_configured",
  "model_not_configured",
  "guard_model_not_for_inference",
  "bedrock_model_not_configured",
]);

// Codes the gateway returns when a firewall POLICY/CONTENT decision (not a system
// fault) stops the request. OpenAI-SDK-compat maps a policy block to HTTP 400 with
// code "content_filter"/"content_blocked" (see C1 error-envelope work), so a naive
// `httpStatus >= 400 -> error` would mislabel a real BLOCK as a system ERROR.
const POLICY_BLOCK_CODES = new Set(["content_blocked", "content_filter", "blocked"]);

/**
 * True when a 4xx body is a deliberate firewall content/policy block rather than a
 * malformed-request / system error. Gated on the gateway's own block markers
 * (code, nested error.code, blocked_by, policy-violation category) so a genuine
 * validation 400 (e.g. missing model param) still resolves to "error".
 */
function isContentPolicyBlock(data, httpStatus) {
  if (!data || httpStatus < 400 || httpStatus >= 500) return false;
  const err = extractErrorPayload(data);
  const code = String(data?.code || "").toLowerCase();
  const errCode = String(err.code || "").toLowerCase();
  const category = String(data?.category || "").toLowerCase();
  const blockedBy = String(data?.blocked_by || "").toLowerCase();
  return (
    POLICY_BLOCK_CODES.has(code)
    || POLICY_BLOCK_CODES.has(errCode)
    || (blockedBy && blockedBy !== "rate_limit")
    || category.includes("policy")
    || category.includes("violation")
  );
}

function inferFinalAction(data, httpStatus, zs) {
  if (data?.final_action) return data.final_action;
  const code = String(data?.code || "").toLowerCase();
  if (httpStatus === 422 && INFERENCE_SETUP_CODES.has(code)) return "needs_model";
  if (isUpstreamProviderError(data, httpStatus)) return "error";
  if (zs?.action) return zs.action;
  if (httpStatus === 403) return "block";
  if (httpStatus === 429) {
    return isUpstreamProviderRateLimit(data, httpStatus) ? "error" : "block";
  }
  // A firewall content/policy block surfaced as a 4xx (OpenAI content_filter) is a
  // BLOCK verdict, not a system error — honor it before the generic 4xx fallthrough.
  if (isContentPolicyBlock(data, httpStatus)) return "block";
  if (httpStatus >= 400) return "error";
  return "allow";
}

/**
 * Stage where the gateway actually stopped the request (403/429/block only).
 * Do NOT treat detection_tier tier_1/tier_2 as a block — scans can run and still allow.
 */
export function inferTerminalBlockedStage(data, httpStatus, zs, finalAction = "") {
  const resolvedFinal = finalAction || inferFinalAction(data, httpStatus, zs);
  if (resolvedFinal !== "block" && httpStatus < 400) {
    return "";
  }

  return inferBlockedStage(data, httpStatus, zs);
}

/** Last pipeline stage that ran input/output scanning (informational, not a block). */
export function inferDetectionCheckpoint(data, zs) {
  const tier = String(data?.detection_tier || zs?.detection_tier || "").toLowerCase();
  if (!tier || tier === "none" || tier === "output_guard" || tier === "output_guardrail") {
    return "";
  }
  if (tier.startsWith("tier") || tier.includes("scan") || tier.includes("guard")) {
    return "input_scan";
  }
  return "";
}

/** Map gateway response to the pipeline stage where processing stopped. */
function inferBlockedStage(data, httpStatus, zs) {
  const code = String(data?.code || "").toLowerCase();
  const category = String(data?.category || zs?.threat_type || "").toLowerCase();
  const tier = String(data?.detection_tier || data?.pipeline_stage || zs?.detection_tier || "").toLowerCase();

  if (isUpstreamProviderRateLimit(data, httpStatus)) return "model_output";

  if (
    code === "rate_limit_exceeded"
    || code === "burst_limit_exceeded"
    || code === "global_rate_limit_exceeded"
    || code === "org_rate_limit_exceeded"
    || data?.rate_limit?.scope === "gateway_tpm"
  ) {
    return "rate_limit";
  }
  if (code === "kill_switch_active") return "kill_switch";
  if (INFERENCE_SETUP_CODES.has(code) || data?.category === "inference_not_configured") {
    return "model_routing";
  }
  if (code === "model_not_allowed" || code === "model_not_configured") return "model_routing";
  if (code === "output_blocked" || category === "output_guard") return "output_guardrail";

  if (
    category === "prompt_injection"
    || category === "jailbreak"
    || category === "goal_hijacking"
    || category.includes("injection")
    || category === "pii"
    || category === "secret"
    || category === "toxicity"
    || category === "dos"
    || category === "tool_overreach"
    || category === "data_leakage"
  ) {
    return "input_scan";
  }

  if (data?.blocked_by) {
    const bb = String(data.blocked_by).toLowerCase();
    if (bb === "rate_limit" || bb === "rate_limit_tpm") return "rate_limit";
    if (bb === "firewall_keywords" || bb === "blocked_keyword") return "policy";
    if (bb === "input_scan" || bb.startsWith("tier")) return "input_scan";
    if (bb === "policy") return "policy";
    if (bb === "kill_switch") return "kill_switch";
    if (bb === "model_routing" || bb === "model_not_allowed") return "model_routing";
    if (bb === "model_output" || bb.includes("inference")) return "model_output";
    if (bb === "output_guardrail" || bb === "output_guard") return "output_guardrail";
  }

  if (
    category === "blocked_keyword"
    || tier === "config"
    || code === "threat_intel_blocked"
  ) {
    return "policy";
  }

  if (code === "content_blocked" && tier === "policy") return "policy";
  if (code === "content_blocked" && category === "blocked_keyword") return "policy";
  if (code === "content_blocked" && tier) return tier.startsWith("tier") ? "input_scan" : "policy";

  if (httpStatus === 403) return "input_scan";
  if (httpStatus === 429) return "rate_limit";
  if (httpStatus >= 500 || (httpStatus >= 400 && data?.error)) return "model_output";
  return "";
}

function formatRateLimitDetail(data, context = {}) {
  const rl = data?.rate_limit;
  if (rl?.limit_tpm != null) {
    return (
      `Gateway TPM ${rl.current_tpm ?? "?"}/${rl.limit_tpm} `
      + `(estimated ${rl.estimated_tokens ?? context.estimatedTokens ?? "?"} tokens this request)`
    );
  }
  const match = String(data?.message || "").match(
    /(\d+)\s*\/\s*(\d+)\s*TPM.*?(\d+)\s*tokens/i,
  );
  if (match) {
    return `Gateway TPM ${match[1]}/${match[2]} (estimated ${match[3]} tokens this request)`;
  }
  if (data?.message) return data.message;
  return "Token or request rate limit exceeded";
}

function formatPolicyBlockDetail(data, zs) {
  const category = data?.category || zs?.threat_type || "";
  if (category === "blocked_keyword" || data?.blocked_by === "firewall_keywords") {
    return (
      "Blocked by firewall keyword list (Firewall settings — not Policy Management rules). "
      + (data?.message || "Request blocked due to security policy")
    );
  }
  const matched = zs.matched_patterns || data?.matched_policies || [];
  if (matched.length) {
    return `Policy Management rule matched (${matched.length} rule${matched.length === 1 ? "" : "s"})`;
  }
  if (zs?.reason || zs?.detail) return zs.reason || zs.detail;
  if (data?.message) return data.message;
  return "Request blocked by Policy Management";
}

function formatInputScanDetail(data, zs, { blocked = false } = {}) {
  if (zs?.guard_reason) {
    return String(zs.guard_reason).split("\n")[0];
  }
  const tierLabel = formatDetectionTier(data?.detection_tier || zs?.detection_tier || "");
  const category = (data?.category || zs?.threat_type || "").replace(/_/g, " ");
  const conf = zs?.confidence ?? data?.confidence;
  const confPart = conf != null && conf > 0 ? ` (${Math.round(conf * 100)}% confidence)` : "";
  const tierPart = tierLabel ? `${tierLabel}: ` : "";
  const reason = zs?.reason || zs?.detail || data?.message || "";
  if (blocked) {
    const cat = category || "threat";
    return reason || `${tierPart}Input blocked — ${cat}${confPart}. Not a Policy Management rule.`;
  }
  if (category && category !== "none") {
    return reason || `${tierPart}Scan completed — flagged ${category}${confPart}; request allowed through.`;
  }
  return reason || `${tierPart}Input scan completed — no threats detected.`;
}

function formatInputScanBlockDetail(data, zs) {
  return formatInputScanDetail(data, zs, { blocked: true });
}

function scanStageAction(finalAction, blockedStage, httpStatus) {
  if (blockedStage === "input_scan" && (finalAction === "block" || httpStatus === 403)) {
    return "block";
  }
  if (finalAction === "redact") return "redact";
  if (finalAction === "flag") return "flag";
  return "allow";
}

function stageIndex(name) {
  return PIPELINE_STAGE_ORDER.indexOf(name);
}

function roundMs(value, fallback = 0) {
  const n = Number(value);
  if (!Number.isFinite(n) || n < 0) return fallback;
  return Math.round(n * 10) / 10;
}

function truncateText(text, limit = 1200) {
  const raw = String(text || "").trim();
  if (raw.length <= limit) return raw;
  return `${raw.slice(0, limit)}…`;
}

/**
 * Read a single header value case-insensitively.
 * Real fetch Headers.get() is already case-insensitive; a plain-object
 * fallback (tests, server-side proxies) is not, so probe the requested
 * key plus its lower/upper-cased variants before giving up.
 */
function getHeaderValue(headers, key) {
  if (!headers) return "";
  if (typeof headers.get === "function") return headers.get(key) || "";
  if (headers[key] != null) return headers[key] || "";
  const lower = key.toLowerCase();
  if (headers[lower] != null) return headers[lower] || "";
  const upper = key.toUpperCase();
  if (headers[upper] != null) return headers[upper] || "";
  // Last resort: linear case-insensitive scan of the plain object's keys.
  const match = Object.keys(headers).find((k) => k.toLowerCase() === lower);
  return match ? (headers[match] || "") : "";
}

/** Read routing telemetry from gateway response headers when zeroshield is redacted. */
export function extractRoutingFromHeaders(headers) {
  if (!headers) return {};
  const get = (key) => getHeaderValue(headers, key);
  return {
    requested_model: get("X-ZeroShield-Original-Model"),
    original_model: get("X-ZeroShield-Original-Model"),
    selected_model: get("X-ZeroShield-Routed-Model"),
    routed_model: get("X-ZeroShield-Routed-Model"),
    routing_reason: get("X-ZeroShield-Routing-Reason"),
    decision_source: get("X-ZeroShield-Routing-Source"),
    policy_summary: get("X-ZeroShield-Routing-Policy-Summary"),
    rerouted: get("X-ZeroShield-Rerouted") === "true",
  };
}

function metricsFromPayload(data, context = {}) {
  const fromBody = data?.stage_metrics_ms || data?.pipeline_trace?.stage_metrics_ms || {};
  const fromContext = context.stageMetrics || {};
  return { ...fromBody, ...fromContext };
}

function latencyForStage(stageName, stageMetrics, zs = {}, context = {}) {
  const tier1 = roundMs(stageMetrics.tier1_ms);
  const tier2 = roundMs(stageMetrics.tier2_ms);
  const map = {
    auth: roundMs(stageMetrics.auth_ms, 0.1),
    rate_limit: roundMs(stageMetrics.rate_limit_ms, 0.1),
    policy: roundMs(stageMetrics.policy_ms, 0.2),
    input_scan: roundMs(tier1 + tier2, roundMs(zs.processing_time_ms, 0.5)),
    kill_switch: roundMs(stageMetrics.kill_switch_ms, 0.1),
    model_routing: roundMs(stageMetrics.routing_ms, 0.5),
    model_input: roundMs(stageMetrics.model_input_ms, 0.1),
    model_output: roundMs(stageMetrics.upstream_ms, roundMs(zs.processing_time_ms, 0)),
    output_guardrail: roundMs(
      stageMetrics.output_guardrail_ms ?? stageMetrics.output_guard_ms,
      0.2,
    ),
  };
  if (map[stageName] > 0) return map[stageName];
  // Do NOT fabricate an even total/N split for stages with no real metric — it
  // rendered an identical (e.g. 714.7ms) latency on multiple unrelated stages,
  // misrepresenting where time was spent. Return the honest small default; the
  // real end-to-end time is surfaced separately as total_latency_ms.
  return 0.1;
}

export function ensureStageLatency(stage, stageMetrics, zs, context) {
  const next = { ...stage };
  if (next.latency_ms === undefined || next.latency_ms === null || Number.isNaN(Number(next.latency_ms))) {
    next.latency_ms = latencyForStage(next.name, stageMetrics, zs, context);
  } else {
    next.latency_ms = roundMs(next.latency_ms, 0.1);
  }
  return next;
}

function enrichStages(stages, data, zs, context) {
  const stageMetrics = metricsFromPayload(data, context);
  const promptPreview = truncateText(
    context.prompt || data?.pipeline_trace?.prompt_preview || "",
  );
  return (stages || []).map((stage) => {
    const enriched = ensureStageLatency({ ...stage }, stageMetrics, zs, context);
    if (stage.name === "input_scan" && !enriched.prompt_submitted) {
      enriched.prompt_submitted = promptPreview;
    }
    if (stage.name === "model_input") {
      if (!enriched.content && enriched.action !== "skip" && promptPreview) {
        enriched.content = promptPreview;
      }
      if (!enriched.prompt_submitted) enriched.prompt_submitted = promptPreview;
    }
    if (stage.name === "model_routing") {
      const routing = zs.routing || {};
      enriched.requested_model = enriched.requested_model
        || routing.original_model
        || routing.requested_model
        || context.requestedModel
        || "";
      enriched.selected_model = enriched.selected_model
        || routing.selected_model
        || routing.routed_model
        || zs.selected_model
        || "";
      const rawReason = enriched.routing_reason
        || routing.routing_reason
        || zs.routing_reason
        || context.routingHeaders?.routing_reason
        || "";
      const rawSource = enriched.decision_source
        || routing.decision_source
        || zs.decision_source
        || context.routingHeaders?.decision_source
        || "";
      enriched.decision_source = rawSource;
      enriched.decision_source_label = enriched.decision_source_label
        || formatDecisionSource(rawSource);
      enriched.routing_reason = formatRoutingReason(rawReason, { decisionSource: rawSource });
      enriched.policy_summary = enriched.policy_summary
        || routing.policy_summary
        || zs.policy_summary
        || context.routingHeaders?.policy_summary
        || "";
      enriched.decision_factors = enriched.decision_factors
        || routing.decision_factors
        || zs.decision_factors
        || [];
      enriched.weights = enriched.weights || routing.weights || zs.weights || {};
      if (!enriched.detail && enriched.routing_reason) {
        enriched.detail = enriched.routing_reason;
      }
    }
    if (stage.name === "kill_switch" && enriched.action === "reroute" && enriched.routing_reason) {
      enriched.detail = formatRoutingReason(enriched.routing_reason, {
        decisionSource: enriched.decision_source,
      });
    }
    return enriched;
  });
}

function skipDetail(stageName, blockedStage) {
  const blockedIdx = stageIndex(blockedStage);
  const idx = stageIndex(stageName);
  if (blockedIdx < 0 || idx <= blockedIdx) return "Not evaluated";

  const messages = {
    input_scan: "Skipped — request was already blocked upstream; guard model not invoked",
    kill_switch: "Skipped — request was already blocked upstream",
    model_routing: "Routing skipped because request was blocked before model selection",
    model_input: "Skipped — prompt was not delivered to the model",
    model_output: "Skipped — LLM was never called; request blocked upstream",
    output_guardrail: "Skipped because request was blocked before inference",
  };
  return messages[stageName] || "Skipped — blocked upstream";
}

function buildSimulatorStages(data, httpStatus, zs, finalAction, blockedStage, context = {}) {
  const routing = { ...(zs.routing || {}), ...(context.routingHeaders || {}) };
  const stageMetrics = metricsFromPayload(data, context);
  const promptPreview = truncateText(context.prompt || "");
  const blockedIdx = blockedStage ? stageIndex(blockedStage) : -1;
  const isBlocked = finalAction === "block";
  const isError = finalAction === "error" || (httpStatus >= 400 && !isBlocked);
  const requestedModel = context.requestedModel
    || data?.model
    || routing.original_model
    || zs.selected_model
    || routing.selected_model
    || "";
  const hasChoices = Boolean(data?.choices?.length);
  const estimatedTokens = context.estimatedTokens ?? estimateRequestTokens(context.prompt, context.maxTokens);

  const stageAt = (name) => {
    const idx = stageIndex(name);
    const terminalIdx = blockedIdx >= 0 ? blockedIdx : (isError ? stageIndex("model_output") : -1);
    if ((!isBlocked && !isError) || terminalIdx < 0) return "past";
    if (idx < terminalIdx) return "past";
    if (idx === terminalIdx) return "blocked";
    return "after";
  };

  const stages = [];

  // 1 — Auth
  {
    const at = stageAt("auth");
    stages.push({
      name: "auth",
      action: at === "blocked" ? "block" : "allow",
      latency_ms: latencyForStage("auth", stageMetrics, zs, context),
      detail: at === "blocked" ? "Gateway API key invalid or missing" : "Gateway API key accepted",
    });
  }

  // 2 — Rate limit
  {
    const at = stageAt("rate_limit");
    const rateBlocked = blockedStage === "rate_limit";
    stages.push({
      name: "rate_limit",
      action: rateBlocked ? "block" : at === "after" ? "skip" : "allow",
      latency_ms: latencyForStage("rate_limit", stageMetrics, zs, context),
      detail: rateBlocked
        ? formatRateLimitDetail(data, context)
        : at === "after"
          ? skipDetail("rate_limit", blockedStage)
          : `Within TPM budget (estimated ${estimatedTokens} tokens this request)`,
      estimated_tokens: estimatedTokens,
      threat_type: rateBlocked ? "rate_limit" : "",
    });
  }

  // 3 — Policy
  {
    const at = stageAt("policy");
    const policyBlocked = blockedStage === "policy";
    const matchedPolicies = data?.matched_policies || zs.matched_policies || [];
    let matchedRules = data?.matched_rules || zs.matched_rules || [];
    // The deterministic policy-redaction path reports the rule names it applied
    // in `matched_patterns` (the redact-path zeroshield carries no matched_rules),
    // so the Policy stage used to fall through to "allow" even though it had just
    // redacted PII. Detect a policy-tier redaction and surface those rule names so
    // the card honestly shows REDACT instead of ALLOW.
    const policyTier = String(zs.detection_tier || data?.detection_tier || "").toLowerCase() === "policy";
    const policyRedacted = policyTier && (finalAction === "redact" || String(zs.action || "").toLowerCase() === "redact");
    if (!matchedRules.length && policyRedacted) {
      const redactNames = (zs.matched_patterns || data?.matched_patterns || [])
        .filter((p) => /redact/i.test(String(p)));
      matchedRules = redactNames.length ? redactNames : (zs.matched_patterns || []);
    }
    const policyActed = Boolean(matchedPolicies?.length || matchedRules?.length || policyRedacted);
    stages.push({
      name: "policy",
      action: policyBlocked
        ? "block"
        : at === "after"
          ? "skip"
          : policyRedacted
            ? "redact"
            : (policyActed && finalAction === "flag" ? "flag" : "allow"),
      latency_ms: latencyForStage("policy", stageMetrics, zs, context),
      detail: policyBlocked
        ? formatPolicyBlockDetail(data, zs)
        : at === "after"
          ? skipDetail("policy", blockedStage)
          : policyRedacted
            ? `Policy engine redacted sensitive data — applied ${matchedRules.length} rule(s) before forwarding to the LLM`
            : (matchedRules?.length
              ? `Policy engine matched ${matchedRules.length} rule(s)`
              : "Policy engine evaluated request against compiled rules; no matching policy rule"),
      matched_policies: matchedPolicies,
      matched_rules: matchedRules,
      threat_type: policyBlocked ? (data?.category || zs.threat_type || "policy") : (policyRedacted ? (zs.threat_type || "pii") : ""),
    });
  }

  // 4 — Input scan
  {
    const at = stageAt("input_scan");
    const scanBlocked = blockedStage === "input_scan" && (finalAction === "block" || httpStatus === 403);
    const tier = zs.detection_tier || "";
    const scanRan = Boolean(tier || zs.threat_type || zs.reason || zs.detail);
    if (scanBlocked) {
      stages.push({
        name: "input_scan",
        action: "block",
        latency_ms: latencyForStage("input_scan", stageMetrics, zs, context) || roundMs(zs.processing_time_ms, 0.5),
        detail: formatInputScanBlockDetail(data, zs),
        threat_type: zs.threat_type || data?.category || "",
        confidence: zs.confidence ?? 0,
        tier: formatDetectionTier(tier) || tier,
        matched_patterns: zs.matched_patterns || [],
        prompt_submitted: promptPreview,
      });
    } else if (at === "after") {
      stages.push({
        name: "input_scan",
        action: "skip",
        latency_ms: latencyForStage("input_scan", stageMetrics, zs, context),
        prompt_submitted: promptPreview,
        detail: skipDetail("input_scan", blockedStage),
        tier: "skipped",
      });
    } else {
      // When the deterministic POLICY tier already redacted the prompt, Tier-2
      // input scanning runs on the ALREADY-REDACTED text — so it is clean, not a
      // flag. Attribute the redaction to the Policy stage and show input_scan as
      // a clean pass (it used to inherit the request-level "redact"/"flag").
      const policyTierRedact = String(tier).toLowerCase() === "policy"
        && (finalAction === "redact" || String(zs.action || "").toLowerCase() === "redact");
      stages.push({
        name: "input_scan",
        action: policyTierRedact ? "allow" : scanStageAction(finalAction, blockedStage, httpStatus),
        latency_ms: latencyForStage("input_scan", stageMetrics, zs, context) || roundMs(zs.processing_time_ms, 0.5),
        detail: policyTierRedact
          ? "Tier-2 scanned the policy-redacted prompt — no additional threats (PII already masked upstream)"
          : (scanRan
            ? formatInputScanDetail(data, zs, { blocked: false })
            : "Input scan not invoked for this request"),
        threat_type: (!policyTierRedact && zs.threat_type && zs.threat_type !== "none") ? zs.threat_type : "",
        confidence: policyTierRedact ? 0 : (zs.confidence ?? 0),
        tier: formatDetectionTier(tier) || tier || "",
        matched_patterns: policyTierRedact ? [] : (zs.matched_patterns || []),
        prompt_submitted: promptPreview,
      });
    }
  }

  // 5 — Kill switch
  {
    const at = stageAt("kill_switch");
    const ksBlocked = blockedStage === "kill_switch";
    const ksRerouted = Boolean(
      routing.rerouted && String(routing.trigger_source || routing.decision_source || "").toLowerCase() === "kill_switch",
    );
    stages.push({
      name: "kill_switch",
      action: ksBlocked ? "block" : ksRerouted ? "reroute" : at === "after" ? "skip" : "allow",
      latency_ms: latencyForStage("kill_switch", stageMetrics, zs, context),
      detail: ksBlocked
        ? (data?.message || "Model kill-switch is active")
        : ksRerouted
          ? formatRoutingReason(routing.routing_reason || routing.reason || "", {
            decisionSource: routing.decision_source,
          })
          : at === "after"
            ? skipDetail("kill_switch", blockedStage)
            : "No active kill-switch for this model",
    });
  }

  // 6 — Model routing
  {
    const at = stageAt("model_routing");
    const needsModel = finalAction === "needs_model";
    const routingBlocked = blockedStage === "model_routing" && !needsModel;
    const hasRouting = Boolean(
      routing.selected_model || routing.routed_model || zs.selected_model || zs.routing_reason,
    );
    const reqModel = routing.original_model || routing.requested_model || context.requestedModel || data?.model || "";
    const selModel = routing.selected_model || routing.routed_model || zs.selected_model || "";
    const rawRoutingReason = routing.routing_reason || zs.routing_reason || context.routingHeaders?.routing_reason || "";
    const rawDecisionSource = routing.decision_source || zs.decision_source || context.routingHeaders?.decision_source || "";
    const formattedReason = formatRoutingReason(rawRoutingReason, { decisionSource: rawDecisionSource });
    const rerouted = isRoutingReroute(reqModel, selModel, routing);
    stages.push({
      name: "model_routing",
      action: needsModel
        ? "needs_model"
        : routingBlocked
          ? "block"
          : at === "after"
            ? "skip"
            : rerouted
              ? "reroute"
              : hasRouting || !isBlocked
                ? "allow"
                : "skip",
      detail: needsModel
        ? (data?.message || "Connect your organization's inference model (Module 1.5 → Model Connection). ZeroShield guard models are for scanning only.")
        : routingBlocked
          ? (data?.message || "Model not allowed or not configured")
          : at === "after"
            ? skipDetail("model_routing", blockedStage)
            : (formattedReason
              || (hasRouting
                ? `Routed to ${formatModelDisplayName(selModel || requestedModel)}`
                : "Model routing evaluated")),
      model: requestedModel,
      requested_model: reqModel,
      selected_model: selModel,
      routed_model: routing.routed_model || routing.selected_model || "",
      route_destination: routing.route_destination || "llm",
      route_destination_label: routing.route_destination_label || formatRouteDestination(routing.route_destination || "llm"),
      routing_reason: formattedReason,
      decision_source: rawDecisionSource,
      decision_source_label: formatDecisionSource(rawDecisionSource),
      policy_summary: routing.policy_summary || zs.policy_summary || context.routingHeaders?.policy_summary || "",
      decision_factors: routing.decision_factors || zs.decision_factors || [],
      weights: routing.weights || zs.weights || {},
      routing_score: routing.routing_score || zs.routing_score || 0,
      candidate_count: routing.candidate_count || zs.candidate_count || 0,
      fallback_chain: routing.fallback_chain || zs.fallback_chain || [],
      evaluator_model: routing.evaluator_model || zs.evaluator_model || "",
      latency_ms: latencyForStage("model_routing", stageMetrics, zs, context),
    });
  }

  // 7 — Model input
  {
    const at = stageAt("model_input");
    const forwardedPrompt = zs.redacted_prompt || data?.redacted_prompt || promptPreview;
    const forwardedPreview = truncateText(forwardedPrompt);
    stages.push({
      name: "model_input",
      action: isBlocked && at !== "past" ? "skip" : at === "blocked" ? "block" : "allow",
      latency_ms: latencyForStage("model_input", stageMetrics, zs, context),
      detail: isBlocked
        ? "Prompt was not sent to the model (blocked upstream)"
        : "Sanitized prompt delivered to LLM after firewall processing",
      content: isBlocked ? "[BLOCKED — prompt was not sent to the model]" : forwardedPreview,
      prompt_submitted: isBlocked ? promptPreview : forwardedPreview,
    });
  }

  // 8 — Model output
  {
    const at = stageAt("model_output");
    const inferenceBlocked = blockedStage === "model_output";
    const errPayload = extractErrorPayload(data);
    if (hasChoices && !isBlocked && !isError) {
      stages.push({
        name: "model_output",
        action: "allow",
        latency_ms: latencyForStage("model_output", stageMetrics, zs, context),
        detail: `Model: ${formatModelDisplayName(data.model || zs.selected_model || "unknown")}`,
        content: data.choices[0]?.message?.content || "",
        model: data.model || zs.selected_model,
      });
    } else {
      const needsModel = finalAction === "needs_model";
      stages.push({
        name: "model_output",
        // An upstream inference FAILURE (502/error) is NOT a security block — it is
        // a provider/credential/infra error. Label it "error" so it is visually and
        // semantically distinct from a guardrail "block" (which denies content).
        // Only a genuine block-stage at model_output keeps "block".
        action: needsModel
          ? "skip"
          : isError
            ? "error"
            : inferenceBlocked
              ? "block"
              : isBlocked || !hasChoices
                ? "skip"
                : "allow",
        latency_ms: latencyForStage("model_output", stageMetrics, zs, context),
        detail: needsModel
          ? "Inference skipped — connect an organization model under Module 1.5 (Model Connection)"
          : inferenceBlocked || isError
            ? (errPayload.message || data?.message || "Model inference failed (upstream provider)")
            : isBlocked
              ? "LLM was never called; request blocked upstream"
              : hasChoices
                ? `Model: ${formatModelDisplayName(data.model || "unknown")}`
                : "Model inference intentionally skipped or not requested",
        content: isBlocked
          ? "[Not generated — request was blocked by the firewall]"
          : (data?.choices?.[0]?.message?.content || ""),
      });
    }
  }

  // 9 — Output guardrail
  {
    const at = stageAt("output_guardrail");
    const ogBlocked = blockedStage === "output_guardrail";
    const outContent = zs.redacted_response || zs.rewritten_response || data?.choices?.[0]?.message?.content || "";
    const outputGuardMs = roundMs(
      stageMetrics.output_guardrail_ms ?? stageMetrics.output_guard_ms,
      0,
    );
    const outputGuardRan = outputGuardMs > 0
      || zs.detection_tier === "output_guard"
      || hasOutputGuardSignal(zs, data);
    const gatewayErrorAfterOutputGuard = httpStatus >= 500 && outputGuardRan;
    if (ogBlocked) {
      stages.push({
        name: "output_guardrail",
        action: "block",
        latency_ms: latencyForStage("output_guardrail", stageMetrics, zs, context),
        detail: data?.message || zs.reason || zs.detail || "Output guard blocked the response",
        content: outContent,
      });
    } else if (gatewayErrorAfterOutputGuard) {
      stages.push({
        name: "output_guardrail",
        action: "error",
        latency_ms: latencyForStage("output_guardrail", stageMetrics, zs, context),
        detail: "Response failed after output guard ran (gateway internal error)",
        content: outContent,
      });
    } else if (hasOutputGuardSignal(zs, data) && !isBlocked) {
      stages.push({
        name: "output_guardrail",
        action: finalAction === "allow" ? "allow" : finalAction,
        latency_ms: latencyForStage("output_guardrail", stageMetrics, zs, context),
        detail: zs.reason || zs.detail || "Output guard inspection completed",
        content: outContent,
      });
    } else {
      stages.push({
        name: "output_guardrail",
        action: isBlocked ? "skip" : "allow",
        latency_ms: latencyForStage("output_guardrail", stageMetrics, zs, context),
        detail: isBlocked
          ? skipDetail("output_guardrail", blockedStage)
          : outContent
            ? "Output checks applied on live completion path"
            : "Output guard not evaluated",
        content: outContent,
      });
    }
  }

  return stages;
}

function hasOutputGuardSignal(zs, data) {
  return (
    zs.detection_tier === "output_guard"
    || zs.redacted_response
    || zs.rewritten_response
    || data?.code === "output_blocked"
  );
}

/** Map /v1/chat/completions response into Attack Simulator shape (full stages + final_action). */
export function normalizeChatPipelineResult(data, httpStatus, context = {}) {
  const routingHeaders = extractRoutingFromHeaders(context.responseHeaders);
  const mergedContext = {
    ...context,
    routingHeaders,
  };

  const mergeScanFieldsFromTrace = (zs, trace) => {
    const inputStage = (trace?.stages || []).find((s) => s.name === "input_scan");
    const outputStage = (trace?.stages || []).find((s) => s.name === "output_guardrail");
    const summary = trace?.guard_summary;
    const guardSource = summary || inputStage || outputStage;
    if (!inputStage && !summary) return zs;
    return {
      ...zs,
      reason: zs.reason || inputStage?.detail || summary?.guard_reason,
      detail: zs.detail || inputStage?.detail || summary?.guard_reason,
      threat_type: zs.threat_type || inputStage?.threat_type || summary?.threat_type,
      confidence: zs.confidence ?? inputStage?.confidence ?? summary?.confidence,
      matched_patterns: zs.matched_patterns?.length
        ? zs.matched_patterns
        : (inputStage?.matched_patterns || summary?.guard_findings),
      detection_tier: zs.detection_tier || inputStage?.tier,
      risk_score: zs.risk_score ?? inputStage?.risk_score ?? summary?.risk_score,
      scan_outcome: zs.scan_outcome || inputStage?.scan_outcome || summary?.scan_outcome,
      reason_code: zs.reason_code || inputStage?.reason_code || guardSource?.reason_code,
      guard_reason: zs.guard_reason || guardSource?.guard_reason,
      guard_action: zs.guard_action || guardSource?.guard_action,
      guard_model: zs.guard_model || guardSource?.guard_model,
      recommended_action: zs.recommended_action || guardSource?.recommended_action,
      guard_findings: zs.guard_findings?.length ? zs.guard_findings : guardSource?.guard_findings,
      enforcement_source: zs.enforcement_source || guardSource?.enforcement_source,
    };
  };

  if (data?.pipeline_trace?.stages?.length >= 6) {
    const zs = mergeScanFieldsFromTrace(data.zeroshield || {}, data.pipeline_trace);
    const finalAction = inferFinalAction(data, httpStatus, zs);
    const blockedStage = inferTerminalBlockedStage(data, httpStatus, zs, finalAction);
    const stages = enrichStages(data.pipeline_trace.stages, data, zs, mergedContext);
    const totalLatency = resolveTotalLatencyMs({
      pipelineTrace: data.pipeline_trace,
      zeroshield: zs,
      clientMs: context.totalLatencyMs,
    }) ?? data.pipeline_trace.total_latency_ms ?? context.totalLatencyMs;
    const guardSummary = data.pipeline_trace.guard_summary || null;
    const stageLatencySum = data.pipeline_trace.stage_latency_sum_ms;
    const overheadMs = data.pipeline_trace.overhead_ms;
    const ttftMs = resolveTtftMs({ pipelineTrace: data.pipeline_trace, zeroshield: zs });
    // Hoist stages/guard_summary/total_latency to the top level (the single
    // source the simulator reads) and DROP the raw pipeline_trace so the result
    // doesn't carry a full duplicate of the stage array + guard summary.
    const { pipeline_trace: _omitTrace, ...rest } = data;
    return {
      ...rest,
      final_action: finalAction,
      blocked_by: blockedStage || "",
      detection_checkpoint: inferDetectionCheckpoint(data, zs),
      zeroshield: zs,
      guard_summary: guardSummary,
      request_id: data.request_id || zs.request_id,
      stages,
      total_latency_ms: totalLatency,
      stage_latency_sum_ms: stageLatencySum,
      overhead_ms: overheadMs,
      ttft_ms: ttftMs,
      estimated_tokens: context.estimatedTokens ?? estimateRequestTokens(context.prompt, context.maxTokens),
      pipeline_live: true,
    };
  }

  if (Array.isArray(data?.stages) && data.stages.length >= 6) {
    const zs = data.zeroshield || {};
    const finalAction = inferFinalAction(data, httpStatus, zs);
    const blockedStage = inferTerminalBlockedStage(data, httpStatus, zs, finalAction);
    return {
      ...data,
      final_action: finalAction,
      blocked_by: blockedStage || "",
      detection_checkpoint: inferDetectionCheckpoint(data, zs),
      zeroshield: zs,
      request_id: data.request_id || zs.request_id,
      stages: enrichStages(data.stages, data, zs, mergedContext),
      total_latency_ms: context.totalLatencyMs,
      pipeline_live: true,
    };
  }

  const zs = mergeScanFieldsFromTrace(
    { ...(data?.zeroshield || {}), ...routingHeaders, routing: { ...(data?.zeroshield?.routing || {}), ...routingHeaders } },
    data?.pipeline_trace,
  );
  const payload = { ...(data || {}), zeroshield: zs };
  const finalAction = inferFinalAction(payload, httpStatus, zs);
  const blockedStage = inferTerminalBlockedStage(payload, httpStatus, zs, finalAction);
  const stages = enrichStages(
    buildSimulatorStages(payload, httpStatus, zs, finalAction, blockedStage, mergedContext),
    payload,
    zs,
    mergedContext,
  );

  const inputStage = stages.find((s) => s.name === "input_scan");
  const guardSummary = payload.pipeline_trace?.guard_summary
    || (inputStage?.guard_reason
      ? {
        guard_model: inputStage.guard_model,
        guard_action: inputStage.guard_action,
        guard_reason: inputStage.guard_reason,
        reason_code: inputStage.reason_code,
        recommended_action: inputStage.recommended_action,
        guard_findings: inputStage.guard_findings,
      }
      : null);

  const totalLatency = resolveTotalLatencyMs({
    pipelineTrace: payload.pipeline_trace,
    zeroshield: zs,
    clientMs: context.totalLatencyMs,
  }) ?? context.totalLatencyMs ?? payload.pipeline_trace?.total_latency_ms;
  const ttftMs = resolveTtftMs({ pipelineTrace: payload.pipeline_trace, zeroshield: zs });
  // Drop the raw pipeline_trace — its stages/guard_summary are hoisted below.
  const { pipeline_trace: _omitTrace, ...payloadRest } = payload;
  return {
    ...payloadRest,
    final_action: finalAction,
    blocked_by: blockedStage || "",
    detection_checkpoint: inferDetectionCheckpoint(payload, zs),
    stages,
    zeroshield: zs,
    guard_summary: guardSummary,
    request_id: payload.request_id || zs.request_id,
    total_latency_ms: totalLatency,
    stage_latency_sum_ms: payload.pipeline_trace?.stage_latency_sum_ms,
    overhead_ms: payload.pipeline_trace?.overhead_ms,
    ttft_ms: ttftMs,
    estimated_tokens: context.estimatedTokens ?? estimateRequestTokens(context.prompt, context.maxTokens),
    pipeline_live: true,
  };
}

/** Output guard panel: run text through live chat path (output scanned on response). */
export function outputGuardChatBody(text, contextChunks = [], model) {
  const contextHint = contextChunks?.length
    ? `\n\nReference context:\n${contextChunks.join("\n")}`
    : "";
  // NOTE: the prompt must NOT look like an instruction-override / jailbreak, or the
  // Tier-2 INPUT scanner blocks it as prompt_injection (403) and the OUTPUT guard is
  // never reached — the simulator would only ever show an input block. "Repeat the
  // following text exactly as your entire reply (no preamble)" tripped exactly that.
  // A neutral "echo for receipt confirmation" framing passes input scan and still
  // reproduces the text so the output guard can act on it. (High-sensitivity inputs —
  // raw SSN/card/credentials — are still intercepted by the INPUT guard before
  // inference; that is defense-in-depth, not a bug. Output-guard demonstration is
  // most reliable on model-generated sensitive content.)
  return chatCompletionBody({
    prompt: `Echo the following record back unchanged for a receipt confirmation:\n\n${text}${contextHint}`,
    model,
    runInference: true,
    maxTokens: 1024,
  });
}

export function normalizeOutputGuardResult(data, httpStatus) {
  const zs = data?.zeroshield || {};
  const content = data?.choices?.[0]?.message?.content || "";
  return {
    request_id: zs.request_id || data.request_id,
    action: zs.action || (httpStatus === 403 ? "block" : "allow"),
    threat_type: zs.threat_type || "",
    confidence: zs.confidence ?? 0,
    detail: zs.detail || zs.reason || "",
    matched_patterns: zs.matched_patterns || [],
    compliance_tags: zs.compliance_tags || [],
    raw_text: content,
    safe_text: zs.redacted_response || zs.rewritten_response || content,
    // The gateway returns a single boolean factuality signal, not granular
    // grounding/pattern/contradiction sub-scores. Carry ONLY the real flag so
    // the UI cannot render fabricated per-metric percentages (grounding was
    // hardcoded 100%, pattern/contradiction 0%, risk a boolean→70% literal).
    factuality_warning: Boolean(zs.factuality_warning),
    escalation_flag: Boolean(zs.review_required || zs.security_incident),
    latency_ms: zs.processing_time_ms,
  };
}

export function normalizeRoutingResult(data) {
  const zs = data?.zeroshield || {};
  const routing = zs.routing || zs;
  return {
    chosen_model: routing.selected_model || routing.routed_model || zs.selected_model || data?.model,
    fallback_chain: routing.fallback_chain || zs.fallback_chain || [],
    routing_reason: routing.routing_reason || zs.routing_reason,
    policy_summary: routing.policy_summary || zs.policy_summary,
    scored_models: [],
    routing,
  };
}

export function ragQueryPayload({ query, collection, vectorDbType = "pinecone", nResults = 10 }) {
  return {
    collection,
    query,
    vector_db_type: vectorDbType,
    n_results: nResults,
  };
}
