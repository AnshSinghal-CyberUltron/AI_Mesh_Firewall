/**
 * Helpers for Module 1 UI panels that exercise the live gateway (no /v1/simulator/*).
 */

import { formatDetectionTier, formatModelDisplayName } from "../constants/zeroshieldBrand";

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
    body.max_tokens = 0;
  }
  if (routingPreferences && typeof routingPreferences === "object") {
    body.routing_preferences = routingPreferences;
  }
  return body;
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

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const parts = buffer.split("\n\n");
    buffer = parts.pop() || "";
    for (const part of parts) {
      const line = part.trim();
      if (!line.startsWith("data: ")) continue;
      const payload = line.slice(6).trim();
      if (payload === "[DONE]") {
        events.push({ type: "done" });
        continue;
      }
      let parsed;
      try {
        parsed = JSON.parse(payload);
      } catch {
        events.push({ type: "raw", payload });
        continue;
      }
      events.push({ type: "data", payload: parsed });
      if (parsed?.error) {
        terminalError = parsed.error;
      }
      const delta = parsed?.choices?.[0]?.delta?.content;
      if (typeof delta === "string") {
        aggregatedContent += delta;
      }
    }
  }

  return { isStream: true, events, aggregatedContent, terminalError, raw: "" };
}

/** Map streamed chat completion into Attack Simulator pipeline shape. */
export function normalizeStreamChatPipelineResult(
  sseResult,
  httpStatus,
  responseHeaders,
  context = {},
) {
  const headers = responseHeaders || {};
  const scanMode = headers.get?.("x-zeroshield-stream-scan-mode")
    || headers["x-zeroshield-stream-scan-mode"]
    || "";
  const terminalErr = sseResult?.terminalError;
  const blockedInStream = Boolean(
    terminalErr
    && (terminalErr.type === "output_blocked" || terminalErr.code === "output_blocked"),
  );

  if (httpStatus === 403 || (httpStatus >= 400 && !sseResult?.isStream)) {
    return normalizeChatPipelineResult(sseResult?.data || {}, httpStatus, context);
  }

  const synthetic = {
    choices: sseResult?.aggregatedContent
      ? [{ message: { content: sseResult.aggregatedContent } }]
      : [],
    zeroshield: {
      action: blockedInStream ? "block" : terminalErr ? "error" : "allow",
      threat_type: blockedInStream ? (terminalErr?.type || "output_blocked") : "",
      stream: true,
      stream_scan_mode: scanMode,
    },
    error: terminalErr || undefined,
  };

  const normalized = normalizeChatPipelineResult(synthetic, blockedInStream ? 403 : httpStatus, context);
  return {
    ...normalized,
    stream: true,
    stream_events: sseResult?.events?.length || 0,
    stream_scan_mode: scanMode,
    aggregated_content: sseResult?.aggregatedContent || "",
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

/** Read routing telemetry from gateway response headers when zeroshield is redacted. */
export function extractRoutingFromHeaders(headers) {
  if (!headers) return {};
  const get = (key) => {
    if (typeof headers.get === "function") return headers.get(key) || "";
    return headers[key] || "";
  };
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
    output_guardrail: roundMs(stageMetrics.output_guard_ms, 0.2),
  };
  if (map[stageName] > 0) return map[stageName];
  const total = roundMs(context.totalLatencyMs);
  if (total > 0) {
    const idx = stageIndex(stageName);
    if (idx >= 0) return roundMs(total / PIPELINE_STAGE_ORDER.length, 0.1);
  }
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
      enriched.requested_model = enriched.requested_model || routing.original_model || routing.requested_model || context.requestedModel || "";
      enriched.selected_model = enriched.selected_model || routing.selected_model || routing.routed_model || zs.selected_model || "";
      enriched.routing_reason = enriched.routing_reason || routing.routing_reason || zs.routing_reason || context.routingHeaders?.routing_reason || "";
      enriched.decision_source = enriched.decision_source || routing.decision_source || zs.decision_source || context.routingHeaders?.decision_source || "";
      enriched.policy_summary = enriched.policy_summary || routing.policy_summary || zs.policy_summary || context.routingHeaders?.policy_summary || "";
      enriched.decision_factors = enriched.decision_factors || routing.decision_factors || zs.decision_factors || [];
      enriched.weights = enriched.weights || routing.weights || zs.weights || {};
      if (!enriched.detail && enriched.routing_reason) {
        enriched.detail = enriched.routing_reason;
      }
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
    const matched = zs.matched_patterns || data?.matched_policies || [];
    stages.push({
      name: "policy",
      action: policyBlocked
        ? "block"
        : at === "after"
          ? "skip"
          : (finalAction === "redact" || finalAction === "flag" ? finalAction : "allow"),
      latency_ms: latencyForStage("policy", stageMetrics, zs, context),
      detail: policyBlocked
        ? formatPolicyBlockDetail(data, zs)
        : at === "after"
          ? skipDetail("policy", blockedStage)
          : "Policy Management: no organization rules configured — stage allowed",
      matched_policies: matched,
      threat_type: policyBlocked ? (data?.category || zs.threat_type || "policy") : "",
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
      stages.push({
        name: "input_scan",
        action: scanStageAction(finalAction, blockedStage, httpStatus),
        latency_ms: latencyForStage("input_scan", stageMetrics, zs, context) || roundMs(zs.processing_time_ms, 0.5),
        detail: scanRan
          ? formatInputScanDetail(data, zs, { blocked: false })
          : "Input scan not invoked for this request",
        threat_type: zs.threat_type && zs.threat_type !== "none" ? zs.threat_type : "",
        confidence: zs.confidence ?? 0,
        tier: formatDetectionTier(tier) || tier || "",
        matched_patterns: zs.matched_patterns || [],
        prompt_submitted: promptPreview,
      });
    }
  }

  // 5 — Kill switch
  {
    const at = stageAt("kill_switch");
    const ksBlocked = blockedStage === "kill_switch";
    stages.push({
      name: "kill_switch",
      action: ksBlocked ? "block" : at === "after" ? "skip" : "allow",
      latency_ms: latencyForStage("kill_switch", stageMetrics, zs, context),
      detail: ksBlocked
        ? (data?.message || "Model kill-switch is active")
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
    stages.push({
      name: "model_routing",
      action: needsModel
        ? "needs_model"
        : routingBlocked
          ? "block"
          : at === "after"
            ? "skip"
            : hasRouting || !isBlocked
              ? "allow"
              : "skip",
      detail: needsModel
        ? (data?.message || "Connect your organization's inference model (Module 1.5 → Model Connection). ZeroShield guard models are for scanning only.")
        : routingBlocked
          ? (data?.message || "Model not allowed or not configured")
          : at === "after"
            ? skipDetail("model_routing", blockedStage)
            : (zs.routing_reason || routing.routing_reason
              || (hasRouting
                ? `Routed to ${formatModelDisplayName(zs.selected_model || routing.selected_model || requestedModel)}`
                : "Model routing evaluated")),
      model: requestedModel,
      requested_model: routing.original_model || data?.model || routing.requested_model || "",
      selected_model: routing.selected_model || zs.selected_model || "",
      routed_model: routing.routed_model || routing.selected_model || "",
      routing_reason: routing.routing_reason || zs.routing_reason || context.routingHeaders?.routing_reason || "",
      decision_source: routing.decision_source || zs.decision_source || context.routingHeaders?.decision_source || "",
      policy_summary: routing.policy_summary || zs.policy_summary || context.routingHeaders?.policy_summary || "",
      decision_factors: routing.decision_factors || zs.decision_factors || [],
      weights: routing.weights || zs.weights || {},
      latency_ms: latencyForStage("model_routing", stageMetrics, zs, context),
    });
  }

  // 7 — Model input
  {
    const at = stageAt("model_input");
    stages.push({
      name: "model_input",
      action: isBlocked && at !== "past" ? "skip" : at === "blocked" ? "block" : "allow",
      latency_ms: latencyForStage("model_input", stageMetrics, zs, context),
      detail: isBlocked
        ? "Prompt was not sent to the model (blocked upstream)"
        : "Sanitized prompt delivered to LLM after firewall processing",
      content: isBlocked ? "[BLOCKED — prompt was not sent to the model]" : promptPreview,
      prompt_submitted: promptPreview,
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
        action: needsModel
          ? "skip"
          : inferenceBlocked || isError
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
    if (ogBlocked) {
      stages.push({
        name: "output_guardrail",
        action: "block",
        latency_ms: latencyForStage("output_guardrail", stageMetrics, zs, context),
        detail: data?.message || zs.reason || zs.detail || "Output guard blocked the response",
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
      guard_reason: zs.guard_reason || guardSource?.guard_reason,
      guard_action: zs.guard_action || guardSource?.guard_action,
      guard_model: zs.guard_model || guardSource?.guard_model,
      reason_code: zs.reason_code || guardSource?.reason_code,
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
    return {
      ...data,
      final_action: finalAction,
      blocked_by: blockedStage || "",
      detection_checkpoint: inferDetectionCheckpoint(data, zs),
      zeroshield: zs,
      guard_summary: data.pipeline_trace.guard_summary || null,
      request_id: data.request_id || zs.request_id,
      stages,
      total_latency_ms: data.pipeline_trace.total_latency_ms ?? context.totalLatencyMs,
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

  return {
    ...payload,
    final_action: finalAction,
    blocked_by: blockedStage || "",
    detection_checkpoint: inferDetectionCheckpoint(payload, zs),
    stages,
    zeroshield: zs,
    guard_summary: guardSummary,
    request_id: payload.request_id || zs.request_id,
    total_latency_ms: context.totalLatencyMs ?? payload.pipeline_trace?.total_latency_ms,
    estimated_tokens: context.estimatedTokens ?? estimateRequestTokens(context.prompt, context.maxTokens),
    pipeline_live: true,
  };
}

/** Output guard panel: run text through live chat path (output scanned on response). */
export function outputGuardChatBody(text, contextChunks = [], model) {
  const contextHint = contextChunks?.length
    ? `\n\nReference context:\n${contextChunks.join("\n")}`
    : "";
  return chatCompletionBody({
    prompt: `Repeat the following text exactly as your entire reply (no preamble):\n\n${text}${contextHint}`,
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
    redacted_tokens: [],
    hallucination: {
      risk_score: zs.factuality_warning ? 0.7 : 0,
      pattern_score: 0,
      grounding_score: 1,
      contradiction_score: 0,
      matched_markers: [],
    },
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
