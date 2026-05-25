import { useState, useEffect, useCallback } from "react";
import {
  Shield, ShieldCheck, ShieldAlert, Eye, Fingerprint, Brain,
  Key, FileWarning, Loader2, Activity,
} from "lucide-react";
import { useAuth } from "../context/AuthContext";
import { InfoTooltip } from "./InfoTooltip";

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
        <Icon className={`w-4 h-4 ${isActive ? "text-amber-600 dark:text-amber-400" : "text-slate-400 dark:text-slate-500"}`} />
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
        <p className="text-[10px] text-slate-400 dark:text-slate-500 mt-0.5">{category.description}</p>
      </div>
    </div>
  );
}

export function OutputGuardrailEngineCard() {
  const { fetchWithAuth } = useAuth();
  const [stats, setStats] = useState({});
  const [summary, setSummary] = useState({ total: 0, blocked: 0, redacted: 0, flagged: 0, allowed: 0 });
  const [loading, setLoading] = useState(true);

  const fetchStats = useCallback(async () => {
    try {
      const res = await fetchWithAuth(
        "/api/security/threat-feed/?hours=24&limit=100&source=security_scan"
      );
      if (res.ok) {
        const data = await res.json();
        const outputEvents = (data.results || []).filter((ev) => {
          const et = (ev.metadata?.event_type || "").toLowerCase();
          return et === "output_guard" || et === "output_scan";
        });

        const categoryStats = {};
        let blocked = 0, redacted = 0, flagged = 0, allowed = 0;

        for (const ev of outputEvents) {
          const tt = (ev.metadata?.threat_category || ev.metadata?.threat_type || "").toLowerCase();
          if (tt) categoryStats[tt] = (categoryStats[tt] || 0) + 1;
          if (ev.action === "block") blocked++;
          else if (ev.action === "redact") redacted++;
          else if (ev.action === "flag") flagged++;
          else allowed++;
        }

        setStats(categoryStats);
        setSummary({ total: outputEvents.length, blocked, redacted, flagged, allowed });
      }
    } finally {
      setLoading(false);
    }
  }, [fetchWithAuth]);

  useEffect(() => { fetchStats(); }, [fetchStats]);

  useEffect(() => {
    const id = setInterval(fetchStats, 30_000);
    return () => clearInterval(id);
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
            Detection categories, action distribution, and engine health (24h window)
          </p>
        </div>
        <div className={`flex items-center gap-1.5 px-2 py-1 rounded-full text-xs font-medium ${
          engineActive
            ? "bg-emerald-100 dark:bg-emerald-900/30 text-emerald-700 dark:text-emerald-300"
            : "bg-slate-100 dark:bg-slate-800 text-slate-500 dark:text-slate-400"
        }`}>
          <Activity className="w-3 h-3" />
          {engineActive ? "Active" : "Idle"}
        </div>
      </div>

      {loading ? (
        <div className="flex items-center justify-center py-8">
          <Loader2 className="w-5 h-5 text-teal-500 animate-spin" />
          <span className="ml-2 text-sm text-slate-500 dark:text-slate-400">Loading engine status...</span>
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
              <span className="text-[10px] uppercase tracking-wider text-slate-400 dark:text-slate-500 font-semibold">Engine Capabilities</span>
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
