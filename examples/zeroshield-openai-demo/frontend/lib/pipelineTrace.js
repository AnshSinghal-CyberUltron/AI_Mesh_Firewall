/**
 * Port of frontend/src/utils/pipelineTrace.js (demo vanilla copy).
 * Source of truth: AI Mesh Firewall frontend/src/utils/pipelineTrace.js
 */
export const ACTION_BADGE = {
  block: "blocked",
  redact: "redacted",
  rewrite: "rewritten",
  flag: "flagged",
  monitor: "monitored",
  reroute: "rerouted",
  skip: "skipped",
  allow: "allowed",
};

export const STAGE_LABELS = {
  auth: "Auth",
  rate_limit: "Rate Limit",
  policy: "Policy Engine",
  policy_redact: "Policy Redaction",
  input_scan: "Input Scan (Tier-1/2)",
  route: "Model Routing",
  model_routing: "Model Routing",
  kill_switch: "Kill-Switch",
  llm: "Model Call",
  model_input: "Model Input",
  model_output: "Model Output",
  output_guard: "Output Guard",
  output_guardrail: "Output Guard",
};

export function honestStageAction(stage) {
  let a = String((stage && stage.action) || "allow").toLowerCase();
  const noop = stage && ((stage.metadata && stage.metadata.redact_noop) || stage.redact_noop);
  const analyzed = stage && stage.scan_outcome === "analyzed";
  if (analyzed && a === "allow") return a;
  if (a === "redact" && noop) {
    if (analyzed) return "allow";
    a = "flag";
  }
  return a;
}

export function normalizeStages(raw) {
  return Array.isArray(raw)
    ? raw
      .filter((s) => s && typeof s === "object")
      .map((s) => {
        if (!s.name && s.stage) return { ...s, name: s.stage };
        return s;
      })
    : [];
}

export function extractFinalAction(event, fallback) {
  const meta = (event && event.metadata) || {};
  const extra = meta.extra || {};
  const zs = (event && event.zeroshield) || meta.zeroshield || extra.zeroshield || {};
  const trace = zs.pipeline_trace || meta.pipeline_trace || extra.pipeline_trace || (event && event.pipeline_trace) || {};
  return trace.final_action || (event && event.action) || fallback || "allow";
}

function finiteMs(value) {
  const n = Number(value);
  return Number.isFinite(n) && n >= 0 ? n : null;
}

export function resolvePipelineTrace(sources = {}) {
  const meta = sources.meta || {};
  const extra = meta.extra || {};
  return sources.pipelineTrace
    || meta.pipeline_trace
    || extra.pipeline_trace
    || sources.logData?.pipeline_trace
    || sources.zeroshield?.pipeline_trace
    || null;
}

export function resolveTotalLatencyMs(sources = {}) {
  const meta = sources.meta || {};
  const extra = meta.extra || {};
  const trace = resolvePipelineTrace({ ...sources, meta, extra });

  const fromTrace = finiteMs(trace?.total_latency_ms);
  if (fromTrace != null && fromTrace > 0) return fromTrace;

  const stageSum = finiteMs(trace?.stage_latency_sum_ms);
  const overhead = finiteMs(trace?.overhead_ms) ?? 0;
  if (stageSum != null && stageSum > 0) {
    return Math.round((stageSum + overhead) * 10) / 10;
  }

  if (Array.isArray(trace?.stages) && trace.stages.length) {
    const summed = trace.stages.reduce(
      (acc, s) => acc + (finiteMs(s?.latency_ms) ?? 0),
      0,
    );
    if (summed > 0) return Math.round(summed * 10) / 10;
  }

  const fromMeta = finiteMs(meta.latency_ms) ?? finiteMs(extra.latency_ms);
  if (fromMeta != null && fromMeta > 0) return fromMeta;

  if (fromTrace === 0) return 0;
  return null;
}

export const LATENCY_MS_PARITY_TOLERANCE = 0.1;

export function formatPipelineDurationMs(ms) {
  if (ms == null || !Number.isFinite(Number(ms))) return "--";
  const rounded = Math.round(Number(ms) * 10) / 10;
  return `${rounded}ms`;
}

export function parsePipelineDurationMs(label) {
  if (label == null || label === "--") return null;
  const m = String(label).match(/^([\d.]+)\s*ms\b/i);
  if (!m) return null;
  const n = Number(m[1]);
  return Number.isFinite(n) ? n : null;
}

