import { useState, useEffect, useCallback } from "react";
import {
  Activity, Shield, ShieldOff, ShieldAlert, RotateCcw,
  Loader2, AlertTriangle, CheckCircle, Zap, Settings,
  ChevronDown, ChevronUp, Clock, TrendingUp,
} from "lucide-react";
import { useAuth } from "../context/AuthContext";
import { InfoTooltip } from "./InfoTooltip";

const STATUS_COLORS = {
  active: { bg: "bg-emerald-500/10", border: "border-emerald-500/30", text: "text-emerald-600 dark:text-emerald-400", icon: CheckCircle },
  isolated: { bg: "bg-red-500/10", border: "border-red-500/30", text: "text-red-600 dark:text-red-400", icon: ShieldOff },
  degraded: { bg: "bg-amber-500/10", border: "border-amber-500/30", text: "text-amber-600 dark:text-amber-400", icon: ShieldAlert },
};

function RiskGauge({ score, threshold }) {
  const pct = Math.min(score, 100);
  const threshPct = Math.min(threshold, 100);
  const color = pct >= threshold ? "bg-red-500" : pct >= threshold * 0.7 ? "bg-amber-500" : "bg-emerald-500";

  return (
    <div className="relative w-full h-3 bg-slate-200 dark:bg-slate-700 rounded-full overflow-visible">
      <div className={`h-full rounded-full transition-all duration-500 ${color}`} style={{ width: `${pct}%` }} />
      <div
        className="absolute top-0 h-full w-0.5 bg-red-600 dark:bg-red-400"
        style={{ left: `${threshPct}%` }}
        title={`Threshold: ${threshold}`}
      />
      <div
        className="absolute -top-4 text-[9px] font-bold text-red-600 dark:text-red-400"
        style={{ left: `${threshPct}%`, transform: "translateX(-50%)" }}
      >
        T:{threshold}
      </div>
    </div>
  );
}

