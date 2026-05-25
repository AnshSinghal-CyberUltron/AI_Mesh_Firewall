/**
 * Helpers for Module 1 UI panels that exercise the live gateway (no /v1/simulator/*).
 */

import { formatDetectionTier } from "../constants/zeroshieldBrand";

export function chatCompletionBody({
  prompt,
  model = "auto",
  runInference = true,
  routingPreferences = null,
  maxTokens = 512,
}) {
  const body = {
    model,
    messages: [{ role: "user", content: prompt }],
    max_tokens: maxTokens,
  };
  if (!runInference) {
    body.max_tokens = 0;
  }
  if (routingPreferences && typeof routingPreferences === "object") {
    body.routing_preferences = routingPreferences;
  }
  return body;
}

/** Map /v1/chat/completions response into Attack Simulator shape (stages + final_action). */
export function normalizeChatPipelineResult(data, httpStatus) {
  const zs = data?.zeroshield || {};
  const action = zs.action || (httpStatus === 403 ? "block" : "allow");
  const tier = zs.detection_tier || "";
  const routing = zs.routing || {};

  const stages = [
    { name: "auth", action: "allow", latency_ms: 0, detail: "Gateway API key accepted" },
  ];

  if (httpStatus === 429 || data?.code === "rate_limit_exceeded") {
    stages.push({
      name: "rate_limit",
      action: "block",
      latency_ms: 0,
      detail: data?.message || "Token rate limit exceeded",
    });
  } else {
    stages.push({ name: "rate_limit", action: "allow", latency_ms: 0, detail: "Within TPM budget" });
  }

  const policyBlock = action === "block" && tier && !tier.includes("scan") && tier !== "input_scan" && tier !== "tier";
  stages.push({
    name: "policy",
    action: policyBlock ? "block" : "allow",
    latency_ms: 0,
    detail: zs.reason || zs.detail || "Policy evaluation",
    matched_policies: zs.matched_patterns || [],
  });

  if (tier === "input_scan" || tier === "tier_1" || tier === "tier_2" || zs.threat_type) {
    stages.push({
      name: "input_scan",
      action: action === "block" ? "block" : action === "redact" ? "redact" : action === "flag" ? "flag" : "allow",
      latency_ms: zs.processing_time_ms || 0,
      detail: zs.detail || zs.reason || "",
      threat_type: zs.threat_type || "",
      confidence: zs.confidence ?? 0,
      tier: formatDetectionTier(tier) || tier,
    });
  } else if (httpStatus === 403) {
    stages.push({ name: "input_scan", action: "skip", detail: "Blocked before input scan" });
  } else {
    stages.push({ name: "input_scan", action: "allow", detail: "No input threats detected" });
  }

  stages.push({
    name: "kill_switch",
    action: "allow",
    detail: "No active kill-switch for this model",
  });

  if (routing.selected_model || routing.routed_model || zs.selected_model) {
    stages.push({
      name: "routing",
      action: "allow",
      detail: zs.routing_reason || routing.routing_reason || `Routed to ${zs.selected_model || routing.selected_model || ""}`,
    });
  }

  if (data?.choices?.length) {
    stages.push({
      name: "model_inference",
      action: "allow",
      detail: `Model: ${data.model || zs.selected_model || "unknown"}`,
      content: data.choices[0]?.message?.content || "",
    });
  } else if (httpStatus === 403) {
    stages.push({ name: "model_inference", action: "skip", detail: "Inference skipped (blocked upstream)" });
  }

  if (tier === "output_guard" || zs.redacted_response || zs.rewritten_response) {
    stages.push({
      name: "output_guardrail",
      action: action,
      detail: zs.reason || zs.detail || "Output guard inspection",
      content: zs.redacted_response || zs.rewritten_response || data?.choices?.[0]?.message?.content || "",
    });
  } else if (data?.choices?.length) {
    stages.push({
      name: "output_guardrail",
      action: action === "allow" ? "allow" : action,
      detail: zs.reason || "Output checks applied on live completion path",
      content: data?.choices?.[0]?.message?.content || "",
    });
  }

  return {
    ...data,
    final_action: action,
    blocked_by: action === "block" ? tier || zs.threat_type || "policy" : "",
    stages,
    zeroshield: zs,
    request_id: zs.request_id,
  };
}

/** Output guard panel: run text through live chat path (output scanned on response). */
export function outputGuardChatBody(text, contextChunks = []) {
  const contextHint = contextChunks?.length
    ? `\n\nReference context:\n${contextChunks.join("\n")}`
    : "";
  return chatCompletionBody({
    prompt: `Repeat the following text exactly as your entire reply (no preamble):\n\n${text}${contextHint}`,
    model: "auto",
    runInference: true,
    maxTokens: 1024,
  });
}

export function normalizeOutputGuardResult(data, httpStatus) {
  const zs = data?.zeroshield || {};
  const content = data?.choices?.[0]?.message?.content || "";
  return {
    request_id: zs.request_id,
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
    routing: routing,
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
