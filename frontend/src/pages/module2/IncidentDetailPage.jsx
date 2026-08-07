import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Loader2,
  ArrowLeft,
  Lock,
  Search,
  ArrowDownToLine,
  Cpu,
  ShieldOff,
  CheckCircle2,
} from "lucide-react";
import { Link, useParams } from "react-router-dom";
import { useAuth } from "../../context/AuthContext";
import { clearModule2Cache, createModule2Api } from "../../api/module2";
import { useRealtimeNotifications } from "../../hooks/useRealtimeNotifications";
import { useContainmentPolling } from "../../hooks/useContainmentPolling";
import { TELEMETRY_ACTIVITY_EVENT } from "../../utils/telemetryEvents";
import { copyToClipboard } from "../../lib/clipboard";
import { formatRiskBandLabel } from "../../utils/riskLabels";
import { PageHeader } from "../../components/module2/PageHeader";
import { Module2RefreshButton } from "../../components/module2/Module2RefreshButton";
import { ChartCard } from "../../components/module2/ChartCard";
import { ContextualAppBar } from "../../components/module2/ContextualAppBar";
import {
  Module2ErrorState,
  Module2PageErrorBoundary,
} from "../../components/module2/PageStates";
import {
  extractIncidentPrompt,
  formatIncidentActionPhrase,
  formatIncidentTimeSpan,
  formatLaneDisplayLabel,
  formatTickerAnalystSummary,
  humanizeThreatType,
  incidentActionBadgeClass,
  incidentLaneDrillDown,
  metadataDetail,
  sourceBadgeClass,
} from "./pageData";
import { ANALYST_BRIEF_TITLE, PAGE_BRIEFS } from "./pageCopy";

const REFRESH_DEBOUNCE_MS = 300;

const STAGE_ICONS = {
  ingress: { Icon: Lock, color: "text-sky-500", bg: "bg-sky-100 dark:bg-sky-900/30" },
  query: { Icon: Search, color: "text-violet-500", bg: "bg-violet-100 dark:bg-violet-900/30" },
  retriever: { Icon: ArrowDownToLine, color: "text-amber-500", bg: "bg-amber-100 dark:bg-amber-900/30" },
  ranker: { Icon: Cpu, color: "text-emerald-500", bg: "bg-emerald-100 dark:bg-emerald-900/30" },
  generator: { Icon: Cpu, color: "text-teal-500", bg: "bg-teal-100 dark:bg-teal-900/30" },
  enforcement: { Icon: ShieldOff, color: "text-red-500", bg: "bg-red-100 dark:bg-red-900/30" },
  completed: { Icon: CheckCircle2, color: "text-green-500", bg: "bg-green-100 dark:bg-green-900/30" },
};

