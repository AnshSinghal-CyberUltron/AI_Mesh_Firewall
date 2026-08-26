import { useState, useEffect, useCallback } from "react";
import {
  Shield, ShieldCheck, ShieldAlert, Eye, Fingerprint, Brain,
  Key, FileWarning, Loader2, Activity, AlertTriangle,
} from "lucide-react";
import { useAuth } from "../context/AuthContext";
import { startVisibleInterval } from "../utils/visiblePoll.js";
import { InfoTooltip } from "./InfoTooltip";
import { TIME_RANGE_TO_HOURS } from "../hooks/useFirewallData";
import { normalizeCategory, OUTPUT_ACTION_COLORS } from "../constants/outputGuardColors";

// Bucket a real EnforcementEvent.action (block/redact/monitor/rewrite/
// model_downgrade) into the three card lanes, reusing the canonical color map
// as the single source of truth. Anything tinted red = blocked; amber/violet/
// orange (redact, rewrite, model_downgrade, flag, alert) = a content
// modification (folded into the "redacted/modified" lane, never "allowed");
// emerald (monitor, allow) = allowed. model_downgrade has no own color entry,
// so it's pinned to "modified" explicitly below.
const RED = OUTPUT_ACTION_COLORS.block;       // blocked
const EMERALD = OUTPUT_ACTION_COLORS.allow;   // allowed / monitored
function actionBucket(action) {
  const a = String(action || "").toLowerCase();
  if (a === "model_downgrade") return "redacted"; // response was altered (no own color)
  const color = OUTPUT_ACTION_COLORS[a];
  if (color === RED) return "blocked";
  if (color === EMERALD) return "allowed"; // monitor/monitored/allow/allowed
  if (color) return "redacted"; // redact, rewrite, flag, alert → content modified
  return "allowed"; // genuinely-unknown/unmapped → treat as allowed (fail-open)
}

const DETECTION_CATEGORIES = [
  { id: "pii", label: "PII Detection", icon: Fingerprint, description: "SSNs, emails, phone numbers, addresses" },
  { id: "credential", label: "Credential Exposure", icon: Key, description: "API keys, tokens, passwords, secrets" },
  { id: "hallucination", label: "Hallucination Scoring", icon: Brain, description: "Factuality check against context" },
  { id: "ip_leakage", label: "IP Leakage", icon: FileWarning, description: "Proprietary data, trade secrets, internal URLs" },
  { id: "secret", label: "Secret Detection", icon: Key, description: "Bearer tokens, AWS keys, connection strings" },
  { id: "data_leakage", label: "Data Leakage", icon: Eye, description: "Sensitive data exfiltration patterns" },
];

function CategoryCard({ category, stats }) {
  const Icon = category.icon;
  const count = stats[category.id] || 0;
  const isActive = count > 0;
  return (
    <div className={`flex items-start gap-3 p-3 rounded-xl border transition-colors ${
      isActive
        ? "border-amber-200 dark:border-amber-800 bg-amber-50/50 dark:bg-amber-900/20"
        : "border-slate-200 dark:border-slate-700 bg-white/50 dark:bg-slate-900/30"
    }`}>
      <div className={`p-1.5 rounded-lg ${isActive ? "bg-amber-100 dark:bg-amber-900/40" : "bg-slate-100 dark:bg-slate-800"}`}>
        <Icon className={`w-4 h-4 ${isActive ? "text-amber-600 dark:text-amber-400" : "text-slate-500 dark:text-slate-400"}`} />
      </div>
      <div className="flex-1 min-w-0">
        <div className="flex items-center justify-between">
          <span className="text-xs font-medium text-slate-700 dark:text-slate-200">{category.label}</span>
          {isActive && (
            <span className="text-[10px] font-semibold px-1.5 py-0.5 rounded bg-amber-100 dark:bg-amber-900/40 text-amber-700 dark:text-amber-300 tabular-nums">
              {count} event{count !== 1 ? "s" : ""}
            </span>
          )}
        </div>
        <p className="text-[10px] text-slate-500 dark:text-slate-400 mt-0.5">{category.description}</p>
      </div>
    </div>
  );
}