export function latencyMsWithinTolerance(apiMs, uiMs, tolerance = LATENCY_MS_PARITY_TOLERANCE) {
  const a = finiteMs(apiMs);
  const u = finiteMs(uiMs);
  if (a == null || u == null) return false;
  return Math.abs(a - u) <= tolerance;
}

export function resolveTtftMs(sources = {}) {
  const meta = sources.meta || {};
  const extra = meta.extra || {};
  const zs = sources.zeroshield || meta.zeroshield || extra.zeroshield || {};
  const trace = resolvePipelineTrace({ ...sources, meta, extra });
  return finiteMs(trace?.ttft_ms)
    ?? finiteMs(zs?.ttft_ms)
    ?? finiteMs(meta.ttft_ms)
    ?? finiteMs(extra.ttft_ms)
    ?? null;
}

function computeByStage(trace, totalMs) {
  const stages = Array.isArray(trace?.stages) ? trace.stages : [];
  const total = finiteMs(totalMs) ?? 0;
  return stages
    .filter((s) => s && (s.name || s.stage))
    .map((s) => {
      const stage = s.name || s.stage;
      const latency_ms = finiteMs(s.latency_ms) ?? 0;
      const share_pct = total > 0 ? Math.round((latency_ms / total) * 1000) / 10 : 0;
      return { stage, latency_ms, share_pct };
    })
    .sort((a, b) => b.latency_ms - a.latency_ms);
}

const STAGE_REDUCTION_HINTS = {
  model_output: [
    "Switch to a smaller or faster model for this workload.",
    "Enable prompt/response caching for repeat traffic.",
    "Lower max_tokens or simplify tool schemas.",
  ],
  input_scan: [
    "Run Tier-2 scans asynchronously when policy allows.",
    "Disable Tier-2 for low-risk endpoints or monitor-only posture.",
  ],
  policy: [
    "Reduce the number of active policy rules for this endpoint.",
    "Compile and cache policy bundles to avoid per-request re-evaluation.",
  ],
  output_guardrail: [
    "Run output Tier-2 scans asynchronously when policy allows.",
    "Reduce output length limits to shrink scan surface.",
  ],
};

export function resolveLatencyHints(sources = {}) {
  const trace = resolvePipelineTrace(sources);
  if (!trace) return [];

  const backendHints = trace.latency_breakdown?.hints;
  if (Array.isArray(backendHints) && backendHints.length) {
    return backendHints.filter((h) => h && typeof h === "object");
  }

  const total = resolveTotalLatencyMs(sources) ?? 0;
  const byStage = computeByStage(trace, total);
  if (!byStage.length || !total) return [];

  const top = byStage[0];
  if (!top || top.latency_ms < 5 || top.share_pct < 10) return [];

  const actions = STAGE_REDUCTION_HINTS[top.stage] || [
    `Investigate why ${String(top.stage).replace(/_/g, " ")} is the slowest pipeline stage.`,
  ];
  const label = STAGE_LABELS[top.stage] || String(top.stage).replace(/_/g, " ");
  return [{
    stage: top.stage,
    severity: top.share_pct >= 40 ? "high" : "medium",
    message: `${label} took ${top.latency_ms}ms (${Math.round(top.share_pct)}% of total).`,
    actions,
  }];
}

export function resolveLatencyBreakdown(sources = {}) {
  const trace = resolvePipelineTrace(sources);
  if (!trace) return null;
  let stageSum = finiteMs(trace.stage_latency_sum_ms);
  let overhead = finiteMs(trace.overhead_ms);
  const total = resolveTotalLatencyMs(sources);

  if (stageSum == null && Array.isArray(trace.stages) && trace.stages.length) {
    stageSum = Math.round(
      trace.stages.reduce((acc, s) => acc + (finiteMs(s?.latency_ms) ?? 0), 0) * 10,
    ) / 10;
  }
  if (overhead == null && total != null && stageSum != null) {
    overhead = Math.round(Math.max(0, total - stageSum) * 10) / 10;
  }

  const backend = trace.latency_breakdown && typeof trace.latency_breakdown === "object"
    ? trace.latency_breakdown
    : null;

  if (stageSum == null && overhead == null && !backend && !(trace.stages?.length)) {
    return null;
  }

  return {
    total_latency_ms: total,
    stage_latency_sum_ms: stageSum,
    overhead_ms: overhead,
    dominant_stage: backend?.dominant_stage || null,
    dominant_latency_ms: finiteMs(backend?.dominant_latency_ms),
    dominant_share_pct: finiteMs(backend?.dominant_share_pct),
    by_stage: Array.isArray(backend?.by_stage) && backend.by_stage.length
      ? backend.by_stage
      : computeByStage(trace, total),
    hints: resolveLatencyHints({ ...sources, pipelineTrace: trace }),
  };
}