function CaseBrief({ incident, selectedEvent, timeline, source, evidence }) {
  const [promptExpanded, setPromptExpanded] = useState(false);
  const meta = selectedEvent?.metadata || {};
  const extra = meta.extra && typeof meta.extra === "object" ? meta.extra : {};
  const prompt = extractIncidentPrompt(selectedEvent || {});
  const action = selectedEvent?.action || "—";
  const threatRaw = meta.threat_type || evidence?.threat_type || "";
  const threat = humanizeThreatType(threatRaw) || "—";
  const reason = metadataDetail(meta);
  const isKillSwitch =
    String(threatRaw).toLowerCase() === "kill_switch"
    || String(meta.event_type || "").toLowerCase() === "kill_switch"
    || String(extra.trigger_source || "").toLowerCase() === "kill_switch";
  const timeSpan = formatIncidentTimeSpan(incident || {}, timeline || []);
  const eventAt = selectedEvent?.created_at
    ? new Date(selectedEvent.created_at).toLocaleString()
    : "—";
  const lane = String(selectedEvent?.source || source || "generic").toLowerCase();
  const keyPrefix =
    selectedEvent?.key_prefix || evidence?.key_prefix || meta.key_prefix || meta.api_key_prefix || "—";
  const model =
    selectedEvent?.model || evidence?.model || meta.model || extra.original_model || "—";
  const project = meta.project_id || evidence?.project_id || "—";
  const isolationScope = extra.isolation_scope ? String(extra.isolation_scope) : "";
  const summary = selectedEvent
    ? formatTickerAnalystSummary({
        action: selectedEvent.action,
        metadata: {
          ...meta,
          detail: reason || meta.detail,
          model: selectedEvent.model || meta.model || extra.original_model,
          key_prefix: selectedEvent.key_prefix || meta.key_prefix,
          threat_type: threatRaw,
          source: lane,
        },
        source: lane,
      })
    : "Select a timeline event to load the analyst brief.";
  const promptPreviewLimit = 320;
  const promptNeedsExpand = prompt.length > promptPreviewLimit;
  const promptShown =
    promptExpanded || !promptNeedsExpand
      ? prompt
      : `${prompt.slice(0, promptPreviewLimit).trimEnd()}…`;
  const accentBorder =
    String(action).toLowerCase() === "block"
      ? "border-red-300 dark:border-red-700"
      : "border-teal-300 dark:border-teal-700";

  return (
    <div
      className={`rounded-xl border-2 bg-white p-4 shadow-sm dark:bg-slate-800/60 ${accentBorder}`}
      data-testid="incident-case-brief"
    >
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-base font-semibold text-slate-900 dark:text-slate-100">
          Analyst Case Brief
        </h3>
        <span
          className={`inline-flex rounded-md px-2.5 py-1 text-xs font-semibold uppercase tracking-wide ${incidentActionBadgeClass(action)}`}
        >
          {String(action).replace(/_/g, " ") || "—"}
        </span>
      </div>

      <div className="space-y-4 text-sm">
        <p className="text-sm leading-relaxed text-slate-700 dark:text-slate-200">{summary}</p>

        {reason ? (
          <div className="rounded-lg border border-amber-200 bg-amber-50/80 p-3 dark:border-amber-800/60 dark:bg-amber-950/30">
            <p className="text-[11px] font-semibold uppercase tracking-wide text-amber-800 dark:text-amber-300">
              Why this fired
            </p>
            <p className="mt-1 text-sm font-medium text-slate-900 dark:text-slate-100">{reason}</p>
            {isKillSwitch && isolationScope ? (
              <p className="mt-1 text-xs text-slate-600 dark:text-slate-300">
                Isolation scope: <code>{isolationScope}</code>
              </p>
            ) : null}
          </div>
        ) : null}

        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <div className="rounded-lg border border-slate-200 bg-slate-50/80 p-3 dark:border-slate-700 dark:bg-slate-900/40">
            <p className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">Action taken</p>
            <p className="mt-2 text-sm font-semibold capitalize text-slate-900 dark:text-slate-100">
              {String(action).replace(/_/g, " ") || "—"}
            </p>
            <p className="mt-1 text-xs text-slate-600 dark:text-slate-300">
              {formatIncidentActionPhrase(action, meta)}
            </p>
          </div>

          <div className="rounded-lg border border-slate-200 bg-slate-50/80 p-3 dark:border-slate-700 dark:bg-slate-900/40">
            <p className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">Threat</p>
            <p className="mt-2 text-sm font-semibold text-slate-900 dark:text-slate-100">{threat}</p>
            {meta.event_type ? (
              <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
                Event: {String(meta.event_type).replace(/_/g, " ")}
              </p>
            ) : null}
          </div>

          <div className="rounded-lg border border-slate-200 bg-slate-50/80 p-3 dark:border-slate-700 dark:bg-slate-900/40 sm:col-span-2">
            <p className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">Time span</p>
            <p className="mt-2 text-sm font-medium text-slate-900 dark:text-slate-100">{timeSpan.caseSpan}</p>
            <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
              Selected event: {eventAt}
              {timeSpan.eventWindow && timeSpan.eventWindow !== eventAt
                ? ` · Timeline window: ${timeSpan.eventWindow}`
                : ""}
            </p>
          </div>
        </div>

        <div className="rounded-lg border border-slate-200 bg-slate-50/80 p-3 dark:border-slate-700 dark:bg-slate-800/40">
          <div className="mb-2 flex items-center justify-between gap-2">
            <p className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">User prompt</p>
            {prompt ? (
              <button
                type="button"
                onClick={() => copyToClipboard(prompt)}
                className="rounded border border-slate-300 px-2 py-0.5 text-[10px] font-medium text-slate-600 hover:bg-slate-100 dark:border-slate-600 dark:text-slate-300 dark:hover:bg-slate-700/50"
              >
                Copy
              </button>
            ) : null}
          </div>
          {prompt ? (
            <>
              <pre className="max-h-48 overflow-auto whitespace-pre-wrap break-words font-sans text-sm leading-relaxed text-slate-800 dark:text-slate-100">
                {promptShown}
              </pre>
              {promptNeedsExpand ? (
                <button
                  type="button"
                  onClick={() => setPromptExpanded((open) => !open)}
                  className="mt-2 text-xs font-medium text-teal-600 hover:underline dark:text-teal-400"
                >
                  {promptExpanded ? "Show less" : "Show full prompt"}
                </button>
              ) : null}
            </>
          ) : (
            <p className="text-sm text-slate-600 dark:text-slate-300">
              {isKillSwitch
                ? "No chat prompt on this event — containment was applied at the API-key / request boundary (kill switch), not from a scanned user message."
                : "No prompt captured for this event."}
            </p>
          )}
        </div>

        <div className="flex flex-wrap gap-x-4 gap-y-1 border-t border-slate-100 pt-3 text-xs text-slate-500 dark:border-slate-700 dark:text-slate-400">
          <span>
            Lane:{" "}
            <span className={`rounded px-1.5 py-0.5 font-medium ${sourceBadgeClass(lane)}`}>
              {formatLaneDisplayLabel(lane)}
            </span>
          </span>
          <span>
            Key: <code className="text-slate-700 dark:text-slate-200">{keyPrefix}</code>
          </span>
          <span>
            Model: <code className="text-slate-700 dark:text-slate-200">{model}</code>
          </span>
          <span>
            Project: <code className="text-slate-700 dark:text-slate-200">{project}</code>
          </span>
        </div>
      </div>
    </div>
  );
}