export function OutputGuardrailEngineCard({ timeRange = "24h" }) {
  const { fetchWithAuth } = useAuth();
  const [stats, setStats] = useState({});
  const [summary, setSummary] = useState({ total: 0, blocked: 0, redacted: 0, flagged: 0, allowed: 0 });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const fetchStats = useCallback(async () => {
    try {
      // Honor the operator-lens window (default 7d for §1.7) instead of a fixed
      // 24h, so the engine card stays consistent with the page KPIs + Evidence.
      const hours = TIME_RANGE_TO_HOURS[timeRange] || 24;
      const res = await fetchWithAuth(
        `/api/security/threat-feed/?hours=${hours}&limit=500&source=security_scan`
      );
      if (res.ok) {
        const data = await res.json();
        const outputEvents = (data.results || []).filter((ev) => {
          // String(...) guards against truthy non-string event_type values
          // (numbers/objects from older gateway payloads) throwing on toLowerCase.
          const et = String(ev.metadata?.event_type || "").toLowerCase();
          return et === "output_guard" || et === "output_scan";
        });

        const categoryStats = {};
        let blocked = 0, redacted = 0, flagged = 0, allowed = 0;

        for (const ev of outputEvents) {
          // Normalize the gateway threat vocabulary onto the six canonical
          // categories (credential_exposure→credential, hallucination_risk→…)
          // so the category cards reflect real counts instead of staying 0.
          const cat = normalizeCategory(ev.metadata?.threat_category || ev.metadata?.threat_type);
          categoryStats[cat] = (categoryStats[cat] || 0) + 1;
          // Bucket on the real action vocabulary via the canonical color map:
          // block→blocked, redact/rewrite/model_downgrade→redacted (modified),
          // monitor→allowed. Avoids the prior bug where rewrite/model_downgrade
          // silently counted as "allowed" and a non-existent "flag" was tallied.
          const bucket = actionBucket(ev.action);
          if (bucket === "blocked") blocked++;
          else if (bucket === "redacted") redacted++;
          else allowed++;
        }

        setStats(categoryStats);
        setSummary({ total: outputEvents.length, blocked, redacted, flagged, allowed });
        setError(null);
      } else {
        // A backend failure (e.g. 400 on bad params, 403/500/timeout) must stay
        // distinct from a successful-but-empty window, which renders "Idle".
        setError(`Failed to load engine status (HTTP ${res.status}).`);
      }
    } catch (err) {
      setError(`Failed to load engine status: ${err?.message || "request failed"}`);
    } finally {
      setLoading(false);
    }
  }, [fetchWithAuth, timeRange]);

  useEffect(() => { fetchStats(); }, [fetchStats]);

  useEffect(() => {
    return startVisibleInterval(fetchStats, 30_000);
  }, [fetchStats]);

  const engineActive = summary.total > 0;

  return (
    <div className="ai-mesh-card ai-mesh-grid-bg rounded-3xl p-6">
      <div className="flex items-center justify-between mb-4">
        <div>
          <h3 className="text-base font-semibold text-slate-900 dark:text-slate-100 flex items-center">
            Output Guardrail Engine
            <InfoTooltip title="Engine Status">
              {"The output guardrail engine inspects every model response through six detection categories before delivery. This card shows which categories have fired, total event counts, and action distribution. No black-box behavior — every decision is logged with full evidence."}
            </InfoTooltip>
          </h3>
          <p className="text-xs text-slate-500 dark:text-slate-400 mt-0.5">
            Detection categories, action distribution, and engine health ({timeRange} window)
          </p>
        </div>
        <div className={`flex items-center gap-1.5 px-2 py-1 rounded-full text-xs font-medium ${
          error
            ? "bg-red-100 dark:bg-red-900/30 text-red-700 dark:text-red-300"
            : engineActive
            ? "bg-emerald-100 dark:bg-emerald-900/30 text-emerald-700 dark:text-emerald-300"
            : "bg-slate-100 dark:bg-slate-800 text-slate-500 dark:text-slate-400"
        }`}>
          {error ? <AlertTriangle className="w-3 h-3" /> : <Activity className="w-3 h-3" />}
          {error ? "Error" : engineActive ? "Active" : "Idle"}
        </div>
      </div>

      {loading ? (
        <div className="flex items-center justify-center py-8">
          <Loader2 className="w-5 h-5 text-teal-500 animate-spin" />
          <span className="ml-2 text-sm text-slate-500 dark:text-slate-400">Loading engine status...</span>
        </div>
      ) : error ? (
        <div className="flex flex-col items-center gap-3 py-8 text-center">
          <div className="flex items-start gap-2 rounded-lg border border-red-200 bg-red-50 p-3 text-xs text-red-700 dark:border-red-800 dark:bg-red-900/20 dark:text-red-300">
            <AlertTriangle className="mt-0.5 h-4 w-4 flex-shrink-0" />
            <div>{error}</div>
          </div>
          <button
            onClick={fetchStats}
            className="rounded-lg border border-slate-200 px-3 py-1 text-xs font-medium text-slate-600 transition-colors hover:bg-slate-100 dark:border-slate-700 dark:text-slate-300 dark:hover:bg-slate-800"
          >
            Retry
          </button>
        </div>
      ) : (
        <>
          {/* Action distribution bar */}
          <div className="mb-4">
            <div className="flex items-center justify-between text-[11px] text-slate-500 dark:text-slate-400 mb-1">
              <span>{summary.total} total decisions</span>
              <span className="tabular-nums">
                {summary.blocked}B · {summary.redacted}R · {summary.flagged}F · {summary.allowed}A
              </span>
            </div>
            {summary.total > 0 ? (
              <div className="flex h-2 rounded-full overflow-hidden bg-slate-200 dark:bg-slate-700">
                {summary.blocked > 0 && (
                  <div className="bg-red-500" style={{ width: `${(summary.blocked / summary.total) * 100}%` }} title={`${summary.blocked} blocked`} />
                )}
                {summary.redacted > 0 && (
                  <div className="bg-amber-500" style={{ width: `${(summary.redacted / summary.total) * 100}%` }} title={`${summary.redacted} redacted`} />
                )}
                {summary.flagged > 0 && (
                  <div className="bg-orange-400" style={{ width: `${(summary.flagged / summary.total) * 100}%` }} title={`${summary.flagged} flagged`} />
                )}
                {summary.allowed > 0 && (
                  <div className="bg-emerald-500" style={{ width: `${(summary.allowed / summary.total) * 100}%` }} title={`${summary.allowed} allowed`} />
                )}
              </div>
            ) : (
              <div className="h-2 rounded-full bg-slate-200 dark:bg-slate-700" />
            )}
          </div>

          {/* Detection categories grid */}
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
            {DETECTION_CATEGORIES.map((cat) => (
              <CategoryCard key={cat.id} category={cat} stats={stats} />
            ))}
          </div>

          {/* Compliance tags summary */}
          {summary.total > 0 && (
            <div className="mt-3 pt-3 border-t border-slate-200 dark:border-slate-700">
              <span className="text-[10px] uppercase tracking-wider text-slate-500 dark:text-slate-400 font-semibold">Engine Capabilities</span>
              <div className="flex flex-wrap gap-1 mt-1">
                {["PII Redaction", "Secret Detection", "Hallucination Scoring", "IP Leakage Guard", "Streaming Scan", "Compliance Tagging"].map((cap) => (
                  <span key={cap} className="inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded text-[10px] bg-teal-50 dark:bg-teal-900/20 text-teal-700 dark:text-teal-300 border border-teal-200 dark:border-teal-800">
                    <ShieldCheck className="w-2.5 h-2.5" />{cap}
                  </span>
                ))}
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
}
