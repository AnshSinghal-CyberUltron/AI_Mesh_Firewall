import { useState, useEffect, useCallback } from "react";
import {
  ShieldCheck, ShieldAlert, ShieldX, Eye, EyeOff,
  Clock, Loader2, RefreshCw, ChevronDown, ChevronUp,
} from "lucide-react";
import { useAuth } from "../context/AuthContext";
import { startVisibleInterval } from "../utils/visiblePoll.js";
import { InfoTooltip } from "./InfoTooltip";
import { OutputPipelineTimeline } from "./OutputPipelineTimeline";
import { TIME_RANGE_TO_HOURS } from "../hooks/useFirewallData";
import { isRedactNoop, selectOutputGovernanceEvents } from "../utils/outputGovernanceFeed";

// Extracted so each state carries its own bg+text pair (not a ternary
// cross-product) — keeps the detector's gray-on-color heuristic honest and
// lifts the muted OFF state to an AA-legible slate.
const AUTO_REFRESH_STYLES = {
  on: "bg-teal-100 dark:bg-teal-900/30 text-teal-600 dark:text-teal-300",
  off: "bg-slate-100 dark:bg-slate-800 text-slate-500 dark:text-slate-400",
};

const ACTION_STYLES = {
  block: { bg: "bg-red-100 dark:bg-red-900/30", text: "text-red-700 dark:text-red-300", icon: ShieldX, label: "Blocked" },
  redact: { bg: "bg-amber-100 dark:bg-amber-900/30", text: "text-amber-700 dark:text-amber-300", icon: EyeOff, label: "Redacted" },
  // rewrite was MISSING here, so a rewritten response fell back to ACTION_STYLES.allow
  // and rendered the badge as "Allowed" in the Output Governance Log (2026-07-16).
  rewrite: { bg: "bg-blue-100 dark:bg-blue-900/30", text: "text-blue-700 dark:text-blue-300", icon: RefreshCw, label: "Rewritten" },
  flag: { bg: "bg-orange-100 dark:bg-orange-900/30", text: "text-orange-700 dark:text-orange-300", icon: ShieldAlert, label: "Flagged" },
  allow: { bg: "bg-emerald-100 dark:bg-emerald-900/30", text: "text-emerald-700 dark:text-emerald-300", icon: ShieldCheck, label: "Allowed" },
};