export function ModelStatePanel() {
  const { fetchWithAuth, user } = useAuth();
  const orgSlug = user?.organization?.slug || "";
  const [models, setModels] = useState([]);
  const [auditLogs, setAuditLogs] = useState([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState(null);
  const [syncing, setSyncing] = useState(false);
  const [actionLoading, setActionLoading] = useState(null);
  const [expandedModel, setExpandedModel] = useState(null);
  const [showAudit, setShowAudit] = useState(false);
  const [polling, setPolling] = useState(true);

  const fetchModelStates = useCallback(async () => {
    setLoadError(null);
    try {
      const res = await fetchWithAuth("/api/models/status/");
      if (!res.ok) {
        setLoadError("Could not load model states.");
        return;
      }
      const dbData = await res.json();
      const dbModels = Array.isArray(dbData) ? dbData : dbData.results || dbData.models || [];

      setModels(dbModels);
    } catch {
      setLoadError("Network error loading model states.");
    }
  }, [fetchWithAuth]);

  const handleSyncStates = async () => {
    setSyncing(true);
    setLoadError(null);
    try {
      const res = await fetchWithAuth("/api/models/sync/", { method: "POST" });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        setLoadError(err.error || "Sync failed.");
        return;
      }
      const data = await res.json();
      setModels(data.models || []);
    } catch {
      setLoadError("Network error during sync.");
    } finally {
      setSyncing(false);
    }
  };

  const fetchAuditLogs = useCallback(async () => {
    try {
      const res = await fetchWithAuth("/api/models/audit-log/");
      if (res.ok) {
        const data = await res.json();
        setAuditLogs(Array.isArray(data) ? data : data.results || []);
      }
    } catch {
      /* fail silently */
    }
  }, [fetchWithAuth]);

  useEffect(() => {
    const init = async () => {
      setLoading(true);
      await Promise.all([fetchModelStates(), fetchAuditLogs()]);
      setLoading(false);
    };
    init();
  }, [fetchModelStates, fetchAuditLogs]);

  // Auto-poll every 5 seconds
  useEffect(() => {
    if (!polling) return;
    const id = setInterval(() => {
      fetchModelStates();
      fetchAuditLogs();
    }, 5000);
    return () => clearInterval(id);
  }, [polling, fetchModelStates, fetchAuditLogs]);

  const handleIsolate = async (modelName) => {
    setActionLoading(modelName);
    try {
      await fetchWithAuth("/api/models/isolate/", {
        method: "POST",
        body: JSON.stringify({
          model_name: modelName,
          action: "block",
          reason: "Manual isolation from dashboard",
        }),
      });
      await fetchModelStates();
      await fetchAuditLogs();
    } finally {
      setActionLoading(null);
    }
  };

  const handleRecover = async (modelName) => {
    setActionLoading(modelName);
    try {
      await fetchWithAuth(`/api/models/recover/${modelName}/`, { method: "POST" });
      await fetchModelStates();
      await fetchAuditLogs();
    } finally {
      setActionLoading(null);
    }
  };

  const handleThresholdChange = async (modelName, newThreshold) => {
    try {
      const row = models.find((x) => x.model_name === modelName);
      if (row && row.id == null) {
        await handleSyncStates();
      }
      const res = await fetchWithAuth(`/api/models/status/${encodeURIComponent(modelName)}/`, {
        method: "PATCH",
        body: JSON.stringify({ threshold: newThreshold }),
      });
      if (!res.ok) {
        setLoadError("Could not update threshold. Try Sync states first.");
        return;
      }
      await fetchModelStates();
    } catch {
      setLoadError("Network error updating threshold.");
    }
  };

  const handleActionChange = async (modelName, newAction) => {
    try {
      const row = models.find((x) => x.model_name === modelName);
      if (row && row.id == null) {
        await handleSyncStates();
      }
      const res = await fetchWithAuth(`/api/models/status/${encodeURIComponent(modelName)}/`, {
        method: "PATCH",
        body: JSON.stringify({ action: newAction }),
      });
      if (!res.ok) {
        setLoadError("Could not update action. Try Sync states first.");
        return;
      }
      await fetchModelStates();
    } catch {
      setLoadError("Network error updating action.");
    }
  };

  const isolatedCount = models.filter((m) => m.status === "isolated").length;
  const degradedCount = models.filter((m) => m.status === "degraded").length;
  const totalModels = models.length;

  return (
    <div className="ai-mesh-card ai-mesh-grid-bg rounded-3xl p-6">
      {/* Header */}
      <div className="flex items-center justify-between mb-4">
        <div>
          <h3 className="text-base font-semibold text-slate-900 dark:text-slate-100 flex items-center">
            Model State & Risk Monitor
            <InfoTooltip title="Real-Time Model Health">
              {"Live model risk monitoring with auto-isolation. Models exceeding their risk threshold are automatically isolated. Recovery happens after cooldown period or manual intervention."}
            </InfoTooltip>
          </h3>
          <p className="text-xs text-slate-500 dark:text-slate-400 mt-0.5">
            Real-time org-scoped model health with rolling risk scores
            {orgSlug ? (
              <span className="ml-1 font-mono text-teal-700 dark:text-teal-300">({orgSlug})</span>
            ) : null}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={handleSyncStates}
            disabled={syncing}
            className="flex min-h-[44px] items-center gap-1.5 rounded-xl bg-slate-100 px-3 py-2 text-xs font-medium text-slate-700 hover:bg-slate-200 disabled:opacity-60 dark:bg-slate-800 dark:text-slate-200 dark:hover:bg-slate-700"
            title="Create ModelState rows from active Model Connections"
          >
            {syncing ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <RotateCcw className="w-3.5 h-3.5" />}
            Sync states
          </button>
          <button
            onClick={() => setShowAudit(!showAudit)}
            className={`flex items-center gap-1.5 rounded-xl px-3 py-2 text-xs font-medium transition-colors ${
              showAudit
                ? "bg-teal-600 text-white hover:bg-teal-700"
                : "bg-slate-100 dark:bg-slate-800 text-slate-600 dark:text-slate-300 hover:bg-slate-200 dark:hover:bg-slate-700"
            }`}
          >
            <Clock className="w-3.5 h-3.5" />
            Audit Log
          </button>
          <button
            onClick={() => setPolling(!polling)}
            className={`flex items-center gap-1.5 rounded-xl px-3 py-2 text-xs font-medium transition-colors ${
              polling
                ? "bg-emerald-600 text-white hover:bg-emerald-700"
                : "bg-slate-100 dark:bg-slate-800 text-slate-600 dark:text-slate-300 hover:bg-slate-200 dark:hover:bg-slate-700"
            }`}
          >
            <Activity className="w-3.5 h-3.5" />
            {polling ? "Live" : "Paused"}
          </button>
        </div>
      </div>

      {/* Summary bar */}
      <div className="flex items-center gap-4 mb-4 p-3 rounded-xl bg-slate-50/80 dark:bg-slate-800/50 border border-slate-200 dark:border-slate-700">
        <div className="flex items-center gap-1.5">
          <Shield className="w-4 h-4 text-emerald-500" />
          <span className="text-xs font-semibold text-slate-700 dark:text-slate-300">{totalModels - isolatedCount - degradedCount} Active</span>
        </div>
        {isolatedCount > 0 && (
          <div className="flex items-center gap-1.5">
            <ShieldOff className="w-4 h-4 text-red-500" />
            <span className="text-xs font-semibold text-red-600 dark:text-red-400">{isolatedCount} Isolated</span>
          </div>
        )}
        {degradedCount > 0 && (
          <div className="flex items-center gap-1.5">
            <ShieldAlert className="w-4 h-4 text-amber-500" />
            <span className="text-xs font-semibold text-amber-600 dark:text-amber-400">{degradedCount} Degraded</span>
          </div>
        )}
        <div className="ml-auto text-[10px] text-slate-400">{polling ? "Auto-refresh: 5s" : "Paused"}</div>
      </div>

      {loadError && (
        <div className="mb-4 rounded-xl border border-red-500/30 bg-red-500/10 px-3 py-2 text-sm text-red-800 dark:text-red-200" role="alert">
          {loadError}
        </div>
      )}

      {loading ? (
        <div className="flex items-center justify-center py-8">
          <Loader2 className="w-5 h-5 text-teal-500 animate-spin" />
          <span className="ml-2 text-sm text-slate-500 dark:text-slate-400">Loading model states...</span>
        </div>
      ) : models.length === 0 ? (
        <div className="text-center py-8 text-sm text-slate-500 dark:text-slate-400 space-y-3">
          <p>No model states yet. Connect models on Module 1.5, then sync to enable risk monitoring.</p>
          <button
            type="button"
            onClick={handleSyncStates}
            disabled={syncing}
            className="inline-flex min-h-[44px] items-center gap-2 rounded-xl bg-teal-600 px-4 py-2 text-xs font-medium text-white hover:bg-teal-700 disabled:opacity-60"
          >
            {syncing ? <Loader2 className="w-4 h-4 animate-spin" /> : <RotateCcw className="w-4 h-4" />}
            Sync from Model Connections
          </button>
        </div>
      ) : (
        <div className="space-y-3">
          {models
            .sort((a, b) => (b.risk_score || 0) - (a.risk_score || 0))
            .map((m) => {
              const sc = STATUS_COLORS[m.status] || STATUS_COLORS.active;
              const StatusIcon = sc.icon;
              const isExpanded = expandedModel === m.model_name;

              return (
                <div
                  key={m.model_name}
                  className={`rounded-xl border ${sc.border} ${sc.bg} transition-all`}
                >
                  {/* Main row */}
                  <div className="flex items-center gap-3 px-4 py-3">
                    <StatusIcon className={`w-5 h-5 ${sc.text} flex-shrink-0`} />
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2 mb-1.5">
                        <span className="font-mono text-sm font-semibold text-slate-900 dark:text-slate-100">{m.model_name}</span>
                        {m.is_bootstrapped === false && (
                          <span className="px-1.5 py-0.5 rounded-md text-[10px] font-medium bg-slate-200 text-slate-600 dark:bg-slate-700 dark:text-slate-300">
                            pending sync
                          </span>
                        )}
                        <span className={`px-1.5 py-0.5 rounded-md text-[10px] font-bold uppercase ${sc.text} border ${sc.border}`}>
                          {m.status}
                        </span>
                        {m.status === "isolated" && m.isolated_until && (
                          <span className="text-[10px] text-slate-500 dark:text-slate-400">
                            until {new Date(m.isolated_until).toLocaleTimeString()}
                          </span>
                        )}
                      </div>
                      <div className="flex items-center gap-4">
                        <div className="flex-1">
                          <RiskGauge score={m.risk_score || 0} threshold={m.threshold || 80} />
                        </div>
                        <div className="text-right flex-shrink-0">
                          <div className={`text-lg font-bold ${(m.risk_score || 0) >= (m.threshold || 80) ? "text-red-600 dark:text-red-400" : "text-slate-800 dark:text-slate-200"}`}>
                            {(m.risk_score || 0).toFixed(1)}
                          </div>
                          <div className="text-[9px] text-slate-400 uppercase tracking-wider">Risk Score</div>
                        </div>
                      </div>
                    </div>
                    <div className="flex items-center gap-1 flex-shrink-0">
                      {actionLoading === m.model_name ? (
                        <Loader2 className="w-4 h-4 text-teal-500 animate-spin" />
                      ) : m.status === "isolated" ? (
                        <button
                          onClick={() => handleRecover(m.model_name)}
                          className="flex items-center gap-1 px-2.5 py-1.5 rounded-lg text-xs font-medium bg-emerald-500/20 hover:bg-emerald-500/30 text-emerald-700 dark:text-emerald-400 transition-colors"
                          title="Recover this model"
                        >
                          <RotateCcw className="w-3 h-3" /> Recover
                        </button>
                      ) : (
                        <button
                          onClick={() => handleIsolate(m.model_name)}
                          className="flex items-center gap-1 px-2.5 py-1.5 rounded-lg text-xs font-medium bg-red-500/20 hover:bg-red-500/30 text-red-700 dark:text-red-400 transition-colors"
                          title="Isolate this model"
                        >
                          <ShieldOff className="w-3 h-3" /> Isolate
                        </button>
                      )}
                      <button
                        onClick={() => setExpandedModel(isExpanded ? null : m.model_name)}
                        className="p-1.5 hover:bg-slate-100 dark:hover:bg-slate-700 rounded-lg text-slate-500 transition-colors"
                        title="Model settings"
                      >
                        {isExpanded ? <ChevronUp className="w-3.5 h-3.5" /> : <ChevronDown className="w-3.5 h-3.5" />}
                      </button>
                    </div>
                  </div>

                  {/* Expanded settings */}
                  {isExpanded && (
                    <div className="px-4 pb-3 pt-1 border-t border-slate-200/50 dark:border-slate-700/50">
                      <div className="grid grid-cols-3 gap-4">
                        <div>
                          <label className="block text-[10px] text-slate-500 dark:text-slate-400 font-medium mb-1 uppercase tracking-wider">
                            Risk Threshold
                          </label>
                          <div className="flex items-center gap-2">
                            <input
                              type="range"
                              min="10"
                              max="100"
                              step="5"
                              value={m.threshold || 80}
                              onChange={(e) => handleThresholdChange(m.model_name, parseFloat(e.target.value))}
                              className="flex-1 h-1.5 accent-teal-500"
                            />
                            <span className="text-xs font-mono font-semibold text-slate-700 dark:text-slate-300 w-8 text-right">{m.threshold || 80}</span>
                          </div>
                        </div>
                        <div>
                          <label className="block text-[10px] text-slate-500 dark:text-slate-400 font-medium mb-1 uppercase tracking-wider">
                            Isolation Action
                          </label>
                          <select
                            value={m.action || "block"}
                            onChange={(e) => handleActionChange(m.model_name, e.target.value)}
                            className="w-full px-2 py-1.5 rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 text-xs text-slate-800 dark:text-slate-200"
                          >
                            <option value="block">Block (503)</option>
                            <option value="reroute">Reroute to Fallback</option>
                            <option value="alert">Alert Only</option>
                          </select>
                        </div>
                        <div>
                          <label className="block text-[10px] text-slate-500 dark:text-slate-400 font-medium mb-1 uppercase tracking-wider">
                            Cooldown (seconds)
                          </label>
                          <span className="text-xs font-mono text-slate-700 dark:text-slate-300">{m.cooldown_seconds || 300}s</span>
                        </div>
                      </div>
                      {m.isolation_reason && (
                        <div className="mt-2 text-[11px] text-slate-500 dark:text-slate-400">
                          <span className="font-medium">Reason:</span> {m.isolation_reason}
                        </div>
                      )}
                    </div>
                  )}
                </div>
              );
            })}
        </div>
      )}

      {/* Audit Log Section */}
      {showAudit && (
        <div className="mt-4 border-t border-slate-200 dark:border-slate-700 pt-4">
          <h4 className="text-sm font-semibold text-slate-800 dark:text-slate-200 mb-3 flex items-center gap-2">
            <Clock className="w-4 h-4" /> Kill-Switch Audit Log
          </h4>
          {auditLogs.length === 0 ? (
            <p className="text-xs text-slate-500 dark:text-slate-400 text-center py-4">No audit events recorded yet.</p>
          ) : (
            <div className="overflow-x-auto max-h-64 overflow-y-auto">
              <table className="w-full text-xs">
                <thead>
                  <tr className="bg-slate-50/80 dark:bg-slate-800/50 border-b border-slate-200 dark:border-slate-700">
                    <th className="px-2 py-1.5 text-left font-semibold text-slate-600 dark:text-slate-300">Time</th>
                    <th className="px-2 py-1.5 text-left font-semibold text-slate-600 dark:text-slate-300">Event</th>
                    <th className="px-2 py-1.5 text-left font-semibold text-slate-600 dark:text-slate-300">Model</th>
                    <th className="px-2 py-1.5 text-left font-semibold text-slate-600 dark:text-slate-300">Risk</th>
                    <th className="px-2 py-1.5 text-left font-semibold text-slate-600 dark:text-slate-300">Action</th>
                    <th className="px-2 py-1.5 text-left font-semibold text-slate-600 dark:text-slate-300">Reason</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100 dark:divide-slate-700">
                  {auditLogs.slice(0, 20).map((log, i) => (
                    <tr key={log.id || i} className="hover:bg-slate-50/80 dark:hover:bg-slate-800/30">
                      <td className="px-2 py-1.5 text-slate-500 whitespace-nowrap">
                        {log.timestamp ? new Date(log.timestamp).toLocaleTimeString() : "—"}
                      </td>
                      <td className="px-2 py-1.5">
                        <span className={`px-1.5 py-0.5 rounded text-[10px] font-medium ${
                          log.event === "isolated" ? "bg-red-100 dark:bg-red-900/20 text-red-700 dark:text-red-400" :
                          log.event === "recovered" ? "bg-emerald-100 dark:bg-emerald-900/20 text-emerald-700 dark:text-emerald-400" :
                          "bg-slate-100 dark:bg-slate-700 text-slate-600 dark:text-slate-400"
                        }`}>
                          {log.event}
                        </span>
                      </td>
                      <td className="px-2 py-1.5 font-mono text-slate-700 dark:text-slate-300">{log.model_name}</td>
                      <td className="px-2 py-1.5 font-mono">{log.risk_score != null ? log.risk_score.toFixed(1) : "—"}</td>
                      <td className="px-2 py-1.5 text-slate-600 dark:text-slate-400">{log.action || "—"}</td>
                      <td className="px-2 py-1.5 text-slate-500 max-w-[200px] truncate">{log.reason || "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
