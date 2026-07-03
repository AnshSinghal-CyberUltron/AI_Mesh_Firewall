import { SafeResponsiveChart } from "./SafeResponsiveChart";
import { resolveTotalLatencyMs } from "../utils/pipelineTrace";

// ── ECharts option builders (replace recharts; the registered zs-light/zs-dark
// theme drives axis/grid/tooltip/legend colors). Only charts backed by REAL
// per-scan data are built — the previous factory synthesized multi-point
// breakdowns from single scalars (latency×0.3/0.5, prompt.length×ratios,
// similarity decay curves, 2-point "trends"), which is fabricated analysis and
// has been removed. Builders with no real chart return { charts: [] } and the
// "Module-Specific Scan Analysis" card hides.
function logBar(data, xKey, series, opts = {}) {
  const multi = series.length > 1;
  const rotate = opts.rotate || 0;
  return {
    grid: { top: multi ? 28 : 12, right: 14, bottom: rotate ? 64 : 26, left: 46 },
    tooltip: { trigger: "axis", axisPointer: { type: "shadow" } },
    ...(multi ? { legend: { top: 0, itemHeight: 8, itemWidth: 12, textStyle: { fontSize: 10 } } } : {}),
    xAxis: { type: "category", data: data.map((d) => d[xKey]), axisLabel: { fontSize: 10, rotate, interval: 0 } },
    yAxis: { type: "value", ...(opts.max != null ? { max: opts.max } : {}), axisLabel: { fontSize: 11 } },
    series: series.map((s) => ({
      name: s.name,
      type: "bar",
      ...(s.stack ? { stack: "a" } : {}),
      itemStyle: { color: s.color, ...(s.stack ? {} : { borderRadius: [3, 3, 0, 0] }) },
      data: data.map((d) => d[s.key]),
    })),
  };
}

function logHBar(data, catKey, valKey, color, name) {
  return {
    grid: { top: 12, right: 18, bottom: 24, left: 92 },
    tooltip: { trigger: "axis", axisPointer: { type: "shadow" } },
    xAxis: { type: "value", axisLabel: { fontSize: 11 } },
    yAxis: { type: "category", data: data.map((d) => d[catKey]), axisLabel: { fontSize: 10 } },
    series: [{ name, type: "bar", itemStyle: { color, borderRadius: [0, 3, 3, 0] }, data: data.map((d) => d[valKey]) }],
  };
}

function logPie(data, nameKey, valKey, suffix = "") {
  return {
    tooltip: { trigger: "item", formatter: `{b}: {c}${suffix} ({d}%)` },
    legend: { bottom: 0, itemHeight: 8, itemWidth: 12, textStyle: { fontSize: 10 } },
    series: [{
      type: "pie",
      radius: "66%",
      center: ["50%", "44%"],
      avoidLabelOverlap: true,
      label: { formatter: `{b}: {c}${suffix}`, fontSize: 11 },
      labelLine: { length: 6, length2: 6 },
      data: data.map((d) => ({ name: d[nameKey], value: d[valKey], itemStyle: { color: d.color } })),
    }],
  };
}

function chart(title, option) {
  return { title, component: <SafeResponsiveChart className="h-[250px] w-full" option={option} /> };
}

export function getModuleLogCharts(logData) {
  const moduleId = detectModuleId(logData);
  let built;
  switch (moduleId) {
    case "1.1": built = get11LogCharts(logData); break;
    case "1.2": built = get12LogCharts(logData); break;
    case "1.3": built = get13LogCharts(logData); break;
    case "1.4": built = get14LogCharts(logData); break;
    case "1.5": built = get15LogCharts(logData); break;
    case "1.6": built = get16LogCharts(logData); break;
    case "1.7": built = get17LogCharts(logData); break;
    default: built = getDefaultLogCharts(logData);
  }
  // DYNAMIC: only surface charts backed by real data. A builder marks a chart
  // `empty: true` when the underlying scan has no data for it (e.g. RAG-pipeline
  // charts on a non-RAG request) — those are dropped here, and the whole
  // "Module-Specific Scan Analysis" card hides when nothing real is left.
  const charts = (built?.charts || []).filter((c) => c && !c.empty);
  return { ...built, charts };
}

