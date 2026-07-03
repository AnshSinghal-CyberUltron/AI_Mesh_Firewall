import { useEffect, useMemo, useState } from "react";
import {
  ArrowLeft, Eye, Clock, Activity, Download, Share2, Copy,
  Shield, Server, CheckCircle, TrendingUp, ChevronDown,
} from "lucide-react";
import { SafeResponsiveChart } from "./SafeResponsiveChart";
import { StageTimeline } from "./simulator/StageTimeline";
import { copyToClipboard } from "../lib/clipboard";
import { getModuleLogCharts } from "./module-specific-log-charts";
import { useAuth } from "../context/AuthContext";
import {
  formatPipelineDurationMs,
  formatDominantStageLabel,
  formatRouteDestination,
  resolveLatencyBreakdown,
  resolveRoutingDecision,
  resolvePipelineInputOutput,
  resolveTotalLatencyMs,
  resolveTtftMs,
} from "../utils/pipelineTrace";
import { formatDecisionSource, formatRoutingReason } from "../constants/zeroshieldBrand";
import { CHART_PALETTE } from "../utils/chartTheme";

const METRIC_ICON_CLASS = {
  blue: "text-blue-500 dark:text-blue-400",
  teal: "text-teal-500 dark:text-teal-400",
  purple: "text-purple-500 dark:text-purple-400",
  emerald: "text-emerald-500 dark:text-emerald-400",
  amber: "text-amber-500 dark:text-amber-400",
  red: "text-red-500 dark:text-red-400",
};

function normalizeLogDetail(logData) {
  const raw = logData?.raw || logData || {};
  const meta = logData?.metadata || raw?.metadata || logData?.event_metadata || {};
  const extra = meta?.extra || {};

  const requestId =
    logData?.request_id
    || meta?.request_id
    || meta?.pipeline_request_id
    || extra?.request_id
    || raw?.metadata?.request_id
    || "n/a";

  const scanId = logData?.id || raw?.id || "n/a";
  const incidentId =
    logData?.incident_id
    || meta?.incident_id
    || extra?.incident_id
    || raw?.incident_id
    || (String(requestId).startsWith("zs-") ? requestId : null)
    || (scanId !== "n/a" ? String(scanId) : null);

  const pipelineTrace =
    logData?.pipeline_trace
    || meta?.pipeline_trace
    || extra?.pipeline_trace
    || {};

  const io = resolvePipelineInputOutput({
    meta,
    extra,
    pipelineTrace,
    fallbackAction: logData?.action || raw?.action || "allow",
  });

  const promptText = io.fromTrace
    ? (io.promptSubmitted || io.inputText || "")
    : (
      meta?.prompt_submitted
      || meta?.prompt_snippet
      || extra?.prompt_submitted
      || extra?.prompt_snippet
      || extra?.prompt
      || logData?.prompt
      || ""
    );

  const responseText = io.fromTrace
    ? io.outputText
    : (
      meta?.sanitized_output
      || meta?.response_snippet
      || extra?.sanitized_output
      || extra?.response_snippet
      || extra?.raw_output
      || logData?.response
      || ""
    );

  const pipelineStages = Array.isArray(pipelineTrace?.stages) ? pipelineTrace.stages : [];

  const totalLatencyMs = resolveTotalLatencyMs({
    pipelineTrace,
    meta,
    extra,
    logData,
  });
  const latencyBreakdown = resolveLatencyBreakdown({ pipelineTrace, meta, extra, logData });
  const ttftMs = resolveTtftMs({ pipelineTrace, meta, extra, zeroshield: meta.zeroshield || extra.zeroshield });

  let durationLabel = formatPipelineDurationMs(totalLatencyMs);
  if (
    latencyBreakdown
    && latencyBreakdown.stage_latency_sum_ms != null
    && latencyBreakdown.overhead_ms != null
    && totalLatencyMs > 0
  ) {
    durationLabel = `${formatPipelineDurationMs(totalLatencyMs)} (stages ${Math.round(latencyBreakdown.stage_latency_sum_ms)}ms + overhead ${Math.round(latencyBreakdown.overhead_ms)}ms)`;
  }

  return {
    raw,
    meta,
    scanId,
    requestId,
    incidentId,
    promptText,
    responseText,
    pipelineTrace,
    pipelineStages,
    timestamp: logData?.timestamp || raw?.timestamp || "",
    duration: durationLabel,
    totalLatencyMs,
    ttftMs,
    latencyBreakdown,
    status: logData?.status || logData?.action || raw?.action || "allowed",
    action: logData?.action || raw?.action || "ALLOWED",
  };
}

// Pipeline-derived verdict. An event's own top-level action/score can UNDER-report
// what the pipeline actually did: a STREAMED request whose input_scan redacted PII is
// stored as action='allow' / security_risk_score=0 (the redaction lives only in the
// stage trace, not the stream_complete summary). Lift the displayed verdict to the
// most-severe pipeline stage so the Scan Detail is honest regardless of which
// telemetry path (stream vs non-stream) produced the event.
const _STAGE_SEVERITY = { block: 3, error: 3, redact: 2, rewrite: 2, flag: 1, reroute: 1, monitor: 0, skip: 0, allow: 0, pass: 0 };
const _STAGE_SCORE = { block: 90, error: 90, redact: 75, rewrite: 75, flag: 60, reroute: 40 };
const ACTION_TONE = { allow: "emerald", monitor: "emerald", pass: "emerald", redact: "blue", rewrite: "blue", flag: "amber", reroute: "amber", block: "red", error: "red" };