function ActionBadge({ action }) {
  const style = ACTION_STYLES[String(action || "").toLowerCase()] || ACTION_STYLES.allow;
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
  const redactNoop = isRedactNoop(event);
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
        {redactNoop && (
          <span className="px-1.5 py-0.5 rounded text-[10px] font-medium bg-slate-100 dark:bg-slate-800 text-slate-600 dark:text-slate-300 border border-slate-200 dark:border-slate-700">
            No bytes changed
          </span>
        )}
        <span className="text-xs font-medium text-slate-700 dark:text-slate-300 capitalize truncate max-w-[120px]">{threatType.replace(/_/g, " ")}</span>
        <ConfidenceMeter confidence={typeof confidence === "number" && confidence <= 1 ? confidence : (confidence / 100)} />
        <span className="text-xs text-slate-500 dark:text-slate-400 ml-auto tabular-nums">{ts}</span>
        <span className="text-xs text-slate-500 dark:text-slate-400 hidden sm:inline">{model}</span>
        {isExpanded ? <ChevronUp className="w-3.5 h-3.5 text-slate-400" /> : <ChevronDown className="w-3.5 h-3.5 text-slate-400" />}
      </button>

      {/* Inline evidence summary — always visible */}
      <div className="px-3 pb-2 space-y-1">
        {promptSnippet && (
          <div className="flex items-start gap-1.5">
            <span className="text-[10px] font-semibold text-blue-600 dark:text-blue-400 shrink-0 mt-0.5">PROMPT</span>
            <span className="text-[11px] text-slate-600 dark:text-slate-400 truncate">{promptSnippet.length > 120 ? promptSnippet.slice(0, 120) + "…" : promptSnippet}</span>
          </div>
        )}
        {rawOutput && (
          <div className="flex items-start gap-1.5">
            <span className="text-[10px] font-semibold text-purple-600 dark:text-purple-400 shrink-0 mt-0.5">OUTPUT</span>
            <span className="text-[11px] text-slate-600 dark:text-slate-400 truncate">{rawOutput.length > 120 ? rawOutput.slice(0, 120) + "…" : rawOutput}</span>
          </div>
        )}
        {guardrailReasoning && (
          <div className="flex items-start gap-1.5">
            <span className="text-[10px] font-semibold text-amber-600 dark:text-amber-400 shrink-0 mt-0.5">REASON</span>
            <span className="text-[11px] text-slate-600 dark:text-slate-400 truncate">{guardrailReasoning.length > 120 ? guardrailReasoning.slice(0, 120) + "…" : guardrailReasoning}</span>
          </div>
        )}
        {matchedPatterns.length > 0 && (
          <div className="flex flex-wrap gap-1 mt-0.5">
            {matchedPatterns.map((p, i) => (
              <span key={i} className="px-1.5 py-0.5 rounded text-[10px] bg-red-50 dark:bg-red-900/20 text-red-600 dark:text-red-400 border border-red-200 dark:border-red-800">{p}</span>
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

export function OutputGovernancePanel({ timeRange = "24h" }) {
  const { fetchWithAuth } = useAuth();
  const [events, setEvents] = useState([]);
  const [loading, setLoading] = useState(true);
  const [expandedId, setExpandedId] = useState(null);
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [actionFilter, setActionFilter] = useState("all");
  const POLL_INTERVAL = 10_000;

  const fetchEvents = useCallback(async () => {
    try {
      // Lens-driven window (default 7d for §1.7) so the log matches the page
      // KPIs + Evidence instead of a fixed 24h that ages out older events.
      const hours = TIME_RANGE_TO_HOURS[timeRange] || 24;
      // limit=500 (not 50): the feed is filtered to output events client-side, so
      // a small page can be entirely crowded out by recent non-output traffic.
      const res = await fetchWithAuth(
        `/api/security/threat-feed/?hours=${hours}&limit=500&source=security_scan&collapse=false`
      );
      if (res.ok) {
        const data = await res.json();
        setEvents(selectOutputGovernanceEvents(data.results || []));
      }
    } finally {
      setLoading(false);
    }
  }, [fetchWithAuth, timeRange]);

  useEffect(() => {
    fetchEvents();
  }, [fetchEvents]);

  useEffect(() => {
    if (!autoRefresh) return;
    return startVisibleInterval(fetchEvents, POLL_INTERVAL);
  }, [autoRefresh, fetchEvents]);

  const blocked = events.filter((e) => e.action === "block").length;
  const redacted = events.filter((e) => e.action === "redact").length;
  const flagged = events.filter((e) => e.action === "flag").length;

  const FILTERS = [
    { id: "all", label: "All", count: events.length },
    { id: "block", label: "Block", count: blocked },
    { id: "redact", label: "Redact", count: redacted },
    { id: "flag", label: "Flag", count: flagged },
    { id: "allow", label: "Allow", count: events.filter((e) => !["block", "redact", "flag"].includes(e.action)).length },
  ];
  const filtered =
    actionFilter === "all"
      ? events
      : actionFilter === "allow"
        ? events.filter((e) => !["block", "redact", "flag"].includes(e.action))
        : events.filter((e) => e.action === actionFilter);

  return (
    <div className="ai-mesh-card ai-mesh-grid-bg rounded-3xl p-6">
      <div className="flex flex-wrap items-center justify-between gap-3 mb-4">
        <div className="min-w-0">
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
        <div className="flex flex-wrap items-center gap-2">
          <div className="flex gap-1.5 text-[11px]">
            <span className="px-1.5 py-0.5 rounded bg-red-100 dark:bg-red-900/30 text-red-700 dark:text-red-300 tabular-nums">{blocked} blocked</span>
            <span className="px-1.5 py-0.5 rounded bg-amber-100 dark:bg-amber-900/30 text-amber-700 dark:text-amber-300 tabular-nums">{redacted} redacted</span>
            <span className="px-1.5 py-0.5 rounded bg-orange-100 dark:bg-orange-900/30 text-orange-700 dark:text-orange-300 tabular-nums">{flagged} flagged</span>
          </div>
          <button
            onClick={() => { setAutoRefresh((p) => !p); if (!autoRefresh) fetchEvents(); }}
            className={`p-1.5 rounded-lg transition-colors ${autoRefresh ? AUTO_REFRESH_STYLES.on : AUTO_REFRESH_STYLES.off}`}
            aria-label={autoRefresh ? "Turn off output event auto-refresh" : "Turn on output event auto-refresh"}
            title={autoRefresh ? "Auto-refresh ON (10s)" : "Auto-refresh OFF"}
          >
            <RefreshCw className={`w-3.5 h-3.5 ${autoRefresh ? "animate-spin" : ""}`} style={autoRefresh ? { animationDuration: "3s" } : {}} />
          </button>
        </div>
      </div>

      {/* Action filter chips — additive client-side filter, removes nothing */}
      {!loading && events.length > 0 && (
        <div className="mb-3 flex flex-wrap gap-1.5">
          {FILTERS.map((f) => (
            <button
              key={f.id}
              onClick={() => setActionFilter(f.id)}
              className={`inline-flex cursor-pointer items-center gap-1 rounded-full px-2.5 py-1 text-[11px] font-medium transition-colors ${
                actionFilter === f.id
                  ? "bg-teal-100 text-teal-700 ring-1 ring-teal-300 dark:bg-teal-900/40 dark:text-teal-300 dark:ring-teal-700"
                  : "bg-slate-100 text-slate-500 hover:bg-slate-200 dark:bg-slate-800 dark:text-slate-400 dark:hover:bg-slate-700"
              }`}
            >
              {f.label}
              <span className="tabular-nums opacity-70">{f.count}</span>
            </button>
          ))}
        </div>
      )}

      {loading ? (
        <div className="flex items-center justify-center py-8">
          <Loader2 className="w-5 h-5 text-teal-500 animate-spin" />
          <span className="ml-2 text-sm text-slate-500 dark:text-slate-400">Loading output events...</span>
        </div>
      ) : events.length === 0 ? (
        <div className="text-center py-8 text-sm text-slate-500 dark:text-slate-400">
          <Eye className="w-8 h-8 mx-auto mb-2 text-slate-300 dark:text-slate-600" />
          No output-guard events in the selected {timeRange} window. Send a prompt through the gateway to generate evidence.
        </div>
      ) : filtered.length === 0 ? (
        <div className="text-center py-8 text-sm text-slate-500 dark:text-slate-400">
          <Eye className="w-8 h-8 mx-auto mb-2 text-slate-300 dark:text-slate-600" />
          No {actionFilter} events in this window. Clear the filter to see all {events.length} events.
        </div>
      ) : (
        <div className="space-y-2 max-h-[420px] overflow-y-auto pr-1">
          {filtered.map((ev) => (
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