// Classify a scan by its REAL nature (endpoint / event type / stage) BEFORE
// trusting a possibly-coarse module_id label — the gateway tags chat requests
// with module_id "1.2", whose chart builder draws the RAG pipeline. Routing by
// endpoint keeps a non-RAG request off the RAG charts entirely.
function detectModuleId(logData) {
  const m = logData?.metadata || {};
  const extra = m?.extra || {};
  const endpoint = String(m.endpoint || extra.endpoint || logData?.endpoint || "").toLowerCase();
  const eventType = String(m.event_type || extra.event_type || "").toLowerCase();
  const stage = String(m.pipeline_stage || extra.pipeline_stage || logData?.stage || "").toLowerCase();
  const moduleHint = String(logData?.module || m.module || m.module_id || "").toLowerCase();
  const sourceHint = String(logData?.source || m.source || "").toLowerCase();

  // 1) Authoritative nature-of-scan signals. RAG/vector is the ONLY thing that
  //    should render the RAG pipeline (query/retriever/ranker/generator) charts.
  if (eventType.startsWith("rag") || eventType.includes("vector") ||
      endpoint.includes("/rag") || endpoint.includes("/embedding") ||
      ["rag", "query", "retriever", "ranker", "generator"].includes(stage)) return "1.3";
  // Specific event types win over the generic chat-endpoint fallback below —
  // an output_guard / model_routed / kill_switch event rides a /chat endpoint
  // but is NOT a 1.1 ingress scan.
  if (eventType.startsWith("output") || eventType.includes("guard")) return "1.7";
  if (eventType.includes("kill") || eventType.includes("isolation") || eventType.includes("model_state")) return "1.6";
  if (eventType === "model_routed") return "1.5";
  if (endpoint.includes("/mcp")) return "1.4";
  // Chat / responses / completions ingress → real token + latency charts, not RAG.
  if (endpoint.includes("/chat") || endpoint.includes("/responses") || endpoint.includes("/completion") ||
      eventType === "request" || eventType === "stream_complete" || eventType === "input_blocked") return "1.1";

  // 2) Explicit module label as a fallback (e.g. seeded/simulated rows).
  const mm = moduleHint.match(/^1\.[1-7]/);
  if (mm) return mm[0];

  // 3) Source / shape hints (legacy).
  if (sourceHint.includes("rag") || sourceHint.includes("vector")) return "1.3";
  if (sourceHint.includes("mcp") || sourceHint.includes("context")) return "1.4";
  if (sourceHint.includes("output")) return "1.7";
  if (sourceHint.includes("kill") || sourceHint.includes("isolation")) return "1.6";
  if (sourceHint.includes("route")) return "1.5";
  if (logData?.collection || logData?.similarity) return "1.3";
  if (logData?.requested || logData?.routed) return "1.5";
  if (logData?.guardrail) return "1.7";

  // 4) Default to traffic ingress (real tokens/latency) — never RAG.
  return "1.1";
}

