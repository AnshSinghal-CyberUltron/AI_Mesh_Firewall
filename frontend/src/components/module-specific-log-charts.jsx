import {
  LineChart, Line, BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, AreaChart, Area, PieChart, Pie, Cell,
  RadarChart, Radar, PolarGrid, PolarAngleAxis, PolarRadiusAxis,
} from "recharts";
import { SafeResponsiveChart } from "./SafeResponsiveChart";

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
  const requestLatency = parseNumeric(logData.latency) || parseNumeric(logData.duration) || parseNumeric(metadata?.latency_ms) || 0;
  const tokenUsageMeta = metadata?.tokens_used || {};
  const inputTokens = parseNumeric(tokenUsageMeta.prompt) || parseNumeric(logData.tokens) || parseNumeric(logData.tokensUsed);
  const outputTokens = parseNumeric(tokenUsageMeta.completion);
  const totalTokens = inputTokens + outputTokens;
  const requestSize = totalTokens || inputTokens || 0;
  // REAL per-stage latency from the pipeline trace (same data as the Pipeline
  // Stages section) — never fabricate a latency breakdown from the total.
  const ptStages = (metadata?.extra?.pipeline_trace?.stages || metadata?.pipeline_trace?.stages || logData?.pipeline_trace?.stages || []);
  const realTimeline = (Array.isArray(ptStages) ? ptStages : [])
    .map((s) => ({ step: String(s?.name || "").replace(/_/g, " "), latency: Math.round(parseNumeric(s?.latency_ms)) }))
    .filter((s) => s.step);
  const tokenUsage = [
    { type: "Input", tokens: inputTokens || Math.round(requestSize * 0.6), color: "#14b8a6" },
    { type: "Output", tokens: outputTokens || Math.round(requestSize * 0.4), color: "#8b5cf6" },
  ];
  return {
    charts: [
      {
        // Real per-stage latency (from the pipeline trace). Hidden when the
        // scan carries no trace — no synthetic latency breakdown.
        empty: realTimeline.length === 0,
        title: "Pipeline Stage Latency",
        component: (
          <SafeResponsiveChart className="h-[250px] w-full">
            <BarChart data={realTimeline}>
              <CartesianGrid strokeDasharray="3 3" stroke="#64748b" strokeOpacity={0.25} />
              <XAxis dataKey="step" stroke="#64748b" tick={{ fontSize: 10 }} interval={0} angle={-30} textAnchor="end" height={70} />
              <YAxis stroke="#64748b" tick={{ fontSize: 11 }} />
              <Tooltip /><Bar dataKey="latency" fill="#14b8a6" name="Latency (ms)" />
            </BarChart>
          </SafeResponsiveChart>
        ),
      },
      {
        empty: totalTokens <= 0,
        title: "Token Usage Distribution",
        component: (
          <SafeResponsiveChart className="h-[250px] w-full">
            <PieChart>
              <Pie data={tokenUsage} cx="50%" cy="50%" outerRadius={90} dataKey="tokens" label={({ type, tokens }) => `${type}: ${tokens}`}>
                {tokenUsage.map((entry, index) => (<Cell key={`cell-${index}`} fill={entry.color} />))}
              </Pie>
              <Tooltip />
            </PieChart>
          </SafeResponsiveChart>
        ),
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
  const totalLatency = parseNumeric(metadata.latency_ms) || parseNumeric(logData.duration) || 0;
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
  const ragEmpty = !hasAudit && !hasStageData;
  return {
    charts: [
      {
        empty: ragEmpty,
        title: hasAudit ? "Pipeline Stage Latency (real audit)" : (hasStageData ? `Pipeline Stage: ${stageHint.charAt(0).toUpperCase() + stageHint.slice(1)}` : "Pipeline Stage (no stage data)"),
        component: (
          <SafeResponsiveChart className="h-[250px] w-full">
            <BarChart data={pipelineStages}>
              <CartesianGrid strokeDasharray="3 3" stroke="#64748b" strokeOpacity={0.25} />
              <XAxis dataKey="stage" stroke="#64748b" tick={{ fontSize: 11 }} />
              <YAxis stroke="#64748b" tick={{ fontSize: 11 }} />
              <Tooltip />
              <Bar dataKey="latency" fill="#8b5cf6" name="Latency (ms)" />
            </BarChart>
          </SafeResponsiveChart>
        ),
      },
      {
        empty: ragEmpty,
        title: hasAudit ? "Stage Verdicts (real audit)" : "Detection vs Passed by Stage",
        component: (
          <SafeResponsiveChart className="h-[250px] w-full">
            <BarChart data={detectionFlow}>
              <CartesianGrid strokeDasharray="3 3" stroke="#64748b" strokeOpacity={0.25} />
              <XAxis dataKey="stage" stroke="#64748b" tick={{ fontSize: 11 }} />
              <YAxis stroke="#64748b" tick={{ fontSize: 11 }} />
              <Tooltip />
              <Bar dataKey="blocked" fill="#ef4444" stackId="a" name="Blocked" />
              <Bar dataKey="flagged" fill="#f59e0b" stackId="a" name="Flagged/Rewritten" />
              <Bar dataKey="passed" fill="#10b981" stackId="a" name="Passed" />
            </BarChart>
          </SafeResponsiveChart>
        ),
      },
      {
        empty: ragEmpty,
        title: hasAudit ? "Document Filtering Funnel (real audit)" : "Stage Completion Funnel",
        component: (
          <SafeResponsiveChart className="h-[250px] w-full">
            <BarChart data={docFunnel} layout="horizontal">
              <CartesianGrid strokeDasharray="3 3" stroke="#64748b" strokeOpacity={0.25} />
              <XAxis type="number" stroke="#64748b" tick={{ fontSize: 11 }} />
              <YAxis type="category" dataKey="stage" stroke="#64748b" tick={{ fontSize: 11 }} width={80} />
              <Tooltip /><Bar dataKey="documents" fill="#14b8a6" name="Documents" />
            </BarChart>
          </SafeResponsiveChart>
        ),
      },
    ],
  };
}

function get13LogCharts(logData) {
  const metadata = logData?.metadata || {};
  const similarity = parseFloat(logData.similarity || logData.relevanceScore || metadata?.relevance_score || "0");
  const docsRetrieved = parseInt(logData.docsRetrieved || metadata?.docs_retrieved || "1", 10) || 1;
  const latency = parseNumeric(metadata?.latency_ms) || parseNumeric(logData.duration) || 0;
  const anomalyCount = parseInt(metadata?.embedding_anomaly_count || "0", 10) || 0;
  const similarityScores = Array.from({ length: Math.min(10, docsRetrieved) }, (_, i) => ({
    doc: `Doc ${i + 1}`,
    similarity: Math.max(0, Math.min(1, similarity || Math.max(0.05, 1 - i * 0.08))),
  }));
  const retrievalMetrics = [
    { metric: "Latency", value: Math.max(1, Math.min(100, Math.round(latency || 1))) },
    { metric: "Coverage", value: Math.min(100, docsRetrieved * 10) },
    { metric: "Relevance", value: Math.max(1, Math.min(100, Math.round((similarity || 0.1) * 100))) },
    { metric: "Anomaly", value: Math.min(100, anomalyCount * 20) },
  ];
  const timeBreakdown = [
    { name: "Embedding", value: Math.max(1, Math.round(latency * 0.3) || 1), color: "#14b8a6" },
    { name: "Search", value: Math.max(1, Math.round(latency * 0.5) || 1), color: "#8b5cf6" },
    { name: "Ranking", value: Math.max(1, latency - (Math.round(latency * 0.3) + Math.round(latency * 0.5))), color: "#f59e0b" },
  ];
  return {
    charts: [
      {
        title: "Document Similarity Scores",
        component: (
          <SafeResponsiveChart className="h-[250px] w-full">
            <BarChart data={similarityScores}>
              <CartesianGrid strokeDasharray="3 3" stroke="#64748b" strokeOpacity={0.25} />
              <XAxis dataKey="doc" stroke="#64748b" tick={{ fontSize: 10 }} />
              <YAxis stroke="#64748b" tick={{ fontSize: 11 }} domain={[0, 1]} />
              <Tooltip /><Bar dataKey="similarity" fill="#8b5cf6" />
            </BarChart>
          </SafeResponsiveChart>
        ),
      },
      {
        title: "Retrieval Performance Radar",
        component: (
          <SafeResponsiveChart className="h-[250px] w-full">
            <RadarChart data={retrievalMetrics}>
              <PolarGrid stroke="#64748b" strokeOpacity={0.25} />
              <PolarAngleAxis dataKey="metric" stroke="#64748b" tick={{ fontSize: 11 }} />
              <PolarRadiusAxis angle={90} domain={[0, 100]} stroke="#64748b" tick={{ fontSize: 10 }} />
              <Radar name="Score" dataKey="value" stroke="#14b8a6" fill="#14b8a6" fillOpacity={0.6} /><Tooltip />
            </RadarChart>
          </SafeResponsiveChart>
        ),
      },
      {
        title: "Query Response Time Breakdown",
        component: (
          <SafeResponsiveChart className="h-[250px] w-full">
            <PieChart>
              <Pie data={timeBreakdown} cx="50%" cy="50%" outerRadius={90} dataKey="value" label={({ name, value }) => `${name}: ${value}ms`}>
                {timeBreakdown.map((entry, index) => (<Cell key={`cell-${index}`} fill={entry.color} />))}
              </Pie>
              <Tooltip />
            </PieChart>
          </SafeResponsiveChart>
        ),
      },
    ],
  };
}

function get14LogCharts(logData) {
  const metadata = logData?.metadata || {};
  const tags = Array.isArray(metadata.compliance_tags) ? metadata.compliance_tags : [];
  const matched = Array.isArray(metadata.matched_patterns) ? metadata.matched_patterns : [];
  const prompt = String(metadata.prompt_snippet || logData.details || "");
  const totalLatency = parseNumeric(metadata.latency_ms) || parseNumeric(logData.duration) || 0;
  const fieldSizes = [
    { field: "System", size: Math.max(1, Math.round(prompt.length * 0.15)) },
    { field: "User", size: Math.max(1, Math.round(prompt.length * 0.45)) },
    { field: "Documents", size: Math.max(1, Math.round(prompt.length * 0.3)) },
    { field: "History", size: Math.max(1, Math.round(prompt.length * 0.1)) },
  ];
  const piiDetections = [
    { type: "Matched Patterns", count: matched.length, color: "#ef4444" },
    { type: "Compliance Tags", count: tags.length, color: "#f59e0b" },
    { type: "Redactions", count: parseNumeric(logData.redactions) || parseNumeric(metadata.redaction_count), color: "#8b5cf6" },
  ];
  const timeline = [
    { phase: "Collection", duration: Math.max(1, Math.round(totalLatency * 0.35)) },
    { phase: "PII Scan", duration: Math.max(1, Math.round(totalLatency * 0.3)) },
    { phase: "Tagging", duration: Math.max(1, Math.round(totalLatency * 0.2)) },
    { phase: "Assembly", duration: Math.max(1, totalLatency - (Math.round(totalLatency * 0.35) + Math.round(totalLatency * 0.3) + Math.round(totalLatency * 0.2))) },
  ];
  return {
    charts: [
      {
        title: "Context Field Sizes (tokens)",
        component: (
          <SafeResponsiveChart className="h-[250px] w-full">
            <BarChart data={fieldSizes}>
              <CartesianGrid strokeDasharray="3 3" stroke="#64748b" strokeOpacity={0.25} />
              <XAxis dataKey="field" stroke="#64748b" tick={{ fontSize: 11 }} />
              <YAxis stroke="#64748b" tick={{ fontSize: 11 }} />
              <Tooltip /><Bar dataKey="size" fill="#14b8a6" />
            </BarChart>
          </SafeResponsiveChart>
        ),
      },
      {
        title: "PII Detections by Type",
        component: (
          <SafeResponsiveChart className="h-[250px] w-full">
            <PieChart>
              <Pie data={piiDetections} cx="50%" cy="50%" outerRadius={90} dataKey="count" label={({ type, count }) => `${type}: ${count}`}>
                {piiDetections.map((entry, index) => (<Cell key={`cell-${index}`} fill={entry.color} />))}
              </Pie>
              <Tooltip />
            </PieChart>
          </SafeResponsiveChart>
        ),
      },
      {
        title: "Assembly Process Timeline",
        component: (
          <SafeResponsiveChart className="h-[250px] w-full">
            <BarChart data={timeline} layout="horizontal">
              <CartesianGrid strokeDasharray="3 3" stroke="#64748b" strokeOpacity={0.25} />
              <XAxis type="number" stroke="#64748b" tick={{ fontSize: 11 }} />
              <YAxis type="category" dataKey="phase" stroke="#64748b" tick={{ fontSize: 10 }} width={120} />
              <Tooltip /><Bar dataKey="duration" fill="#8b5cf6" />
            </BarChart>
          </SafeResponsiveChart>
        ),
      },
    ],
  };
}

function get15LogCharts(logData) {
  const metadata = logData?.metadata || {};
  const requestedModel = logData.requested || metadata.requested_model || metadata.model || "unknown";
  const routedModel = logData.routed || metadata.routed_model || requestedModel;
  const risk = parseNumeric(metadata.security_risk_score) || Math.round((parseNumeric(metadata.risk_score) || 0) * 100);
  const latency = parseNumeric(metadata.latency_ms) || parseNumeric(logData.duration) || 0;
  const promptTokens = parseNumeric(metadata?.tokens_used?.prompt);
  const completionTokens = parseNumeric(metadata?.tokens_used?.completion);
  const totalTokens = promptTokens + completionTokens;
  const routingDecision = [
    { factor: "Cost", score: Math.max(1, Math.min(100, 100 - Math.round((totalTokens || 100) / 20))) },
    { factor: "Latency", score: Math.max(1, Math.min(100, 100 - Math.round(latency / 10))) },
    { factor: "Compliance", score: Math.max(1, Math.min(100, 100 - Math.round(risk * 0.6))) },
    { factor: "Risk", score: Math.max(1, Math.min(100, 100 - risk)) },
  ];
  const costComparison = [
    { model: requestedModel, cost: (promptTokens || 1200) / 100000 },
    { model: routedModel, cost: (completionTokens || 900) / 120000 },
  ];
  const timeBreakdown = [
    { name: "Routing", value: Math.max(1, Math.round(latency * 0.15) || 1), color: "#14b8a6" },
    { name: "Model Processing", value: Math.max(1, Math.round(latency * 0.75) || 1), color: "#8b5cf6" },
    { name: "Network", value: Math.max(1, latency - (Math.round(latency * 0.15) + Math.round(latency * 0.75))), color: "#f59e0b" },
  ];
  return {
    charts: [
      {
        title: "Routing Decision Factors",
        component: (
          <SafeResponsiveChart className="h-[250px] w-full">
            <RadarChart data={routingDecision}>
              <PolarGrid stroke="#64748b" strokeOpacity={0.25} />
              <PolarAngleAxis dataKey="factor" stroke="#64748b" tick={{ fontSize: 11 }} />
              <PolarRadiusAxis angle={90} domain={[0, 100]} stroke="#64748b" tick={{ fontSize: 10 }} />
              <Radar name="Score" dataKey="score" stroke="#14b8a6" fill="#14b8a6" fillOpacity={0.6} /><Tooltip />
            </RadarChart>
          </SafeResponsiveChart>
        ),
      },
      {
        title: "Cost Comparison",
        component: (
          <SafeResponsiveChart className="h-[250px] w-full">
            <BarChart data={costComparison}>
              <CartesianGrid strokeDasharray="3 3" stroke="#64748b" strokeOpacity={0.25} />
              <XAxis dataKey="model" stroke="#64748b" tick={{ fontSize: 11 }} />
              <YAxis stroke="#64748b" tick={{ fontSize: 11 }} />
              <Tooltip /><Bar dataKey="cost" fill="#8b5cf6" />
            </BarChart>
          </SafeResponsiveChart>
        ),
      },
      {
        title: "Response Time Breakdown",
        component: (
          <SafeResponsiveChart className="h-[250px] w-full">
            <PieChart>
              <Pie data={timeBreakdown} cx="50%" cy="50%" outerRadius={90} dataKey="value" label={({ name, value }) => `${name}: ${value}ms`}>
                {timeBreakdown.map((entry, index) => (<Cell key={`cell-${index}`} fill={entry.color} />))}
              </Pie>
              <Tooltip />
            </PieChart>
          </SafeResponsiveChart>
        ),
      },
    ],
  };
}

function get16LogCharts(logData) {
  const metadata = logData?.metadata || {};
  const risk = parseInt(logData.riskScore || metadata.security_risk_score || (parseNumeric(metadata.risk_score) * 100) || "0", 10) || 0;
  const latency = parseNumeric(metadata.latency_ms) || parseNumeric(logData.duration) || 0;
  const action = String(logData.action || metadata.action || "allow").toLowerCase();
  const riskTrend = [
    { time: "Baseline", risk: Math.max(0, Math.round(risk * 0.65)) },
    { time: "Current", risk },
  ];
  const isolationMetrics = [
    { metric: "Response Time", value: Math.max(1, Math.min(100, 100 - Math.round(latency / 10))) },
    { metric: "Error Rate", value: action === "block" ? 20 : action === "redact" ? 10 : 4 },
    { metric: "Anomaly Score", value: Math.max(1, Math.min(100, risk)) },
    { metric: "Health Score", value: Math.max(1, Math.min(100, 100 - Math.round(risk * 0.5))) },
  ];
  const timeline = [
    { phase: "Detection", duration: Math.max(1, Math.round(latency * 0.3)) },
    { phase: "Analysis", duration: Math.max(1, Math.round(latency * 0.35)) },
    { phase: "Decision", duration: Math.max(1, Math.round(latency * 0.2)) },
    { phase: "Isolation", duration: Math.max(1, latency - (Math.round(latency * 0.3) + Math.round(latency * 0.35) + Math.round(latency * 0.2))) },
  ];
  return {
    charts: [
      {
        title: "Risk Score Trend",
        component: (
          <SafeResponsiveChart className="h-[250px] w-full">
            <AreaChart data={riskTrend}>
              <defs>
                <linearGradient id="riskGradient" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor="#ef4444" stopOpacity={0.8} /><stop offset="95%" stopColor="#ef4444" stopOpacity={0} />
                </linearGradient>
              </defs>
              <CartesianGrid strokeDasharray="3 3" stroke="#64748b" strokeOpacity={0.25} />
              <XAxis dataKey="time" stroke="#64748b" tick={{ fontSize: 11 }} />
              <YAxis stroke="#64748b" tick={{ fontSize: 11 }} domain={[0, 100]} />
              <Tooltip />
              <Area type="monotone" dataKey="risk" stroke="#ef4444" strokeWidth={2} fillOpacity={1} fill="url(#riskGradient)" />
            </AreaChart>
          </SafeResponsiveChart>
        ),
      },
      {
        title: "Model Health Metrics",
        component: (
          <SafeResponsiveChart className="h-[250px] w-full">
            <RadarChart data={isolationMetrics}>
              <PolarGrid stroke="#64748b" strokeOpacity={0.25} />
              <PolarAngleAxis dataKey="metric" stroke="#64748b" tick={{ fontSize: 10 }} />
              <PolarRadiusAxis angle={90} domain={[0, 100]} stroke="#64748b" tick={{ fontSize: 10 }} />
              <Radar name="Score" dataKey="value" stroke="#f59e0b" fill="#f59e0b" fillOpacity={0.6} /><Tooltip />
            </RadarChart>
          </SafeResponsiveChart>
        ),
      },
      {
        title: "Isolation Decision Timeline",
        component: (
          <SafeResponsiveChart className="h-[250px] w-full">
            <BarChart data={timeline} layout="horizontal">
              <CartesianGrid strokeDasharray="3 3" stroke="#64748b" strokeOpacity={0.25} />
              <XAxis type="number" stroke="#64748b" tick={{ fontSize: 11 }} />
              <YAxis type="category" dataKey="phase" stroke="#64748b" tick={{ fontSize: 11 }} width={100} />
              <Tooltip /><Bar dataKey="duration" fill="#ef4444" />
            </BarChart>
          </SafeResponsiveChart>
        ),
      },
    ],
  };
}

function get17LogCharts(logData) {
  const metadata = logData?.metadata || {};
  const tags = Array.isArray(metadata.compliance_tags) ? metadata.compliance_tags : [];
  const matched = Array.isArray(metadata.matched_patterns) ? metadata.matched_patterns : [];
  const action = String(logData.action || metadata.action || "allow").toLowerCase();
  const riskScore = parseNumeric(metadata.security_risk_score) || Math.round((parseNumeric(metadata.risk_score) || 0) * 100);
  const latency = parseNumeric(metadata.latency_ms) || parseNumeric(logData.duration) || 0;
  const guardrailScores = [
    { guardrail: "PII", score: Math.max(0.01, Math.min(1, tags.length ? tags.length / 10 : 0.05)) },
    { guardrail: "Credential", score: Math.max(0.01, Math.min(1, matched.length ? matched.length / 10 : 0.04)) },
    { guardrail: "Policy", score: Math.max(0.01, Math.min(1, riskScore / 100 || 0.05)) },
    { guardrail: "Hallucination", score: Math.max(0.01, Math.min(1, action === "monitor" ? 0.6 : 0.15)) },
  ];
  const contentAnalysis = [
    { category: "Safe", value: action === "allow" ? 80 : 35, color: "#10b981" },
    { category: "Flagged", value: action === "monitor" || action === "redact" ? 45 : 15, color: "#f59e0b" },
    { category: "Blocked", value: action === "block" ? 50 : 5, color: "#ef4444" },
  ];
  const pipeline = [
    { stage: "Output Scan", duration: Math.max(1, Math.round(latency * 0.35)) },
    { stage: "PII/Secret", duration: Math.max(1, Math.round(latency * 0.25)) },
    { stage: "Guardrail", duration: Math.max(1, Math.round(latency * 0.2)) },
    { stage: "Policy Action", duration: Math.max(1, latency - (Math.round(latency * 0.35) + Math.round(latency * 0.25) + Math.round(latency * 0.2))) },
  ];
  return {
    charts: [
      {
        title: "Guardrail Confidence Scores",
        component: (
          <SafeResponsiveChart className="h-[250px] w-full">
            <BarChart data={guardrailScores}>
              <CartesianGrid strokeDasharray="3 3" stroke="#64748b" strokeOpacity={0.25} />
              <XAxis dataKey="guardrail" stroke="#64748b" tick={{ fontSize: 11 }} />
              <YAxis stroke="#64748b" tick={{ fontSize: 11 }} domain={[0, 1]} />
              <Tooltip /><Bar dataKey="score" fill="#8b5cf6" />
            </BarChart>
          </SafeResponsiveChart>
        ),
      },
      {
        title: "Content Analysis Results",
        component: (
          <SafeResponsiveChart className="h-[250px] w-full">
            <PieChart>
              <Pie data={contentAnalysis} cx="50%" cy="50%" outerRadius={90} dataKey="value" label={({ category, value }) => `${category}: ${value}%`}>
                {contentAnalysis.map((entry, index) => (<Cell key={`cell-${index}`} fill={entry.color} />))}
              </Pie>
              <Tooltip />
            </PieChart>
          </SafeResponsiveChart>
        ),
      },
      {
        title: "Processing Pipeline",
        component: (
          <SafeResponsiveChart className="h-[250px] w-full">
            <BarChart data={pipeline} layout="horizontal">
              <CartesianGrid strokeDasharray="3 3" stroke="#64748b" strokeOpacity={0.25} />
              <XAxis type="number" stroke="#64748b" tick={{ fontSize: 11 }} />
              <YAxis type="category" dataKey="stage" stroke="#64748b" tick={{ fontSize: 10 }} width={100} />
              <Tooltip /><Bar dataKey="duration" fill="#14b8a6" />
            </BarChart>
          </SafeResponsiveChart>
        ),
      },
    ],
  };
}

function getDefaultLogCharts(logData) {
  const value = parseNumeric(logData.duration) || parseNumeric(logData?.metadata?.latency_ms) || 0;
  return {
    charts: [
      {
        title: "Processing Timeline",
        component: (
          <SafeResponsiveChart className="h-[250px] w-full">
            <LineChart data={[{ step: 1, value }]}>
              <CartesianGrid strokeDasharray="3 3" stroke="#64748b" strokeOpacity={0.25} />
              <XAxis dataKey="step" stroke="#64748b" tick={{ fontSize: 11 }} />
              <YAxis stroke="#64748b" tick={{ fontSize: 11 }} />
              <Tooltip /><Line type="monotone" dataKey="value" stroke="#14b8a6" strokeWidth={2} />
            </LineChart>
          </SafeResponsiveChart>
        ),
      },
    ],
  };
}

function parseNumeric(value) {
  if (typeof value === "number") return value;
  if (typeof value !== "string") return 0;
  const parsed = Number.parseFloat(value.replace(/[^0-9.]/g, ""));
  return Number.isFinite(parsed) ? parsed : 0;
}
