import { useState, useEffect, useCallback, useRef } from "react";
import {
  Activity, Shield, ShieldOff, ShieldAlert, RotateCcw,
  Loader2, AlertTriangle, CheckCircle, Zap, Settings,
  ChevronDown, ChevronUp, Clock, TrendingUp,
} from "lucide-react";
import { useAuth } from "../context/AuthContext";
import { startVisibleInterval } from "../utils/visiblePoll.js";
import { InfoTooltip } from "./InfoTooltip";
import { filterUserManagedModels } from "../constants/zeroshieldBrand";

/** Encode model id for /api/models/status/<path>/ — keep `/` so Django <path:> matches provider/model ids. */
function encodeModelPathSegment(modelName) {
  return String(modelName || "")
    .split("/")
    .map((part) => encodeURIComponent(part))
    .join("/");
}

function modelStatusUrl(modelName) {
  return `/api/models/status/${encodeModelPathSegment(modelName)}/`;
}

function modelRecoverUrl(modelName) {
  return `/api/models/recover/${encodeModelPathSegment(modelName)}/`;
}

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
        className="absolute -top-4 text-[10px] font-bold text-red-600 dark:text-red-400"
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
  // Soft guide when operator picks Reroute without a fallback yet (no dead-end error).
  const [fallbackHintModel, setFallbackHintModel] = useState(null);
  const fallbackSelectRefs = useRef({});

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

      setModels(filterUserManagedModels(dbModels));
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
      setModels(filterUserManagedModels(data.models || []));
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
    return startVisibleInterval(() => {
      fetchModelStates();
      fetchAuditLogs();
    }, 5000);
  }, [polling, fetchModelStates, fetchAuditLogs]);

  const handleIsolate = async (modelName) => {
    const row = models.find((x) => x.model_name === modelName);
    const action = row?.action || "block";
    const fallback = (row?.fallback_model || "").trim();
    if (action === "reroute" && !fallback) {
      setLoadError(`Select a Fallback Model for "${modelName}" before isolating with silent reroute.`);
      setFallbackHintModel(modelName);
      setExpandedModel(modelName);
      requestAnimationFrame(() => {
        const el = fallbackSelectRefs.current[modelName];
        if (el && typeof el.focus === "function") el.focus();
      });
      return;
    }
    const msg =
      action === "reroute"
        ? `Isolate "${modelName}" with silent reroute to "${fallback}"? Clients should keep working on the fallback.`
        : `Isolate "${modelName}"? Requests to this model will be blocked (503) until you recover it.`;
    if (!window.confirm(msg)) return;
    setActionLoading(modelName);
    setLoadError(null);
    try {
      const body = {
        model_name: modelName,
        action,
        reason: "Manual isolation from dashboard",
      };
      if (action === "reroute") body.fallback_model = fallback;
      const res = await fetchWithAuth("/api/models/isolate/", {
        method: "POST",
        body: JSON.stringify(body),
      });
      if (!res.ok) {
        setLoadError(`Could not isolate "${modelName}" — the model was not changed.`);
        return;
      }
      await fetchModelStates();
      await fetchAuditLogs();
    } catch {
      setLoadError(`Network error isolating "${modelName}".`);
    } finally {
      setActionLoading(null);
    }
  };

  const handleRecover = async (modelName) => {
    if (!window.confirm(`Recover "${modelName}"? Live traffic to this model will resume.`)) return;
    setActionLoading(modelName);
    setLoadError(null);
    try {
      const res = await fetchWithAuth(modelRecoverUrl(modelName), { method: "POST" });
      if (!res.ok) {
        setLoadError(`Could not recover "${modelName}" — the model was not changed.`);
        return;
      }
      await fetchModelStates();
      await fetchAuditLogs();
    } catch {
      setLoadError(`Network error recovering "${modelName}".`);
    } finally {
      setActionLoading(null);
    }
  };

  const _apiErrorMessage = async (res, fallback) => {
    try {
      const data = await res.json();
      if (!data || typeof data !== "object") return fallback;
      for (const key of ["fallback_model", "action", "threshold", "detail", "error"]) {
        const v = data[key];
        if (Array.isArray(v) && v[0]) return String(v[0]);
        if (typeof v === "string" && v.trim()) return v;
      }
      const first = Object.values(data).flat?.() ?? Object.values(data);
      if (Array.isArray(first) && first[0]) return String(first[0]);
    } catch {
      /* ignore parse errors */
    }
    return fallback;
  };

  const handleThresholdChange = async (modelName, newThreshold) => {
    try {
      const row = models.find((x) => x.model_name === modelName);
      if (row && row.id == null) {
        await handleSyncStates();
      }
      const res = await fetchWithAuth(modelStatusUrl(modelName), {
        method: "PATCH",
        body: JSON.stringify({ threshold: newThreshold }),
      });
      if (!res.ok) {
        setLoadError(await _apiErrorMessage(res, "Could not update threshold. Try Sync states first."));
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
      const fallback = (row?.fallback_model || "").trim();
      // Kill-Switch parity: Reroute without a fallback → guide to Fallback select (do not dead-end).
      if (newAction === "reroute" && !fallback) {
        setLoadError(null);
        setFallbackHintModel(modelName);
        setExpandedModel(modelName);
        requestAnimationFrame(() => {
          const el = fallbackSelectRefs.current[modelName];
          if (el && typeof el.focus === "function") el.focus();
        });
        return;
      }
      const body = { action: newAction };
      if (newAction === "reroute") body.fallback_model = fallback;
      if (newAction === "block" || newAction === "alert") body.fallback_model = "";
      const res = await fetchWithAuth(modelStatusUrl(modelName), {
        method: "PATCH",
        body: JSON.stringify(body),
      });
      if (!res.ok) {
        setLoadError(await _apiErrorMessage(res, "Could not update action. Try Sync states first."));
        return;
      }
      setLoadError(null);
      setFallbackHintModel(null);
      await fetchModelStates();
    } catch {
      setLoadError("Network error updating action.");
    }
  };

  const handleFallbackChange = async (modelName, fallbackModel) => {
    try {
      const row = models.find((x) => x.model_name === modelName);
      if (row && row.id == null) {
        await handleSyncStates();
      }
      const body = { fallback_model: fallbackModel || "" };
      if (fallbackModel) {
        // Picking a fallback implies silent continuity — persist as reroute.
        body.action = "reroute";
      } else if ((row?.action || "block") === "reroute") {
        // Cleared fallback while on reroute → fall back to hard-stop default.
        body.action = "block";
      }
      const res = await fetchWithAuth(modelStatusUrl(modelName), {
        method: "PATCH",
        body: JSON.stringify(body),
      });
      if (!res.ok) {
        setLoadError(await _apiErrorMessage(res, "Could not update fallback model. Try Sync states first."));
        return;
      }
      setLoadError(null);
      setFallbackHintModel(null);
      await fetchModelStates();
    } catch {
      setLoadError("Network error updating fallback model.");
    }
  };

  const isolatedCount = models.filter((m) => m.status === "isolated").length;
  const degradedCount = models.filter((m) => m.status === "degraded").length;
  // Strip platform/guard (ZeroShield) rows so the reserved guard model's raw id
  // never surfaces in the audit trail — mirrors the allowlist/governance panels.
  const visibleAuditLogs = filterUserManagedModels(auditLogs);
  const totalModels = models.length;

  return (
    <div className="ai-mesh-card ai-mesh-grid-bg rounded-3xl p-6">
      {/* Header */}
      <div className="flex flex-wrap items-center justify-between gap-3 mb-4">
        <div className="min-w-0">
          <h3 className="text-base font-semibold text-slate-900 dark:text-slate-100 flex items-center">
            Model State & Risk Monitor
            <InfoTooltip title="Real-Time Model Health">
              {"Live model risk monitoring with auto-isolation. Models exceeding their risk threshold are automatically isolated. Recovery happens after cooldown period or manual intervention."}
            </InfoTooltip>
          </h3>
          <p className="text-xs text-slate-500 dark:text-slate-400 mt-0.5">
            Org-scoped model health. Status cards refresh every 5s; risk scores update on traffic and a ~60s auto-scan.
            {orgSlug ? (
              <span className="ml-1 font-mono text-teal-700 dark:text-teal-300">({orgSlug})</span>
            ) : null}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
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
        <div className="ml-auto text-[10px] text-slate-500 dark:text-slate-400 text-right">
          <div>{polling ? "Status refresh: 5s" : "Paused"}</div>
          <div className="opacity-80" title="Risk scores update when traffic is scored and via a periodic scan — not recomputed every 5 seconds.">
            Risk scores: on traffic + ~60s scan
          </div>
        </div>
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
          {[...models]
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
                        {m.kill_switch_active && (
                          <span
                            className="px-1.5 py-0.5 rounded-md text-[10px] font-bold uppercase text-red-600 dark:text-red-400 border border-red-500/30 bg-red-500/10"
                            title={m.kill_switch_reason || "A kill-switch is active for this model (managed in Kill-Switch Management)."}
                          >
                            kill-switch
                          </span>
                        )}
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
                          <div className="text-[10px] text-slate-500 dark:text-slate-400 uppercase tracking-wider">Risk Score</div>
                        </div>
                      </div>
                    </div>
                    <div className="flex items-center gap-1 flex-shrink-0">
                      {actionLoading === m.model_name ? (
                        <Loader2 className="w-4 h-4 text-teal-500 animate-spin" />
                      ) : m.source === "kill_switch" ? (
                        <span
                          className="flex items-center gap-1 px-2.5 py-1.5 rounded-lg text-xs font-medium bg-red-500/10 text-red-700 dark:text-red-400 border border-red-500/20"
                          title="This model is held by a kill-switch. Manage it in Kill-Switch Management."
                        >
                          <ShieldOff className="w-3 h-3" /> Kill-switch
                        </span>
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
                        aria-label={`${isExpanded ? "Collapse" : "Expand"} model settings for ${m.model_name}`}
                        aria-expanded={isExpanded}
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
                              aria-label={`Risk threshold for ${m.model_name}`}
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
                            aria-label={`Isolation action for ${m.model_name}`}
                          >
                            <option value="block">Block (503)</option>
                            <option value="reroute">Reroute to Fallback</option>
                            <option value="alert">Alert Only</option>
                          </select>
                          <p className="mt-1 text-[10px] text-slate-500 dark:text-slate-400">
                            Block = hard stop. Reroute = no client interrupt if fallback is healthy.
                            Select a Fallback Model to enable silent reroute.
                          </p>
                        </div>
                        <div>
                          <label className="block text-[10px] text-slate-500 dark:text-slate-400 font-medium mb-1 uppercase tracking-wider">
                            Fallback Model
                          </label>
                          <select
                            ref={(el) => {
                              if (el) fallbackSelectRefs.current[m.model_name] = el;
                              else delete fallbackSelectRefs.current[m.model_name];
                            }}
                            value={m.fallback_model || ""}
                            onChange={(e) => handleFallbackChange(m.model_name, e.target.value)}
                            className={`w-full px-2 py-1.5 rounded-lg border bg-white dark:bg-slate-800 text-xs text-slate-800 dark:text-slate-200 ${
                              fallbackHintModel === m.model_name
                                ? "border-amber-400 ring-2 ring-amber-400/40 dark:border-amber-500"
                                : "border-slate-200 dark:border-slate-700"
                            }`}
                            aria-label={`Fallback model for ${m.model_name}`}
                            data-testid={`fallback-model-${m.model_name}`}
                          >
                            <option value="">— none —</option>
                            {models
                              .filter((x) => x.model_name !== m.model_name)
                              .map((x) => (
                                <option key={x.model_name} value={x.model_name}>
                                  {x.model_name}
                                </option>
                              ))}
                          </select>
                          {fallbackHintModel === m.model_name && !(m.fallback_model || "").trim() && (
                            <p
                              className="mt-1 text-[10px] text-amber-700 dark:text-amber-300"
                              role="status"
                              data-testid="fallback-hint"
                            >
                              Select a fallback to enable silent reroute.
                            </p>
                          )}
                        </div>
                        <div>
                          <label className="block text-[10px] text-slate-500 dark:text-slate-400 font-medium mb-1 uppercase tracking-wider">
                            Cooldown (seconds)
                          </label>
                          <span className="text-xs font-mono text-slate-700 dark:text-slate-300">{m.cooldown_seconds || 300}s</span>
                          {m.last_updated && (
                            <div className="mt-2 text-[10px] text-slate-500 dark:text-slate-400">
                              Updated {new Date(m.last_updated).toLocaleString()}
                            </div>
                          )}
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
          {visibleAuditLogs.length === 0 ? (
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
                  {visibleAuditLogs.slice(0, 20).map((log, i) => (
                    <tr key={log.id || i} className="hover:bg-slate-50/80 dark:hover:bg-slate-800/30">
                      <td className="px-2 py-1.5 text-slate-500 dark:text-slate-400 whitespace-nowrap">
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
                      <td className="px-2 py-1.5 font-mono text-slate-700 dark:text-slate-300">{log.risk_score != null ? log.risk_score.toFixed(1) : "—"}</td>
                      <td className="px-2 py-1.5 text-slate-600 dark:text-slate-400">{log.action || "—"}</td>
                      <td className="px-2 py-1.5 text-slate-500 dark:text-slate-400 max-w-[200px] truncate">{log.reason || "—"}</td>
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