function get11LogCharts(logData) {
  const metadata = logData?.metadata || {};
  const tokenUsageMeta = metadata?.tokens_used || {};
  const inputTokens = parseNumeric(tokenUsageMeta.prompt) || parseNumeric(logData.tokens) || parseNumeric(logData.tokensUsed);
  const outputTokens = parseNumeric(tokenUsageMeta.completion);
  const totalTokens = inputTokens + outputTokens;
  // REAL per-stage latency from the pipeline trace (same data as the Pipeline
  // Stages section) — never fabricate a latency breakdown from the total.
  const ptStages = (metadata?.extra?.pipeline_trace?.stages || metadata?.pipeline_trace?.stages || logData?.pipeline_trace?.stages || []);
  const realTimeline = (Array.isArray(ptStages) ? ptStages : [])
    .map((s) => ({ step: String(s?.name || "").replace(/_/g, " "), latency: Math.round(parseNumeric(s?.latency_ms)) }))
    .filter((s) => s.step);
  // Real token counts only (guarded below by totalTokens <= 0). No synthetic
  // 0.6/0.4 split of a request size when the real breakdown is missing.
  const tokenUsage = [
    { type: "Input", tokens: inputTokens, color: "#14b8a6" },
    { type: "Output", tokens: outputTokens, color: "#8b5cf6" },
  ];
  return {
    charts: [
      {
        // Real per-stage latency (from the pipeline trace). Hidden when the
        // scan carries no trace — no synthetic latency breakdown.
        empty: realTimeline.length === 0,
        ...chart("Pipeline Stage Latency", logBar(realTimeline, "step", [{ key: "latency", name: "Latency (ms)", color: "#14b8a6" }], { rotate: realTimeline.length > 3 ? 30 : 0 })),
      },
      {
        empty: totalTokens <= 0,
        ...chart("Token Usage Distribution", logPie(tokenUsage, "type", "tokens")),
      },
    ],
  };
}

function get12LogCharts(logData) {
  const metadata = logData?.metadata || {};
  const audit = metadata?.pipeline_audit || {};
  const auditStages = audit?.stages || {};
  const hasAudit = Object.keys(auditStages).length > 0;

  const detected = logData.action === "block" || logData.action === "redact" ? 1 : 0;
  const totalLatency = resolveTotalLatencyMs({
    pipelineTrace: metadata?.pipeline_trace || metadata?.extra?.pipeline_trace || logData?.pipeline_trace,
    meta: metadata,
    logData,
  }) || parseNumeric(metadata.latency_ms) || parseNumeric(logData.duration) || 0;
  const riskScore = parseNumeric(metadata.security_risk_score) || Math.round((parseNumeric(metadata.risk_score) || 0) * 100);
  const stageHint = String(metadata.pipeline_stage || logData.stage || "").toLowerCase();
  const hasStageData = !!stageHint;
  const baseThroughput = Math.max(1, 100 - Math.min(95, Math.round(riskScore * 0.6)));
  const stages = ["query", "retriever", "ranker", "generator"];

  // Use real pipeline_audit data when available, otherwise fall back to synthetic
  const pipelineStages = hasAudit
    ? stages.map((s) => {
        const rec = auditStages[s] || {};
        return {
          stage: s.charAt(0).toUpperCase() + s.slice(1),
          latency: Math.round(parseNumeric(rec.duration_ms) || 0),
          docsIn: parseNumeric(rec.document_count_in) || 0,
          docsOut: parseNumeric(rec.document_count_out) || 0,
          action: rec.action || "allow",
        };
      })
    : stages.map((s) => ({
        stage: s.charAt(0).toUpperCase() + s.slice(1),
        latency: s === stageHint ? Math.max(1, totalLatency) : 0,
        docsIn: 0,
        docsOut: 0,
        action: s === stageHint && detected ? "block" : "allow",
      }));

  const detectionFlow = hasAudit
    ? stages.map((s) => {
        const rec = auditStages[s] || {};
        const act = (rec.action || "allow").toLowerCase();
        return {
          stage: s.charAt(0).toUpperCase() + s.slice(1),
          blocked: act === "block" ? 1 : 0,
          flagged: act === "flag" || act === "rewrite" ? 1 : 0,
          passed: act === "allow" ? 1 : 0,
        };
      })
    : stages.map((s) => ({
        stage: s.charAt(0).toUpperCase() + s.slice(1),
        blocked: s === stageHint ? detected : 0,
        flagged: 0,
        passed: s === stageHint ? (detected ? 0 : 1) : (hasStageData ? 1 : 0),
      }));

  const docFunnel = hasAudit
    ? stages.map((s) => {
        const rec = auditStages[s] || {};
        return {
          stage: s.charAt(0).toUpperCase() + s.slice(1),
          documents: parseNumeric(rec.document_count_out) || parseNumeric(rec.document_count_in) || 0,
        };
      })
    : stages.map((s) => ({
        stage: s.charAt(0).toUpperCase() + s.slice(1),
        documents: s === stageHint ? baseThroughput : (hasStageData ? 100 : 0),
      }));

  // RAG/vector pipeline charts are only meaningful with REAL stage data
  // (pipeline_audit, or at least a pipeline_stage hint). A plain chat/policy
  // request has neither — so mark them empty and they get dropped (the card
  // hides) instead of rendering an empty "no stage data" RAG pipeline.
  // Only render these RAG-pipeline charts from REAL pipeline_audit data. The
  // prior non-audit fallback synthesized single-stage latency + a placeholder
  // "documents: 100" — fabricated, so it is dropped (chart hidden) here.
  const ragEmpty = !hasAudit;
  return {
    charts: [
      {
        empty: ragEmpty,
        ...chart("Pipeline Stage Latency (real audit)", logBar(pipelineStages, "stage", [{ key: "latency", name: "Latency (ms)", color: "#8b5cf6" }])),
      },
      {
        empty: ragEmpty,
        ...chart("Stage Verdicts (real audit)", logBar(detectionFlow, "stage", [
          { key: "blocked", name: "Blocked", color: "#ef4444", stack: true },
          { key: "flagged", name: "Flagged/Rewritten", color: "#f59e0b", stack: true },
          { key: "passed", name: "Passed", color: "#10b981", stack: true },
        ])),
      },
      {
        empty: ragEmpty,
        ...chart("Document Filtering Funnel (real audit)", logHBar(docFunnel, "stage", "documents", "#14b8a6", "Documents")),
      },
    ],
  };
}

