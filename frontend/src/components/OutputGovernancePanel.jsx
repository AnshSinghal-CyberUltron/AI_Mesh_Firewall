import { useState, useEffect, useCallback } from "react";
import {
  ShieldCheck, ShieldAlert, ShieldX, Eye, EyeOff,
  Clock, Loader2, RefreshCw, ChevronDown, ChevronUp,
} from "lucide-react";
import { useAuth } from "../context/AuthContext";
import { InfoTooltip } from "./InfoTooltip";
import { OutputPipelineTimeline } from "./OutputPipelineTimeline";

const ACTION_STYLES = {
  block: { bg: "bg-red-100 dark:bg-red-900/30", text: "text-red-700 dark:text-red-300", icon: ShieldX, label: "Blocked" },
  redact: { bg: "bg-amber-100 dark:bg-amber-900/30", text: "text-amber-700 dark:text-amber-300", icon: EyeOff, label: "Redacted" },
  flag: { bg: "bg-orange-100 dark:bg-orange-900/30", text: "text-orange-700 dark:text-orange-300", icon: ShieldAlert, label: "Flagged" },
  allow: { bg: "bg-emerald-100 dark:bg-emerald-900/30", text: "text-emerald-700 dark:text-emerald-300", icon: ShieldCheck, label: "Allowed" },
};