export function formatDominantStageLabel(stage) {
  if (!stage) return "";
  return STAGE_LABELS[stage] || String(stage).replace(/_/g, " ");
}

export const ROUTE_DESTINATION_LABELS = {
  llm: "LLM inference",
  rag: "RAG retrieval",
  vector_db: "Vector DB",
  mcp: "MCP tool",
};

export function formatRouteDestination(dest) {
  const key = String(dest || "llm").trim().toLowerCase();
  return ROUTE_DESTINATION_LABELS[key] || key.replace(/_/g, " ");
}

export function resolveRoutingDecision(sources = {}) {
  const trace = resolvePipelineTrace(sources);
  if (!trace) return null;

  const root = trace.routing && typeof trace.routing === "object" ? trace.routing : {};
  const stage = Array.isArray(trace.stages)
    ? trace.stages.find((s) => (s?.name || s?.stage) === "model_routing")
    : null;
  const pick = (key) => (
    root[key]
    ?? stage?.[key]
    ?? trace[key]
    ?? null
  );

  const requested = pick("requested_model") || pick("original_model") || "";
  const routed = pick("routed_model") || pick("selected_model") || "";
  const destination = pick("route_destination") || "llm";

  if (!requested && !routed && !pick("routing_reason")) return null;

  return {
    requested_model: requested,
    selected_model: pick("selected_model") || routed,
    routed_model: routed,
    route_destination: destination,
    route_destination_label: pick("route_destination_label") || formatRouteDestination(destination),
    routing_reason: pick("routing_reason") || "",
    decision_source: pick("decision_source") || "",
    decision_source_label: pick("decision_source_label") || "",
    policy_summary: pick("policy_summary") || "",
    decision_factors: Array.isArray(pick("decision_factors")) ? pick("decision_factors") : [],
    weights: pick("weights") && typeof pick("weights") === "object" ? pick("weights") : {},
    routing_score: pick("routing_score") ?? 0,
    candidate_count: pick("candidate_count") ?? 0,
    fallback_chain: Array.isArray(pick("fallback_chain")) ? pick("fallback_chain") : [],
    evaluator_model: pick("evaluator_model") || "",
    guard_reason: stage?.guard_reason || "",
    rerouted: root.rerouted || stage?.rerouted,
  };
}

export function resolvePipelineInputOutput(sources = {}) {
  const meta = sources.meta || {};
  const extra = meta.extra || {};
  const trace = resolvePipelineTrace({ ...sources, meta, extra });
  const finalAction = String(
    trace?.final_action || extractFinalAction(sources, sources.fallbackAction || "allow"),
  ).toLowerCase();

  const inputText = trace?.input_text
    || trace?.prompt_preview
    || meta?.input_text
    || extra?.input_text
    || meta?.prompt_submitted
    || meta?.prompt_snippet
    || extra?.prompt_submitted
    || extra?.prompt_snippet
    || sources.promptText
    || "";

  const promptSubmitted = trace?.prompt_submitted
    || meta?.prompt_submitted
    || extra?.prompt_submitted
    || inputText;

  const outputWithheld = Boolean(trace?.output_withheld)
    || (finalAction === "block" && !(trace?.output_text || trace?.final_response));

  const outputWithheldReason = trace?.output_withheld_reason
    || (outputWithheld ? "[Response blocked — not delivered to client]" : "");

  const withheldPreview = trace?.output_text
    ?? trace?.final_response
    ?? meta?.raw_output
    ?? extra?.raw_output
    ?? meta?.response_snippet
    ?? extra?.response_snippet
    ?? meta?.sanitized_output
    ?? extra?.sanitized_output
    ?? sources.responseText
    ?? "";

  const outputText = outputWithheld
    ? outputWithheldReason
    : (withheldPreview || "");

  const inputWasRedacted = Boolean(trace?.input_was_redacted)
    || (promptSubmitted && inputText && promptSubmitted !== inputText);

  const inputBefore = trace?.input_text_before || (inputWasRedacted ? inputText : "");
  const inputAfter = trace?.input_text_after || (inputWasRedacted ? promptSubmitted : "");

  return {
    inputText,
    promptSubmitted,
    outputText,
    outputWithheld,
    outputWithheldReason,
    outputWithheldPreview: outputWithheld ? withheldPreview : "",
    inputWasRedacted,
    inputBefore,
    inputAfter,
    finalAction,
    fromTrace: Boolean(trace?.input_text || trace?.output_text != null),
  };
}