// Honest timestamp: a log missing its timestamp must show "—", never the
// current wall-clock time (which reads as if the event just happened now).
function fmtTimestamp(ts) {
  if (!ts) return "—";
  const d = new Date(ts);
  return Number.isNaN(d.getTime()) ? "—" : d.toLocaleString();
}

function deriveStageVerdict(stages) {
  let action = null, score = 0, severity = -1;
  for (const s of Array.isArray(stages) ? stages : []) {
    const a = String(s?.action || "").toLowerCase();
    const sev = _STAGE_SEVERITY[a] ?? 0;
    if (a && sev > severity) { severity = sev; action = a; }
    if (_STAGE_SCORE[a]) score = Math.max(score, _STAGE_SCORE[a]);
  }
  return { action, score, severity };
}

export function LogDetailPage({ logData, onBack }) {
  const { fetchWithAuth } = useAuth();
  const [detail, setDetail] = useState(logData);
  const [loadingDetail, setLoadingDetail] = useState(false);

  useEffect(() => {
    setDetail(logData);
  }, [logData]);

  useEffect(() => {
    const id = logData?.id || logData?.raw?.id;
    if (!id || !fetchWithAuth) return;
    let cancelled = false;
    (async () => {
      setLoadingDetail(true);
      try {
        const res = await fetchWithAuth(`/api/security/threat-feed/${id}/`);
        if (!res.ok || cancelled) return;
        const item = await res.json();
        if (cancelled) return;
        setDetail((prev) => ({
          ...(prev || {}),
          ...item,
          id: item.id || id,
          metadata: item.metadata || prev?.metadata,
          request_id: item.request_id,
          incident_id: item.incident_id,
        }));
      } catch {
        // Keep list-row payload if detail fetch fails.
      } finally {
        if (!cancelled) setLoadingDetail(false);
      }
    })();
    return () => { cancelled = true; };
  }, [logData?.id, logData?.raw?.id, fetchWithAuth]);

  const normalized = useMemo(() => normalizeLogDetail(detail || logData || {}), [detail, logData]);

  const [expandedSections, setExpandedSections] = useState({
    pipeline: true, content: true, request: true, response: false, security: false, metadata: false,
  });
  const [copiedField, setCopiedField] = useState(null);

  const moduleLogCharts = getModuleLogCharts(detail || logData || {});
  const meta = normalized.meta;

  const timestamp = normalized.timestamp;
  const duration = normalized.duration;
  const ttftMs = normalized.ttftMs;
  const latencyBreakdown = normalized.latencyBreakdown;
  const latencyHints = latencyBreakdown?.hints || [];
  const status = normalized.status;
  const action = normalized.action;
  const scanId = normalized.scanId;
  const requestId = normalized.requestId;
  const incidentId = normalized.incidentId;
  const promptText = normalized.promptText;
  const responseText = normalized.responseText;
  const pipelineStages = normalized.pipelineStages;
  const pipelineTrace = normalized.pipelineTrace;
  const routingDecision = useMemo(
    () => resolveRoutingDecision({ pipelineTrace, meta: normalized.meta }),
    [pipelineTrace, normalized.meta],
  );
  const pipelineIO = useMemo(
    () => resolvePipelineInputOutput({
      pipelineTrace,
      meta: normalized.meta,
      extra: normalized.meta?.extra,
      promptText,
      responseText,
      fallbackAction: action,
    }),
    [pipelineTrace, normalized.meta, promptText, responseText, action],
  );

  const handleCopy = async (text, field) => {
    await copyToClipboard(text);
    setCopiedField(field);
    setTimeout(() => setCopiedField(null), 2000);
  };

  const toggleSection = (section) => {
    setExpandedSections((prev) => ({ ...prev, [section]: !prev[section] }));
  };

  // Real per-stage latency from the pipeline trace — a single hardcoded
  // [{ time: "Event", latency }] point was a fake "timeline" that implied a
  // trend from one value. When there is no stage trace the chart is hidden.
  const timelineData = pipelineStages
    .map((s) => ({
      time: String(s?.name || s?.stage || "").replace(/_/g, " "),
      latency: Math.round(parseNumeric(s?.latency_ms ?? s?.duration_ms ?? s?.latency)),
    }))
    .filter((s) => s.time);
  // Promote the displayed verdict / score / threat to the most-severe pipeline stage
  // when the event's own top-level fields under-report it (see deriveStageVerdict).
  const stageVerdict = deriveStageVerdict(pipelineStages);
  const baseScore = parseNumeric(logData?.severity || meta?.security_risk_score) || 0;
  const securityScore = Math.max(baseScore, stageVerdict.score);
  const threatLevel = securityScore > 0
    ? mapThreatLevel(securityScore)
    : mapThreatLevel(logData?.severity || meta?.security_risk_score);
  const _baseSeverity = _STAGE_SEVERITY[String(action || "").toLowerCase()] ?? 0;
  const _promote = stageVerdict.action && stageVerdict.severity > _baseSeverity;
  const effectiveAction = _promote ? stageVerdict.action : action;
  const effectiveStatus = _promote ? stageVerdict.action : status;
  // Incident ID is a backend alias of Request ID (no separate incident-grouping exists);
  // showing two guaranteed-identical IDs is noise. Only surface it when it truly differs.
  const showIncidentId = Boolean(incidentId) && incidentId !== requestId && incidentId !== String(scanId);

  const handleExport = () => {
    const payload = JSON.stringify(detail || logData || {}, null, 2);
    const blob = new Blob([payload], { type: "application/json;charset=utf-8;" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `module-log-${scanId}.json`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  };

  const handleShare = async () => {
    const url = window.location.href;
    const message = `ZeroShield Scan #${scanId} | Status: ${status} | Threat: ${meta?.threat_type || "none"} | Model: ${meta?.model || "n/a"} | ${url}`;
    if (navigator.share) {
      try {
        await navigator.share({ title: "ZeroShield Security Log", text: message, url });
        return;
      } catch {
        // Fall back to clipboard for unsupported/denied share flow.
      }
    }
    await copyToClipboard(message);
    setCopiedField("Shared");
    setTimeout(() => setCopiedField(null), 2000);
  };

  return (
    <div className="space-y-6">
      {/* Header with Breadcrumb */}
      <div className="bg-white dark:bg-slate-800 rounded-xl border-2 border-slate-200 dark:border-slate-700 shadow-sm p-6">
        <button
          onClick={onBack}
          className="flex items-center gap-2 text-teal-600 dark:text-teal-400 hover:text-teal-700 dark:hover:text-teal-300 mb-4 transition-colors"
        >
          <ArrowLeft className="w-4 h-4" />
          <span className="text-sm font-medium">Back to Activity Preview</span>
        </button>
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-3 mb-2">
              <h1 className="text-2xl font-bold text-slate-900 dark:text-slate-100">Scan Detail Report</h1>
              <StatusBadge status={effectiveStatus} action={effectiveAction} />
            </div>
            <div className="flex flex-wrap items-center gap-x-4 gap-y-2 text-sm text-slate-600 dark:text-slate-400">
              <div className="flex items-center gap-2"><Eye className="w-4 h-4" /><span>Scan ID: {scanId}</span></div>
              <div className="w-1 h-1 bg-slate-400 rounded-full"></div>
              <div className="flex items-center gap-2"><Clock className="w-4 h-4" /><span>{fmtTimestamp(timestamp)}</span></div>
              <div className="w-1 h-1 bg-slate-400 rounded-full"></div>
              <div className="flex items-center gap-2"><Activity className="w-4 h-4" /><span>Duration: {duration}</span></div>
              <div className="w-1 h-1 bg-slate-400 rounded-full"></div>
              <button onClick={() => handleCopy(requestId, "Request ID")} className="flex min-w-0 max-w-full items-center gap-2 font-mono hover:text-teal-600 dark:hover:text-teal-400 transition-colors" title="Copy request ID">
                <Server className="w-4 h-4 shrink-0" /><span className="truncate">Request ID: {requestId}</span>{copiedField === "Request ID" && <span className="text-teal-600 dark:text-teal-400 shrink-0">✓</span>}
              </button>
              {showIncidentId && (
                <>
                  <div className="w-1 h-1 bg-slate-400 rounded-full"></div>
                  <button onClick={() => handleCopy(incidentId, "Incident ID")} className="flex items-center gap-2 font-mono hover:text-teal-600 transition-colors" title="Copy incident ID">
                    <Shield className="w-4 h-4" /><span>Incident ID: {incidentId}</span>{copiedField === "Incident ID" && <span className="text-teal-600">✓</span>}
                  </button>
                </>
              )}
              {loadingDetail && (
                <span className="text-xs text-slate-500 dark:text-slate-400">Loading full trace…</span>
              )}
            </div>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <button
              onClick={() => handleCopy(JSON.stringify(logData, null, 2), "Request Payload")}
              className="inline-flex min-h-9 items-center gap-2 rounded-lg bg-slate-100 px-4 py-2 text-sm font-medium text-slate-700 transition-colors hover:bg-slate-200 dark:bg-slate-700 dark:text-slate-300 dark:hover:bg-slate-600"
            >
              <Copy className="w-4 h-4" />
              {copiedField === "Request Payload" ? "Copied!" : "Copy"}
            </button>
            <button onClick={handleShare} className="inline-flex min-h-9 items-center gap-2 rounded-lg bg-slate-100 px-4 py-2 text-sm font-medium text-slate-700 transition-colors hover:bg-slate-200 dark:bg-slate-700 dark:text-slate-300 dark:hover:bg-slate-600">
              <Share2 className="w-4 h-4" />
              <span>{copiedField === "Shared" ? "Shared!" : "Share"}</span>
            </button>
            <button onClick={handleExport} className="inline-flex min-h-9 items-center gap-2 rounded-lg bg-teal-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-teal-700">
              <Download className="w-4 h-4" />Export
            </button>
          </div>
        </div>
      </div>

      {/* Key Metrics */}
      <div className={`grid grid-cols-1 gap-4 sm:grid-cols-2 ${ttftMs != null ? "xl:grid-cols-5" : "xl:grid-cols-4"}`}>
        <MetricCard icon={CheckCircle} label="Overall Status" value={(effectiveAction || status).toUpperCase()} color={ACTION_TONE[String(effectiveAction || status).toLowerCase()] || "emerald"} />
        <MetricCard icon={Clock} label="Total Duration" value={duration} color="blue" />
        {ttftMs != null && (
          <MetricCard icon={Activity} label="Time to First Token" value={formatPipelineDurationMs(ttftMs)} color="teal" />
        )}
        <MetricCard icon={Shield} label="Security Score" value={`${securityScore}/100`} color="purple" />
        <MetricCard icon={Activity} label="Threat Level" value={threatLevel} color="teal" />
      </div>

      {timelineData.length > 0 && (
        <div className="bg-white dark:bg-slate-800 rounded-xl border-2 border-slate-200 dark:border-slate-700 shadow-sm p-6">
          <h3 className="text-base font-semibold text-slate-900 dark:text-slate-100 mb-4">Pipeline Stage Latency</h3>
          <SafeResponsiveChart
            className="h-[220px] w-full"
            option={{
              grid: { top: 14, right: 14, bottom: timelineData.length > 4 ? 60 : 30, left: 46 },
              tooltip: { trigger: "axis", valueFormatter: (v) => `${v} ms` },
              xAxis: { type: "category", data: timelineData.map((d) => d.time), axisLabel: { fontSize: 11, rotate: timelineData.length > 4 ? 30 : 0, interval: 0 } },
              yAxis: { type: "value", axisLabel: { fontSize: 11, formatter: "{value} ms" } },
              series: [{ name: "Latency", type: "bar", barWidth: "55%", itemStyle: { color: CHART_PALETTE[6], borderRadius: [3, 3, 0, 0] }, data: timelineData.map((d) => d.latency) }],
            }}
          />
        </div>
      )}

      {latencyBreakdown && (latencyBreakdown.by_stage?.length > 0 || latencyHints.length > 0) && (
        <div className="bg-white dark:bg-slate-800 rounded-xl border-2 border-slate-200 dark:border-slate-700 shadow-sm p-6">
          <h3 className="text-base font-semibold text-slate-900 dark:text-slate-100 mb-3">
            Latency Breakdown
          </h3>
          {latencyBreakdown.dominant_stage && (
            <p className="text-sm text-slate-600 dark:text-slate-300 mb-4">
              Dominant stage:{" "}
              <span className="font-medium text-slate-900 dark:text-slate-100">
                {formatDominantStageLabel(latencyBreakdown.dominant_stage)}
              </span>
              {latencyBreakdown.dominant_latency_ms != null && (
                <>
                  {" "}— {formatPipelineDurationMs(latencyBreakdown.dominant_latency_ms)}
                  {latencyBreakdown.dominant_share_pct != null && (
                    <> ({Math.round(latencyBreakdown.dominant_share_pct)}% of total)</>
                  )}
                </>
              )}
            </p>
          )}
          {Array.isArray(latencyBreakdown.by_stage) && latencyBreakdown.by_stage.length > 0 && (
            <div className="overflow-x-auto mb-4">
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-left text-slate-500 dark:text-slate-400 border-b border-slate-200 dark:border-slate-700">
                    <th className="py-2 pr-4 font-medium">Stage</th>
                    <th className="py-2 pr-4 font-medium text-right">Latency</th>
                    <th className="py-2 font-medium text-right">Share</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100 dark:divide-slate-700/60">
                  {latencyBreakdown.by_stage.map((row) => (
                    <tr key={row.stage}>
                      <td className="py-2 pr-4 text-slate-800 dark:text-slate-200">
                        {formatDominantStageLabel(row.stage)}
                      </td>
                      <td className="py-2 pr-4 text-right tabular-nums text-slate-700 dark:text-slate-300">
                        {formatPipelineDurationMs(row.latency_ms)}
                      </td>
                      <td className="py-2 text-right tabular-nums text-slate-500 dark:text-slate-400">
                        {row.share_pct != null ? `${Math.round(row.share_pct)}%` : "—"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {latencyHints.length > 0 && (
            <div className="space-y-3">
              <h4 className="text-sm font-semibold text-slate-900 dark:text-slate-100">
                How to reduce latency
              </h4>
              {latencyHints.map((hint) => (
                <div
                  key={hint.stage}
                  className={`rounded-lg border p-4 ${
                    hint.severity === "high"
                      ? "border-amber-300 bg-amber-50 dark:border-amber-700/60 dark:bg-amber-950/30"
                      : "border-slate-200 bg-slate-50 dark:border-slate-600 dark:bg-slate-900/40"
                  }`}
                  data-testid={`latency-hint-${hint.stage}`}
                >
                  <p className="text-sm font-medium text-slate-900 dark:text-slate-100 mb-2">
                    {hint.message}
                  </p>
                  {Array.isArray(hint.actions) && hint.actions.length > 0 && (
                    <ul className="list-disc list-inside text-sm text-slate-600 dark:text-slate-300 space-y-1">
                      {hint.actions.map((action) => (
                        <li key={action}>{action}</li>
                      ))}
                    </ul>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Module-Specific Log Detail Charts */}
      {moduleLogCharts && moduleLogCharts.charts && moduleLogCharts.charts.length > 0 && (
        <>
          <div className="flex items-center justify-between">
            <h2 className="text-xl font-bold text-slate-900 dark:text-slate-100">Module-Specific Scan Analysis</h2>
            <span className="text-sm text-slate-500 dark:text-slate-400">Detailed insights for this specific scan</span>
          </div>
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
            {moduleLogCharts.charts.map((chart, index) => (
              <div key={index} className="bg-white dark:bg-slate-800 rounded-xl border-2 border-slate-200 dark:border-slate-700 shadow-sm p-6">
                <h3 className="text-base font-semibold text-slate-900 dark:text-slate-100 mb-4">{chart.title}</h3>
                {chart.component}
              </div>
            ))}
          </div>
        </>
      )}

      {/* Detailed Scan Data - Collapsible Sections */}
      <div className="space-y-4">
        {/* Full pipeline view — per-stage action + result + latency. */}
        <CollapsibleSection
          title={`Pipeline Stages${pipelineStages.length ? ` (${pipelineStages.length})` : ""}`}
          icon={Activity}
          isExpanded={expandedSections.pipeline}
          onToggle={() => toggleSection("pipeline")}
          allowOverflow
          testId="log-detail-pipeline-stages"
        >
          {pipelineStages.length > 0 ? (
            <div data-testid="pipeline-trace-view" className="space-y-4">
              {routingDecision && (
                <RoutingDecisionCard routing={routingDecision} />
              )}
              <StageTimeline stages={pipelineStages} />
            </div>
          ) : (
            <p className="text-sm text-slate-500 dark:text-slate-400">
              No per-stage pipeline trace was recorded for this event
              {meta?.event_type ? ` (event type: ${meta.event_type})` : ""}. Stage timings appear on full request/output-guard events.
            </p>
          )}
        </CollapsibleSection>

        {/* Input / Output content — PIPELINE-0022 trace-root redacted-safe I/O. */}
        <CollapsibleSection
          title="Input / Output"
          icon={Server}
          isExpanded={expandedSections.content}
          onToggle={() => toggleSection("content")}
        >
          {(pipelineIO.inputText || pipelineIO.outputText || pipelineIO.outputWithheld) ? (
            <div className="space-y-4">
              {pipelineIO.inputWasRedacted ? (
                <>
                  <ContentBlock
                    label="Input (before redaction)"
                    text={pipelineIO.inputBefore}
                    onCopy={() => handleCopy(pipelineIO.inputBefore, "InputBefore")}
                    copied={copiedField === "InputBefore"}
                  />
                  <ContentBlock
                    label="Input (forwarded to model)"
                    text={pipelineIO.inputAfter}
                    onCopy={() => handleCopy(pipelineIO.inputAfter, "InputAfter")}
                    copied={copiedField === "InputAfter"}
                  />
                </>
              ) : (
                <ContentBlock
                  label="Input (prompt)"
                  text={pipelineIO.inputText}
                  onCopy={() => handleCopy(pipelineIO.inputText, "Prompt")}
                  copied={copiedField === "Prompt"}
                />
              )}
              {pipelineIO.outputWithheld ? (
                <div>
                  <span className="text-xs font-semibold text-slate-600 dark:text-slate-400 uppercase tracking-wide">
                    Output (response)
                  </span>
                  <p className="mt-1 rounded-lg border border-red-200 dark:border-red-900/50 bg-red-50 dark:bg-red-950/30 p-3 text-sm text-red-800 dark:text-red-200">
                    {pipelineIO.outputWithheldReason || "[Response withheld — not delivered to client]"}
                  </p>
                </div>
              ) : (
                <ContentBlock
                  label="Output (response)"
                  text={pipelineIO.outputText}
                  onCopy={() => handleCopy(pipelineIO.outputText, "Response")}
                  copied={copiedField === "Response"}
                />
              )}
            </div>
          ) : (
            <p className="text-sm text-slate-500 dark:text-slate-400">
              No prompt/response content was captured on this event. Look up other events sharing Request <span className="font-mono">{requestId}</span> for the full conversation.
            </p>
          )}
        </CollapsibleSection>

        <CollapsibleSection
          title="Request Details" icon={Server}
          isExpanded={expandedSections.request}
          onToggle={() => toggleSection("request")}
        >
          <table className="w-full text-sm">
            <tbody className="divide-y divide-slate-200 dark:divide-slate-700">
              <DataRow label="Request ID" value={requestId} />
              {showIncidentId && <DataRow label="Incident ID" value={incidentId} />}
              <DataRow label="Scan (row) ID" value={String(scanId)} />
              <DataRow label="Timestamp" value={fmtTimestamp(timestamp)} />
              <DataRow label="Method" value={logData?.method || meta?.method || "—"} />
              <DataRow label="Endpoint" value={logData?.endpoint || meta?.endpoint || "—"} />
              <DataRow label="Source IP" value={meta?.source_ip || logData?.ip || logData?.source_ip || "--"} />
              <DataRow label="User Agent" value={meta?.user_agent || logData?.userAgent || logData?.user_agent || "--"} />
              <DataRow label="Model" value={logData?.model || meta?.model || "--"} />
              <DataRow label="Status Code" value={logData?.statusCode || meta?.status_code || "--"} />
            </tbody>
          </table>
        </CollapsibleSection>

        <CollapsibleSection
          title="Response Details" icon={Activity}
          isExpanded={expandedSections.response}
          onToggle={() => toggleSection("response")}
        >
          <table className="w-full text-sm">
            <tbody className="divide-y divide-slate-200 dark:divide-slate-700">
              <DataRow label="Status Code" value={logData?.statusCode || meta?.status_code || "--"} />
              <DataRow label="Response Time" value={duration} />
              <DataRow label="Action" value={effectiveAction || action} />
              <DataRow label="Input Tokens" value={meta?.input_tokens ?? meta?.extra?.input_tokens ?? logData?.tokensPrompt ?? "--"} />
              <DataRow label="Output Tokens" value={meta?.output_tokens ?? meta?.extra?.output_tokens ?? logData?.tokensCompletion ?? "--"} />
              <DataRow label="Total Tokens" value={meta?.total_tokens ?? meta?.extra?.total_tokens ?? logData?.tokensUsed ?? "--"} />
              <DataRow label="Cost (USD)" value={
                meta?.extra?.cost != null ? `$${Number(meta.extra.cost).toFixed(6)}` : (logData?.cost != null ? `$${Number(logData.cost).toFixed(6)}` : "--")
              } />
              <DataRow label="Cache Hit" value={
                meta?.extra?.cache_hit != null ? (meta.extra.cache_hit ? "Yes" : "No") : (logData?.cacheHit != null ? (logData.cacheHit ? "Yes" : "No") : "--")
              } />
            </tbody>
          </table>
        </CollapsibleSection>

        <CollapsibleSection
          title="Security Analysis" icon={Shield}
          isExpanded={expandedSections.security}
          onToggle={() => toggleSection("security")}
        >
          <table className="w-full text-sm">
            <tbody className="divide-y divide-slate-200 dark:divide-slate-700">
              <DataRow label="Threat Level" value={threatLevel} />
              <DataRow label="Input Validation" value={meta?.input_validation || "--"} />
              <DataRow label="Content Safety" value={meta?.content_safety || "--"} />
              <DataRow label="PII Detection" value={String(meta?.pii_detected ?? "--")} />
              <DataRow label="Prompt Injection" value={String(meta?.prompt_injection_detected ?? "--")} />
              <DataRow label="Jailbreak Attempt" value={String(meta?.jailbreak_detected ?? "--")} />
              <DataRow label="Rate Limit Status" value={meta?.rate_limit_status || "--"} />
              <DataRow label="Auth Status" value={meta?.auth_status || "--"} />
              <DataRow label="Policy Violations" value={
                Array.isArray(meta?.policy_violations) && meta.policy_violations.length > 0
                  ? meta.policy_violations.join(", ")
                  : (meta?.policy_violations != null && meta.policy_violations !== "" ? String(meta.policy_violations) : "--")
              } />
              <DataRow label="Pipeline Stage" value={meta?.pipeline_stage || "--"} />
              <DataRow label="Intent" value={meta?.intent || "--"} />
              <DataRow label="OWASP Code" value={meta?.owasp_code || logData?.subcategory || "--"} />
              <DataRow label="Compliance Tags" value={
                Array.isArray(meta?.compliance_tags) && meta.compliance_tags.length > 0
                  ? meta.compliance_tags.join(", ")
                  : "--"
              } />
              <DataRow label="Matched Patterns" value={
                Array.isArray(meta?.extra?.matched_patterns) && meta.extra.matched_patterns.length > 0
                  ? meta.extra.matched_patterns.join(", ")
                  : "--"
              } />
            </tbody>
          </table>
        </CollapsibleSection>

        <CollapsibleSection
          title="Metadata & Context" icon={TrendingUp}
          isExpanded={expandedSections.metadata}
          onToggle={() => toggleSection("metadata")}
        >
          <table className="w-full text-sm">
            <tbody className="divide-y divide-slate-200 dark:divide-slate-700">
              <DataRow label="Project ID" value={meta?.project_id || "--"} />
              <DataRow label="Organization" value={logData?.organization_name || meta?.organization_name || logData?.endpoint_name || logData?.raw?.endpoint_name || meta?.organization_id || "--"} />
              <DataRow label="Endpoint" value={meta?.endpoint || logData?.endpoint_id || logData?.raw?.endpoint_id || "--"} />
              <DataRow label="Event Type" value={meta?.event_type || "--"} />
              <DataRow label="Pipeline Stage" value={meta?.pipeline_stage || "--"} />
              <DataRow label="Pipeline Request ID" value={meta?.pipeline_request_id || "--"} />
              <DataRow label="RAG Collection" value={meta?.extra?.collection || meta?.extra?.rag_collection || meta?.rag_collection || "--"} />
              <DataRow label="Escalation Level" value={
                meta?.extra?.escalation_level != null && meta.extra.escalation_level !== 0
                  ? `Level ${meta.extra.escalation_level}`
                  : "--"
              } />
              <DataRow label="Key Prefix" value={meta?.key_prefix || "--"} />
              <DataRow label="Prompt Hash" value={meta?.prompt_hash || "--"} />
              <DataRow label="Source" value={logData?.source || meta?.source || "--"} />
              <DataRow label="Intent" value={meta?.intent || "--"} />
            </tbody>
          </table>
        </CollapsibleSection>
      </div>
    </div>
  );
}

function parseNumeric(value) {
  if (typeof value === "number") return value;
  if (typeof value !== "string") return 0;
  const parsed = Number.parseFloat(value.replace(/[^0-9.]/g, ""));
  return Number.isFinite(parsed) ? parsed : 0;
}

function formatDuration(ms) {
  if (ms == null) return "--";
  const parsed = parseNumeric(String(ms));
  return Number.isFinite(parsed) ? `${parsed}ms` : "--";
}

function mapThreatLevel(value) {
  const score = parseNumeric(String(value ?? ""));
  if (score >= 80) return "HIGH";
  if (score >= 60) return "MEDIUM";
  if (score > 0) return "LOW";
  return "NONE";
}

function StatusBadge({ status, action }) {
  const allow = { bg: "bg-emerald-100 dark:bg-emerald-800/30", text: "text-emerald-700 dark:text-emerald-300", dot: "bg-emerald-500" };
  const block = { bg: "bg-red-100 dark:bg-red-800/30", text: "text-red-700 dark:text-red-300", dot: "bg-red-500" };
  const flag = { bg: "bg-amber-100 dark:bg-amber-800/30", text: "text-amber-700 dark:text-amber-300", dot: "bg-amber-500" };
  const redact = { bg: "bg-blue-100 dark:bg-blue-800/30", text: "text-blue-700 dark:text-blue-300", dot: "bg-blue-500" };
  // Accept both present- and past-tense verdicts (event action is present-tense:
  // allow/redact/block/flag/reroute; legacy rows used allowed/redacted/…).
  const statusConfig = {
    allow, allowed: allow, monitor: allow, pass: allow,
    block, blocked: block, error: block,
    flag, flagged: flag, reroute: flag,
    redact, redacted: redact, rewrite: redact,
  };
  const config = statusConfig[status?.toLowerCase()] || statusConfig.allow;
  return (
    <span className={`inline-flex items-center gap-1.5 px-3 py-1 ${config.bg} ${config.text} rounded-full text-xs font-semibold`}>
      <div className={`w-1.5 h-1.5 rounded-full ${config.dot}`}></div>
      {action || status?.toUpperCase()}
    </span>
  );
}

function MetricCard({ icon: Icon, label, value, color }) {
  const bgMap = { blue: "bg-blue-50 dark:bg-blue-900/20", teal: "bg-teal-50 dark:bg-teal-900/20", purple: "bg-purple-50 dark:bg-purple-900/20", emerald: "bg-emerald-50 dark:bg-emerald-900/20", amber: "bg-amber-50 dark:bg-amber-900/20", red: "bg-red-50 dark:bg-red-900/20" };
  const iconClass = METRIC_ICON_CLASS[color] || METRIC_ICON_CLASS.teal;
  return (
    <div className="flex h-full flex-col rounded-xl border-2 border-slate-200 bg-white p-4 shadow-sm dark:border-slate-700 dark:bg-slate-800">
      <div className={`mb-3 inline-flex h-10 w-10 items-center justify-center rounded-lg ${bgMap[color] || bgMap.teal}`}>
        <Icon className={`h-5 w-5 ${iconClass}`} />
      </div>
      <div className="mb-1 text-xl font-bold leading-tight text-slate-900 dark:text-slate-100 sm:text-2xl">{value}</div>
      <div className="text-xs font-medium leading-tight text-slate-600 dark:text-slate-400">{label}</div>
    </div>
  );
}

function RoutingDecisionCard({ routing }) {
  const factors = Array.isArray(routing.decision_factors) ? routing.decision_factors : [];
  const weights = routing.weights && typeof routing.weights === "object" ? routing.weights : {};
  const weightEntries = Object.entries(weights);
  const formattedReason = routing.routing_reason
    ? formatRoutingReason(routing.routing_reason, { decisionSource: routing.decision_source })
    : "";
  const sourceLabel = routing.decision_source_label
    || formatDecisionSource(routing.decision_source);

  return (
    <div className="mb-4 rounded-xl border border-indigo-200 bg-indigo-50/60 p-4 dark:border-indigo-500/30 dark:bg-indigo-500/10" data-testid="routing-decision-card">
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <span className="text-xs font-semibold uppercase tracking-wide text-indigo-700 dark:text-indigo-300">
          Routing decision
        </span>
        {routing.route_destination_label && (
          <span className="rounded-full bg-indigo-100 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-indigo-800 dark:bg-indigo-500/20 dark:text-indigo-200">
            {routing.route_destination_label || formatRouteDestination(routing.route_destination)}
          </span>
        )}
      </div>
      <div className="grid gap-2 text-xs text-slate-700 dark:text-slate-300 sm:grid-cols-2">
        {routing.requested_model && (
          <div>
            <span className="text-slate-500 dark:text-slate-400">Requested:</span>{" "}
            <span className="font-mono">{routing.requested_model}</span>
          </div>
        )}
        {routing.routed_model && (
          <div>
            <span className="text-slate-500 dark:text-slate-400">Routed to:</span>{" "}
            <span className="font-mono font-semibold text-indigo-800 dark:text-indigo-200">{routing.routed_model}</span>
          </div>
        )}
        {sourceLabel && (
          <div>
            <span className="text-slate-500 dark:text-slate-400">Decision source:</span>{" "}
            <span>{sourceLabel}</span>
          </div>
        )}
        {Number(routing.routing_score) > 0 && (
          <div>
            <span className="text-slate-500 dark:text-slate-400">Score:</span>{" "}
            <span>{Number(routing.routing_score).toFixed(3)}</span>
          </div>
        )}
        {Number(routing.candidate_count) > 0 && (
          <div>
            <span className="text-slate-500 dark:text-slate-400">Candidates:</span>{" "}
            <span>{routing.candidate_count}</span>
          </div>
        )}
      </div>
      {formattedReason && (
        <p className="mt-3 text-xs leading-relaxed text-slate-700 dark:text-slate-200">{formattedReason}</p>
      )}
      {routing.policy_summary && (
        <p className="mt-2 text-xs text-slate-600 dark:text-slate-400">
          <span className="font-medium text-slate-500 dark:text-slate-400">Policy:</span> {routing.policy_summary}
        </p>
      )}
      {factors.length > 0 && (
        <p className="mt-2 text-xs text-slate-600 dark:text-slate-400">
          <span className="font-medium text-slate-500 dark:text-slate-400">Factors:</span> {factors.join(", ")}
        </p>
      )}
      {weightEntries.length > 0 && (
        <p className="mt-2 text-xs text-slate-600 dark:text-slate-400">
          <span className="font-medium text-slate-500 dark:text-slate-400">Weights:</span>{" "}
          {weightEntries.map(([k, v]) => {
            const raw = Number(v);
            const pct = Number.isFinite(raw) ? `${Math.round(raw * 100)}%` : String(v);
            return `${k}=${pct}`;
          }).join(", ")}
        </p>
      )}
    </div>
  );
}

function CollapsibleSection({ title, icon: Icon, isExpanded, onToggle, children, allowOverflow = false, testId }) {
  // allowOverflow: opt OUT of the card's overflow clipping for sections whose
  // content renders floating popovers/tooltips that must escape the card bounds
  // (e.g. the Pipeline Stages hover-detail card). `overflow-hidden` on the card
  // AND `overflow-x-auto` on the content wrapper (which forces overflow-y to clip)
  // would otherwise cut the popover off at the section's edge. The pipeline's
  // StageTimeline scrolls horizontally on its own, so the wrapper is redundant here.
  return (
    <div className={`rounded-xl border-2 border-slate-200 bg-white shadow-sm dark:border-slate-700 dark:bg-slate-800 ${allowOverflow ? "" : "overflow-hidden"}`} data-testid={testId}>
      <button
        type="button"
        onClick={onToggle}
        aria-expanded={isExpanded}
        className="flex min-h-11 w-full items-center justify-between rounded-t-[10px] px-4 py-4 transition-colors hover:bg-slate-50 dark:hover:bg-slate-700 sm:px-6 sm:py-5"
      >
        <div className="flex min-w-0 items-center gap-3">
          <Icon className="h-5 w-5 shrink-0 text-teal-600 dark:text-teal-400" />
          <span className="text-left text-base font-semibold leading-tight text-slate-900 dark:text-slate-100">{title}</span>
        </div>
        <ChevronDown className={`h-5 w-5 shrink-0 text-slate-500 transition-transform dark:text-slate-400 ${isExpanded ? "rotate-180" : ""}`} />
      </button>
      {isExpanded && (
        <div className="px-6 pb-6 border-t border-slate-200 dark:border-slate-700 pt-4">
          {allowOverflow ? children : <div className="overflow-x-auto">{children}</div>}
        </div>
      )}
    </div>
  );
}

function DataRow({ label, value }) {
  return (
    <tr>
      <td className="py-2.5 pr-4 text-xs font-medium text-slate-600 dark:text-slate-400 w-1/3">{label}</td>
      <td className="break-words py-2.5 text-sm font-mono text-slate-900 dark:text-slate-100">{value}</td>
    </tr>
  );
}

function ContentBlock({ label, text, onCopy, copied }) {
  return (
    <div>
      <div className="flex items-center justify-between mb-1">
        <span className="text-xs font-semibold text-slate-600 dark:text-slate-400 uppercase tracking-wide">{label}</span>
        {text ? (
          <button type="button" onClick={onCopy} className="inline-flex min-h-9 min-w-9 items-center justify-center rounded px-2 text-xs text-teal-600 hover:text-teal-700 dark:text-teal-400 dark:hover:text-teal-300">{copied ? "Copied!" : "Copy"}</button>
        ) : null}
      </div>
      <pre className="whitespace-pre-wrap break-words rounded-lg bg-slate-50 dark:bg-slate-900/50 border border-slate-200 dark:border-slate-700 p-3 text-xs font-mono text-slate-800 dark:text-slate-200 max-h-72 overflow-auto">
        {text || "—"}
      </pre>
    </div>
  );
}