function ChainOfCustody({ timeline }) {
  const stageEvents = (timeline || []).filter(
    (ev) => ev.metadata?.pipeline_stage || ev.metadata?.event_type === "rag_pipeline",
  );
  if (stageEvents.length === 0) return null;

  return (
    <ChartCard
      title="Chain-of-Custody — Pipeline Execution Trace"
      titleHelpText="Step-by-step RAG/MCP pipeline actions for this case—shows where ingress, retrieval, or generation policy fired."
    >
      <ol className="relative ml-3 mt-2 space-y-4 border-l border-slate-200 dark:border-slate-700">
        {stageEvents.map((ev, i) => {
          const stage = (ev.metadata?.pipeline_stage || ev.metadata?.event_type || "ingress").toLowerCase();
          const meta = STAGE_ICONS[stage] || STAGE_ICONS.ingress;
          const Icon = meta.Icon;
          const action = ev.action || "allow";
          const isBlock = action === "block";
          const detail = metadataDetail(ev.metadata);
          return (
            <li key={ev.id || i} className="ml-4">
              <span className={`absolute -left-3 flex h-6 w-6 items-center justify-center rounded-full ${meta.bg}`}>
                <Icon className={`h-3.5 w-3.5 ${meta.color}`} />
              </span>
              <div className="rounded-lg border border-slate-100 bg-slate-50 px-3 py-2 dark:border-slate-700 dark:bg-slate-800/50">
                <div className="flex items-center justify-between">
                  <p className="text-xs font-semibold capitalize text-slate-700 dark:text-slate-200">
                    {stage === "ingress" ? "Ingress" : `Pipeline Step [${stage}]`}
                  </p>
                  <span className={`text-xs font-medium ${isBlock ? "text-red-500" : "text-emerald-600"}`}>
                    {isBlock ? "Blocked" : action}
                  </span>
                </div>
                {detail && <p className="mt-0.5 text-xs text-slate-500">{detail}</p>}
                {ev.metadata?.collection && (
                  <p className="mt-0.5 text-xs text-slate-400">
                    Collection: <code>{ev.metadata.collection}</code>
                  </p>
                )}
                {ev.metadata?.model && (
                  <p className="mt-0.5 text-xs text-slate-400">
                    Model: <code>{ev.metadata.model}</code>
                  </p>
                )}
                {ev.metadata?.key_prefix && (
                  <p className="mt-0.5 text-xs text-slate-400">
                    Key: <code>{ev.metadata.key_prefix}</code>
                  </p>
                )}
              </div>
            </li>
          );
        })}
      </ol>
    </ChartCard>
  );
}

export function IncidentDetailPage() {
  return (
    <Module2PageErrorBoundary title="Incident detail failed to render">
      <IncidentDetailPageInner />
    </Module2PageErrorBoundary>
  );
}