// RAG/vector logs (1.3): the prior charts (per-doc similarity DECAY CURVE from
// one value, "time breakdown" latency×0.3/0.5, radar derived from scalars) were
// all synthesized — no real per-scan array exists, so no chart is drawn.
function get13LogCharts() {
  return { charts: [] };
}

function get14LogCharts(logData) {
  const metadata = logData?.metadata || {};
  const tags = Array.isArray(metadata.compliance_tags) ? metadata.compliance_tags : [];
  const matched = Array.isArray(metadata.matched_patterns) ? metadata.matched_patterns : [];
  const redactions = parseNumeric(logData.redactions) || parseNumeric(metadata.redaction_count) || 0;
  // Real detection counts only. The prior "Context Field Sizes" (prompt.length
  // split 0.15/0.45/0.3/0.1) and "Assembly Timeline" (latency splits) were
  // fabricated from a single scalar and have been removed.
  const piiDetections = [
    { type: "Matched Patterns", count: matched.length, color: "#ef4444" },
    { type: "Compliance Tags", count: tags.length, color: "#f59e0b" },
    { type: "Redactions", count: redactions, color: "#8b5cf6" },
  ];
  return {
    charts: [
      {
        empty: matched.length + tags.length + redactions === 0,
        ...chart("PII Detections by Type", logPie(piiDetections, "type", "count")),
      },
    ],
  };
}

// Routing logs (1.5): routing radar, cost from tokens/100000, and a latency-split
// pie were all synthetic. No real per-scan chart data.
function get15LogCharts() {
  return { charts: [] };
}

// Isolation / kill-switch logs (1.6): a 2-point risk "trend" (risk×0.65 baseline),
// a derived health radar, and a latency-split timeline were synthetic. No chart.
function get16LogCharts() {
  return { charts: [] };
}

// Output-guard logs (1.7): "confidence" from count/10, a content-analysis pie
// hardcoded by action (allow?80:35), and a latency-split pipeline were synthetic.
function get17LogCharts() {
  return { charts: [] };
}

// Default: a single [{ step: 1, value }] point is not a timeline. No chart.
function getDefaultLogCharts() {
  return { charts: [] };
}
function parseNumeric(value) {
  if (typeof value === "number") return value;
  if (typeof value !== "string") return 0;
  const parsed = Number.parseFloat(value.replace(/[^0-9.]/g, ""));
  return Number.isFinite(parsed) ? parsed : 0;
}
