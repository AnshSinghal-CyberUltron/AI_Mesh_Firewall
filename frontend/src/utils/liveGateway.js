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

  // ZeroShield input scan (Tier 1/2 guard) — not Policy Management rules
  if (
    tier === "tier_1"
    || tier === "tier_1_5"
    || tier === "tier_2"
    || tier === "input_scan"
    || category === "prompt_injection"
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

function formatInputScanBlockDetail(data, zs) {
  const tierLabel = formatDetectionTier(data?.detection_tier || zs?.detection_tier || "");
  const category = (data?.category || zs?.threat_type || "threat").replace(/_/g, " ");
  const conf = zs?.confidence ?? data?.confidence;
  const confPart = conf != null && conf > 0 ? ` (confidence ${Math.round(conf * 100)}%)` : "";
  const tierPart = tierLabel ? `${tierLabel}: ` : "";
  return `${tierPart}ZeroShield input scan detected ${category}${confPart}. Not a Policy Management rule.`;
}

function stageIndex(name) {
  return PIPELINE_STAGE_ORDER.indexOf(name);
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
  const routing = zs.routing || {};
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
      latency_ms: 0,
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
      latency_ms: 0,
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
      latency_ms: 0,
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
    const scanBlocked = blockedStage === "input_scan";
    const tier = zs.detection_tier || "";
    if (at === "past" && !scanBlocked && (tier || zs.threat_type) && finalAction !== "block") {
      stages.push({
        name: "input_scan",
        action: finalAction === "redact" ? "redact" : finalAction === "flag" ? "flag" : "allow",
        latency_ms: zs.processing_time_ms || 0,
        detail: zs.detail || zs.reason || "No input threats detected",
        threat_type: zs.threat_type || "",
        confidence: zs.confidence ?? 0,
        tier: formatDetectionTier(tier) || tier,
        matched_patterns: zs.matched_patterns || [],
      });
    } else if (scanBlocked) {
      stages.push({
        name: "input_scan",
        action: "block",
        latency_ms: zs.processing_time_ms || 0,
        detail: formatInputScanBlockDetail(data, zs),
        threat_type: zs.threat_type || data?.category || "",
        confidence: zs.confidence ?? 0,
        tier: formatDetectionTier(tier) || tier,
        matched_patterns: zs.matched_patterns || [],
      });
    } else {
      stages.push({
        name: "input_scan",
        action: at === "after" ? "skip" : "allow",
        latency_ms: at === "after" ? undefined : 0,
        detail: at === "after"
          ? skipDetail("input_scan", blockedStage)
          : "No input threats detected",
        tier: at === "after" ? "skipped" : "",
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
      latency_ms: undefined,
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
      routing_reason: routing.routing_reason || zs.routing_reason || "",
    });
  }

  // 7 — Model input
  {
    const at = stageAt("model_input");
    stages.push({
      name: "model_input",
      action: isBlocked && at !== "past" ? "skip" : at === "blocked" ? "block" : "allow",
      detail: isBlocked
        ? "[BLOCKED — prompt was not sent to the model]"
        : "Sanitized prompt delivered to LLM after firewall processing",
      content: isBlocked ? "[BLOCKED — prompt was not sent to the model]" : "",
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
        detail: data?.message || zs.reason || zs.detail || "Output guard blocked the response",
        content: outContent,
      });
    } else if (hasOutputGuardSignal(zs, data) && !isBlocked) {
      stages.push({
        name: "output_guardrail",
        action: finalAction === "allow" ? "allow" : finalAction,
        detail: zs.reason || zs.detail || "Output guard inspection completed",
        content: outContent,
      });
    } else {
      stages.push({
        name: "output_guardrail",
        action: isBlocked ? "skip" : "allow",
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
  if (Array.isArray(data?.stages) && data.stages.length >= 6) {
    const zs = data.zeroshield || {};
    const finalAction = inferFinalAction(data, httpStatus, zs);
    return {
      ...data,
      final_action: finalAction,
      blocked_by: data.blocked_by || inferBlockedStage(data, httpStatus, zs),
      zeroshield: zs,
      request_id: data.request_id || zs.request_id,
    };
  }

  const zs = data?.zeroshield || {};
  const finalAction = inferFinalAction(data, httpStatus, zs);
  const blockedStage = inferBlockedStage(data, httpStatus, zs);
  const blockedBy = data?.blocked_by || blockedStage || "";
  const stages = buildSimulatorStages(data, httpStatus, zs, finalAction, blockedStage, context);

  return {
    ...data,
    final_action: finalAction,
    blocked_by: blockedBy,
    stages,
    zeroshield: zs,
    request_id: data.request_id || zs.request_id,
    estimated_tokens: context.estimatedTokens ?? estimateRequestTokens(context.prompt, context.maxTokens),
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