function IncidentDetailPageInner() {
  const { id } = useParams();
  const { fetchWithAuth } = useAuth();
  const api = useMemo(() => createModule2Api(fetchWithAuth), [fetchWithAuth]);
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [actionError, setActionError] = useState(null);
  const [actionLoading, setActionLoading] = useState(false);
  const [selectedEvent, setSelectedEvent] = useState(null);
  const loadSeqRef = useRef(0);
  const refreshTimerRef = useRef(null);

  const load = useCallback(async ({ silent = false } = {}) => {
    const seq = ++loadSeqRef.current;
    if (!silent) {
      setLoading(true);
      setError(null);
    }
    try {
      clearModule2Cache();
      const result = await api.getIncident(id, { useCache: false });
      if (seq !== loadSeqRef.current) return;
      setData(result);
      setSelectedEvent((prev) => {
        if (!result?.timeline?.length) return null;
        if (prev && result.timeline.some((ev) => ev.id === prev.id)) return prev;
        return result.timeline[0];
      });
    } catch (e) {
      if (seq !== loadSeqRef.current) return;
      setError(e.message || "Failed to load incident.");
      if (!silent) setData(null);
    } finally {
      if (seq === loadSeqRef.current && !silent) setLoading(false);
    }
  }, [api, id]);

  const refreshLive = useCallback(() => {
    clearTimeout(refreshTimerRef.current);
    refreshTimerRef.current = setTimeout(() => {
      load({ silent: true });
    }, REFRESH_DEBOUNCE_MS);
  }, [load]);

  useEffect(() => () => clearTimeout(refreshTimerRef.current), []);

  useRealtimeNotifications({
    onEnforcementEvent: refreshLive,
    onEscalationEvent: refreshLive,
    onResolutionEvent: refreshLive,
  });
  useContainmentPolling(refreshLive, { enabled: !!data });

  useEffect(() => {
    const onTelemetry = () => refreshLive();
    window.addEventListener(TELEMETRY_ACTIVITY_EVENT, onTelemetry);
    return () => window.removeEventListener(TELEMETRY_ACTIVITY_EVENT, onTelemetry);
  }, [refreshLive]);

  useEffect(() => {
    load();
  }, [load]);

  const promptPayload = useMemo(() => {
    if (!selectedEvent) return null;
    const meta = selectedEvent.metadata || {};
    const source = String(selectedEvent.source || data?.source || "generic").toLowerCase();
    const extra = meta.extra && typeof meta.extra === "object" ? meta.extra : {};
    const promptLineage = Array.isArray(meta.prompt_lineage) ? meta.prompt_lineage.slice(0, 3) : [];
    const promptSnippet = extractIncidentPrompt(selectedEvent);
    if (!promptSnippet && promptLineage.length === 0 && source !== "chat") {
      return null;
    }
    return {
      lane: source,
      request_id: meta.request_id || meta.pipeline_request_id || null,
      model: selectedEvent.model || meta.model || null,
      key_prefix: selectedEvent.key_prefix || meta.key_prefix || meta.api_key_prefix || null,
      prompt_snippet: promptSnippet || null,
      prompt_lineage: promptLineage,
      intent: meta.intent || null,
      detail: meta.detail || extra.detail || null,
    };
  }, [selectedEvent, data?.source]);

  const escalate = async () => {
    setActionLoading(true);
    setActionError(null);
    try {
      await api.escalateIncident(id);
      await load({ silent: true });
    } catch (e) {
      setActionError(e.message || "Escalation failed.");
    } finally {
      setActionLoading(false);
    }
  };

  const resolve = async () => {
    const confirmed = window.confirm(
      `Resolve incident #${id}? This closes the case and moves it to resolved history.`,
    );
    if (!confirmed) return;

    setActionLoading(true);
    setActionError(null);
    try {
      await api.resolveIncident(id);
      await load({ silent: true });
    } catch (e) {
      setActionError(e.message || "Resolve failed.");
    } finally {
      setActionLoading(false);
    }
  };

  if (loading && !data) {
    return (
      <div>
        <ContextualAppBar title={ANALYST_BRIEF_TITLE} description={PAGE_BRIEFS.incidentDetail} />
        <div className="flex justify-center py-20">
          <Loader2 className="h-8 w-8 animate-spin text-teal-500" />
        </div>
      </div>
    );
  }

  if (error && !data) {
    return (
      <div>
        <ContextualAppBar title={ANALYST_BRIEF_TITLE} description={PAGE_BRIEFS.incidentDetail} />
        <Module2ErrorState message={error} onRetry={() => load()} />
      </div>
    );
  }

  if (!data) {
    return (
      <div>
        <ContextualAppBar title={ANALYST_BRIEF_TITLE} description={PAGE_BRIEFS.incidentDetail} />
        <p className="text-sm text-slate-500">Incident not found.</p>
      </div>
    );
  }

  const incident = data.incident;
  const laneDrill = incidentLaneDrillDown(data.source);
  const selectedDetail = selectedEvent ? metadataDetail(selectedEvent.metadata) : "";

  return (
    <div>
      <Link to="/incidents" className="mb-4 inline-flex items-center gap-1 text-sm text-teal-600 hover:underline">
        <ArrowLeft className="h-4 w-4" /> Back to Incidents
      </Link>
      <ContextualAppBar title={ANALYST_BRIEF_TITLE} description={PAGE_BRIEFS.incidentDetail} />
      <PageHeader
        title={incident.title}
        subtitle={
          <>
            {formatRiskBandLabel("severity", incident.severity)} · Status: {incident.status} ·{" "}
            <span className={`rounded px-1.5 py-0.5 text-xs font-medium ${sourceBadgeClass(data.source)}`}>
              {formatLaneDisplayLabel(data.source)}
            </span>
            {laneDrill && (
              <>
                {" · "}
                <Link to={laneDrill.to} className="text-teal-600 hover:underline">
                  {laneDrill.label}
                </Link>
              </>
            )}
          </>
        }
        actions={
          <>
            <Module2RefreshButton
              label="Refresh"
              disabled={actionLoading}
              onRefresh={async () => {
                clearTimeout(refreshTimerRef.current);
                await load({ silent: true });
              }}
            />
            {incident.status !== "escalated" && incident.status !== "resolved" && (
              <button
                type="button"
                onClick={escalate}
                disabled={actionLoading}
                className="rounded-lg border border-amber-300 px-3 py-1.5 text-sm text-amber-700 disabled:opacity-50"
              >
                Escalate
              </button>
            )}
            {incident.status !== "resolved" && (
              <button
                type="button"
                onClick={resolve}
                disabled={actionLoading}
                className="rounded-lg bg-teal-600 px-3 py-1.5 text-sm text-white disabled:opacity-50"
              >
                Resolve
              </button>
            )}
          </>
        }
      />

      {actionError && (
        <div className="mb-4 rounded-lg border border-red-200 bg-red-50 px-4 py-2 text-sm text-red-800 dark:border-red-800 dark:bg-red-950/40 dark:text-red-200">
          {actionError}
        </div>
      )}

      <div className="mb-4">
        <CaseBrief
          key={selectedEvent?.id || "none"}
          incident={incident}
          selectedEvent={selectedEvent}
          timeline={data.timeline}
          source={data.source}
          evidence={data.evidence}
        />
      </div>

      <ChainOfCustody timeline={data.timeline} />

      <div className="mt-4 grid gap-4 lg:grid-cols-2">
        <ChartCard
          title="Incident Timeline"
          titleHelpText="Ordered enforcement events—select one to load evidence in the viewer panel."
        >
          <div className="max-h-96 space-y-2 overflow-y-auto">
            {(data.timeline || []).map((ev) => (
              <button
                key={ev.id}
                type="button"
                onClick={() => setSelectedEvent(ev)}
                className={`w-full rounded-lg border p-3 text-left text-sm hover:bg-slate-50 dark:hover:bg-slate-700/40 ${
                  selectedEvent?.id === ev.id
                    ? "border-teal-400 bg-teal-50/50 dark:border-teal-600 dark:bg-teal-950/20"
                    : "border-slate-100 dark:border-slate-700"
                }`}
              >
                <div className="flex justify-between">
                  <span className="font-medium">{ev.action}</span>
                  <span className="text-xs text-slate-400">{new Date(ev.created_at).toLocaleString()}</span>
                </div>
                <p className="mt-1 text-xs text-slate-400">
                  {ev.source}
                  {ev.key_prefix ? ` · key ${ev.key_prefix}` : ""}
                  {ev.model ? ` · ${ev.model}` : ""}
                </p>
                {metadataDetail(ev.metadata) && (
                  <p className="mt-1 line-clamp-2 text-xs text-slate-500">{metadataDetail(ev.metadata)}</p>
                )}
              </button>
            ))}
            {!data.timeline?.length && (
              <p className="py-6 text-center text-sm text-slate-400">No related enforcement events recorded.</p>
            )}
          </div>
        </ChartCard>

        <ChartCard
          title="Evidence Viewer"
          titleHelpText="Policy, rule, key, model, and prompt/response snippets for the selected event."
        >
          {selectedEvent ? (
            <div className="space-y-3 text-sm">
              <p className="text-xs text-slate-500 dark:text-slate-400">
                Selected event context (what fired, where it fired, and what identity/model was involved).
              </p>
              <div className="grid gap-2 sm:grid-cols-2">
                <div className="rounded-lg border border-slate-200 bg-slate-50/70 p-2 dark:border-slate-700 dark:bg-slate-800/40">
                  <p className="text-[11px] font-semibold uppercase text-slate-500">Source</p>
                  <p className="mt-0.5">{formatLaneDisplayLabel(selectedEvent.source || data.source)}</p>
                </div>
                <div className="rounded-lg border border-slate-200 bg-slate-50/70 p-2 dark:border-slate-700 dark:bg-slate-800/40">
                  <p className="text-[11px] font-semibold uppercase text-slate-500">Action</p>
                  <p className="mt-0.5 capitalize">{selectedEvent.action || "—"}</p>
                </div>
                <div className="rounded-lg border border-slate-200 bg-slate-50/70 p-2 dark:border-slate-700 dark:bg-slate-800/40">
                  <p className="text-[11px] font-semibold uppercase text-slate-500">Policy / Rule</p>
                  <p className="mt-0.5">{selectedEvent.policy_id || "—"} / {selectedEvent.rule_id || "—"}</p>
                </div>
                <div className="rounded-lg border border-slate-200 bg-slate-50/70 p-2 dark:border-slate-700 dark:bg-slate-800/40">
                  <p className="text-[11px] font-semibold uppercase text-slate-500">Identity</p>
                  <p className="mt-0.5">{selectedEvent.key_prefix || data?.evidence?.key_prefix || "—"}</p>
                </div>
                <div className="rounded-lg border border-slate-200 bg-slate-50/70 p-2 dark:border-slate-700 dark:bg-slate-800/40">
                  <p className="text-[11px] font-semibold uppercase text-slate-500">Project / Model</p>
                  <p className="mt-0.5">{selectedEvent.metadata?.project_id || data?.evidence?.project_id || "—"} / {selectedEvent.model || data?.evidence?.model || "—"}</p>
                </div>
                <div className="rounded-lg border border-slate-200 bg-slate-50/70 p-2 dark:border-slate-700 dark:bg-slate-800/40">
                  <p className="text-[11px] font-semibold uppercase text-slate-500">Threat Type</p>
                  <p className="mt-0.5">{selectedEvent.metadata?.threat_type || data?.evidence?.threat_type || "—"}</p>
                </div>
              </div>
              {selectedDetail && (
                <p><strong>Detail:</strong> {selectedDetail}</p>
              )}
              {promptPayload && (
                <details className="rounded-lg border border-slate-200 bg-slate-50/70 p-3 dark:border-slate-700 dark:bg-slate-800/40">
                  <summary className="cursor-pointer text-[11px] font-semibold uppercase tracking-wide text-slate-500">
                    Technical payload (JSON)
                  </summary>
                  <div className="mt-2 flex items-center justify-end gap-2">
                    <button
                      type="button"
                      onClick={() => copyToClipboard(JSON.stringify(promptPayload, null, 2))}
                      className="rounded border border-slate-300 px-2 py-0.5 text-[10px] font-medium text-slate-600 hover:bg-slate-100 dark:border-slate-600 dark:text-slate-300 dark:hover:bg-slate-700/50"
                    >
                      Copy JSON
                    </button>
                  </div>
                  <pre className="mt-2 max-h-44 overflow-auto rounded bg-slate-900 p-2 text-[10px] leading-relaxed text-emerald-300">
                    {JSON.stringify(promptPayload, null, 2)}
                  </pre>
                </details>
              )}
              {selectedEvent.metadata?.response_snippet && (
                <div className="rounded-lg border border-slate-200 bg-slate-50/70 p-3 dark:border-slate-700 dark:bg-slate-800/40">
                  <p className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-slate-500">
                    Response snippet
                  </p>
                  <pre className="max-h-36 overflow-auto whitespace-pre-wrap break-words font-sans text-xs text-slate-700 dark:text-slate-200">
                    {selectedEvent.metadata.response_snippet}
                  </pre>
                </div>
              )}
            </div>
          ) : (
            <p className="py-8 text-center text-sm text-slate-400">Select a timeline event to view evidence</p>
          )}
        </ChartCard>
      </div>
    </div>
  );
}