function ActionBadge({ action }) {
  const style = ACTION_STYLES[action] || ACTION_STYLES.allow;
  const Icon = style.icon;
  return (
    <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium ${style.bg} ${style.text}`}>
      <Icon className="w-3 h-3" />
      {style.label}
    </span>
  );
}

function ConfidenceMeter({ confidence }) {
  const pct = Math.round((confidence || 0) * 100);
  const color = pct >= 80 ? "bg-red-500" : pct >= 50 ? "bg-amber-500" : "bg-emerald-500";
  return (
    <div className="flex items-center gap-2">
      <div className="w-16 h-1.5 bg-slate-200 dark:bg-slate-700 rounded-full overflow-hidden">
        <div className={`h-full ${color} rounded-full`} style={{ width: `${pct}%` }} />
      </div>
      <span className="text-xs text-slate-500 dark:text-slate-400 tabular-nums">{pct}%</span>
    </div>
  );
}

function EventRow({ event, isExpanded, onToggle }) {
  const meta = event.metadata || {};
  const extra = meta.extra || {};
  const action = event.action || "allow";
  const threatType = meta.threat_category || meta.threat_type || extra.threat_type || "--";
  const detail = extra.detail || meta.detail || "";
  const promptSnippet = (meta.prompt_lineage && meta.prompt_lineage[0]?.prompt) || meta.prompt_snippet || "";
  const responseSnippet = extra.response_snippet || meta.response_snippet || "";
  const rawOutput = extra.raw_output || meta.raw_output || responseSnippet;
  const sanitizedOutput = extra.sanitized_output || meta.sanitized_output || "";
  const guardrailReasoning = extra.guardrail_reasoning || meta.guardrail_reasoning || detail;
  const confidence = meta.risk_score || meta.security_risk_score || 0;
  const model = meta.model || "--";
  const latency = meta.latency_ms || extra.latency_ms || 0;
  const complianceTags = meta.compliance_tags || extra.compliance_tags || [];
  const matchedPatterns = extra.matched_patterns || meta.matched_patterns || [];
  const ts = event.timestamp
    ? new Date(event.timestamp).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })
    : "--";

  return (
    <div className="border border-slate-200 dark:border-slate-700 rounded-2xl bg-white/65 dark:bg-slate-900/35 transition-colors hover:bg-white/85 dark:hover:bg-slate-900/55">
      <button
        onClick={onToggle}
        className="w-full flex items-center gap-3 p-3 text-left"
        aria-expanded={isExpanded}
      >
        <ActionBadge action={action} />
        <span className="text-xs font-medium text-slate-700 dark:text-slate-300 capitalize truncate max-w-[120px]">{threatType.replace(/_/g, " ")}</span>
        <ConfidenceMeter confidence={typeof confidence === "number" && confidence <= 1 ? confidence : (confidence / 100)} />
        <span className="text-xs text-slate-500 dark:text-slate-400 ml-auto tabular-nums">{ts}</span>
        <span className="text-xs text-slate-400 dark:text-slate-500 hidden sm:inline">{model}</span>
        {isExpanded ? <ChevronUp className="w-3.5 h-3.5 text-slate-400" /> : <ChevronDown className="w-3.5 h-3.5 text-slate-400" />}
      </button>

      {/* Inline evidence summary — always visible */}
      <div className="px-3 pb-2 space-y-1">
        {promptSnippet && (
          <div className="flex items-start gap-1.5">
            <span className="text-[10px] font-semibold text-blue-500 dark:text-blue-400 shrink-0 mt-0.5">PROMPT</span>
            <span className="text-[11px] text-slate-600 dark:text-slate-400 truncate">{promptSnippet.length > 120 ? promptSnippet.slice(0, 120) + "…" : promptSnippet}</span>
          </div>
        )}
        {rawOutput && (
          <div className="flex items-start gap-1.5">
            <span className="text-[10px] font-semibold text-purple-500 dark:text-purple-400 shrink-0 mt-0.5">OUTPUT</span>
            <span className="text-[11px] text-slate-600 dark:text-slate-400 truncate">{rawOutput.length > 120 ? rawOutput.slice(0, 120) + "…" : rawOutput}</span>
          </div>
        )}
        {guardrailReasoning && (
          <div className="flex items-start gap-1.5">
            <span className="text-[10px] font-semibold text-amber-500 dark:text-amber-400 shrink-0 mt-0.5">REASON</span>
            <span className="text-[11px] text-slate-600 dark:text-slate-400 truncate">{guardrailReasoning.length > 120 ? guardrailReasoning.slice(0, 120) + "…" : guardrailReasoning}</span>
          </div>
        )}
        {matchedPatterns.length > 0 && (
          <div className="flex flex-wrap gap-1 mt-0.5">
            {matchedPatterns.map((p, i) => (
              <span key={i} className="px-1.5 py-0.5 rounded text-[9px] bg-red-50 dark:bg-red-900/20 text-red-600 dark:text-red-400 border border-red-200 dark:border-red-800">{p}</span>
            ))}
          </div>
        )}
      </div>

      {isExpanded && (
        <div className="px-3 pb-3 border-t border-slate-100 dark:border-slate-800 pt-2">
          <OutputPipelineTimeline event={event} />
        </div>
      )}
    </div>
  );
}

export function OutputGovernancePanel() {
  const { fetchWithAuth } = useAuth();
  const [events, setEvents] = useState([]);
  const [loading, setLoading] = useState(true);
  const [expandedId, setExpandedId] = useState(null);
  const [autoRefresh, setAutoRefresh] = useState(true);
  const POLL_INTERVAL = 10_000;

  const fetchEvents = useCallback(async () => {
    try {
      const res = await fetchWithAuth(
        "/api/security/threat-feed/?hours=24&limit=50&source=security_scan"
      );
      if (res.ok) {
        const data = await res.json();
        const outputEvents = (data.results || []).filter((ev) => {
          const et = (ev.metadata?.event_type || "").toLowerCase();
          return et === "output_guard" || et === "output_scan";
        });
        setEvents(outputEvents);
      }
    } finally {
      setLoading(false);
    }
  }, [fetchWithAuth]);

  useEffect(() => {
    fetchEvents();
  }, [fetchEvents]);

  useEffect(() => {
    if (!autoRefresh) return;
    const id = setInterval(fetchEvents, POLL_INTERVAL);
    return () => clearInterval(id);
  }, [autoRefresh, fetchEvents]);

  const blocked = events.filter((e) => e.action === "block").length;
  const redacted = events.filter((e) => e.action === "redact").length;
  const flagged = events.filter((e) => e.action === "flag").length;

  return (
    <div className="ai-mesh-card ai-mesh-grid-bg rounded-3xl p-6">
      <div className="flex items-center justify-between mb-4">
        <div>
          <h3 className="text-base font-semibold text-slate-900 dark:text-slate-100 flex items-center">
            Output Governance Log
            <InfoTooltip title="Output Governance">
              {"Real-time feed of output guardrail decisions. Every model response is inspected for PII, credentials, hallucinations, and IP leakage. Expand a row to see the prompt, raw output, detection detail, and action taken."}
            </InfoTooltip>
          </h3>
          <p className="text-xs text-slate-500 dark:text-slate-400 mt-0.5">
            Real-time output-governance evidence — prompt, raw output, detected risks, action, final output
          </p>
        </div>
        <div className="flex items-center gap-2">
          <div className="flex gap-1.5 text-[11px]">
            <span className="px-1.5 py-0.5 rounded bg-red-100 dark:bg-red-900/30 text-red-700 dark:text-red-300 tabular-nums">{blocked} blocked</span>
            <span className="px-1.5 py-0.5 rounded bg-amber-100 dark:bg-amber-900/30 text-amber-700 dark:text-amber-300 tabular-nums">{redacted} redacted</span>
            <span className="px-1.5 py-0.5 rounded bg-orange-100 dark:bg-orange-900/30 text-orange-700 dark:text-orange-300 tabular-nums">{flagged} flagged</span>
          </div>
          <button
            onClick={() => { setAutoRefresh((p) => !p); if (!autoRefresh) fetchEvents(); }}
            className={`p-1.5 rounded-lg transition-colors ${autoRefresh ? "bg-teal-100 dark:bg-teal-900/30 text-teal-600" : "bg-slate-100 dark:bg-slate-800 text-slate-400"}`}
            title={autoRefresh ? "Auto-refresh ON (10s)" : "Auto-refresh OFF"}
          >
            <RefreshCw className={`w-3.5 h-3.5 ${autoRefresh ? "animate-spin" : ""}`} style={autoRefresh ? { animationDuration: "3s" } : {}} />
          </button>
        </div>
      </div>

      {loading ? (
        <div className="flex items-center justify-center py-8">
          <Loader2 className="w-5 h-5 text-teal-500 animate-spin" />
          <span className="ml-2 text-sm text-slate-500 dark:text-slate-400">Loading output events...</span>
        </div>
      ) : events.length === 0 ? (
        <div className="text-center py-8 text-sm text-slate-500 dark:text-slate-400">
          <Eye className="w-8 h-8 mx-auto mb-2 text-slate-300 dark:text-slate-600" />
          No output-guard events in the last 24 hours. Send a prompt through the gateway to generate evidence.
        </div>
      ) : (
        <div className="space-y-2 max-h-[420px] overflow-y-auto pr-1">
          {events.map((ev) => (
            <EventRow
              key={ev.id}
              event={ev}
              isExpanded={expandedId === ev.id}
              onToggle={() => setExpandedId(expandedId === ev.id ? null : ev.id)}
            />
          ))}
        </div>
      )}
    </div>
  );
}
