import { useCallback, useEffect, useMemo, useState } from "react";
import { AlertTriangle, Loader2, Power, PowerOff, ShieldAlert, ExternalLink } from "lucide-react";
import {
  buildCredentialKillSwitchPayload,
  buildUebaKillSwitchReason,
  createKillSwitchApi,
  filterKillSwitchesForPrefix,
} from "../../api/killSwitch";

const BAND_STYLES = {
  low: {
    badge: "bg-emerald-100 text-emerald-800 dark:bg-emerald-900/40 dark:text-emerald-300",
    gauge: "bg-emerald-500",
    ring: "stroke-emerald-500",
  },
  medium: {
    badge: "bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-300",
    gauge: "bg-amber-500",
    ring: "stroke-amber-500",
  },
  high: {
    badge: "bg-red-100 text-red-800 dark:bg-red-900/40 dark:text-red-300",
    gauge: "bg-red-500",
    ring: "stroke-red-500",
  },
};

const ANOMALY_LABELS = {
  high_block_rate: "High block rate",
  velocity_spike: "Velocity spike",
  model_spread: "Model spread",
};

function RiskGauge({ score, band }) {
  const pct = Math.min(Math.max(Number(score) || 0, 0), 1) * 100;
  const styles = BAND_STYLES[band] || BAND_STYLES.low;
  const radius = 36;
  const circumference = 2 * Math.PI * radius;
  const offset = circumference - (pct / 100) * circumference;

  return (
    <div className="relative flex h-24 w-24 items-center justify-center">
      <svg className="h-24 w-24 -rotate-90" viewBox="0 0 96 96">
        <circle cx="48" cy="48" r={radius} className="stroke-slate-200 dark:stroke-slate-700" strokeWidth="8" fill="none" />
        <circle
          cx="48"
          cy="48"
          r={radius}
          className={styles.ring}
          strokeWidth="8"
          fill="none"
          strokeLinecap="round"
          strokeDasharray={circumference}
          strokeDashoffset={offset}
        />
      </svg>
      <div className="absolute text-center">
        <p className="text-lg font-bold text-slate-900 dark:text-white">{pct.toFixed(0)}</p>
        <p className="text-[10px] uppercase text-slate-500">risk</p>
      </div>
    </div>
  );
}

function MetricBar({ label, value, colorClass }) {
  const pct = Math.min(Math.max(Number(value) || 0, 0), 100);
  return (
    <div>
      <div className="mb-1 flex justify-between text-xs">
        <span className="text-slate-600 dark:text-slate-400">{label}</span>
        <span className="font-medium text-slate-800 dark:text-slate-200">{pct.toFixed(1)}%</span>
      </div>
      <div className="h-2 overflow-hidden rounded-full bg-slate-200 dark:bg-slate-700">
        <div className={`h-full rounded-full ${colorClass}`} style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}

export function ApiKeyRiskProfile({ behavior, fetchWithAuth, onActionComplete, showActions = true }) {
  const api = useMemo(() => createKillSwitchApi(fetchWithAuth), [fetchWithAuth]);
  const [killSwitches, setKillSwitches] = useState([]);
  const [ksLoading, setKsLoading] = useState(true);
  const [actionLoading, setActionLoading] = useState(null);
  const [actionError, setActionError] = useState(null);
  const [actionSuccess, setActionSuccess] = useState(null);
  const [killModalOpen, setKillModalOpen] = useState(false);
  const [selectedModel, setSelectedModel] = useState("");

  const scopedKillSwitches = useMemo(
    () => filterKillSwitchesForPrefix(killSwitches, behavior?.prefix),
    [killSwitches, behavior?.prefix],
  );

  const modelOptions = useMemo(() => {
    return (behavior?.top_models || []).map(([name]) => name).filter((name) => name && name !== "unknown");
  }, [behavior]);

  const allowRate = useMemo(() => {
    if (!behavior?.request_count) return 0;
    const blocked = behavior.blocked_count || 0;
    const redacted = behavior.redacted_count || 0;
    const allowed = Math.max(0, behavior.request_count - blocked - redacted);
    return (allowed / behavior.request_count) * 100;
  }, [behavior]);

  const loadKillSwitches = useCallback(async () => {
    setKsLoading(true);
    try {
      setKillSwitches(await api.listKillSwitches());
    } catch {
      setKillSwitches([]);
    } finally {
      setKsLoading(false);
    }
  }, [api]);

  useEffect(() => {
    loadKillSwitches();
  }, [loadKillSwitches]);

  useEffect(() => {
    if (modelOptions.length && !selectedModel) {
      setSelectedModel(modelOptions[0]);
    }
  }, [modelOptions, selectedModel]);

  if (!behavior) {
    return (
      <p className="py-8 text-center text-sm text-slate-400">
        Select a key from Top Risky Keys to inspect behavior and response controls.
      </p>
    );
  }

  const band = behavior.risk_band || "low";
  const styles = BAND_STYLES[band] || BAND_STYLES.low;

  const handleApplyKillSwitch = async () => {
    if (!selectedModel) {
      setActionError("Select a model to scope the credential kill switch.");
      return;
    }
    const confirmed = window.confirm(
      `Apply credential kill switch for key ${behavior.prefix} on model "${selectedModel}"? `
      + "Future requests using this key for that model will be blocked at the gateway.",
    );
    if (!confirmed) return;

    setActionLoading("kill-switch");
    setActionError(null);
    setActionSuccess(null);
    try {
      const payload = buildCredentialKillSwitchPayload({
        modelName: selectedModel,
        apiKeyPrefix: behavior.prefix,
        reason: buildUebaKillSwitchReason(behavior),
      });
      await api.createAndActivateKillSwitch(payload);
      setActionSuccess(`Kill switch activated for ${behavior.prefix} → ${selectedModel}`);
      setKillModalOpen(false);
      await loadKillSwitches();
      onActionComplete?.();
    } catch (err) {
      setActionError(err.message || "Failed to apply kill switch.");
    } finally {
      setActionLoading(null);
    }
  };

  const handleDisableKey = async () => {
    const confirmed = window.confirm(
      `Disable API key ${behavior.prefix}? All requests with this credential will fail authentication.`,
    );
    if (!confirmed) return;

    setActionLoading("disable-key");
    setActionError(null);
    setActionSuccess(null);
    try {
      await api.setGatewayKeyActive(behavior.key_id, false);
      setActionSuccess(`API key ${behavior.prefix} disabled.`);
      onActionComplete?.();
    } catch (err) {
      setActionError(err.message || "Failed to disable API key.");
    } finally {
      setActionLoading(null);
    }
  };

  const handleReenableKey = async () => {
    setActionLoading("enable-key");
    setActionError(null);
    try {
      await api.setGatewayKeyActive(behavior.key_id, true);
      setActionSuccess(`API key ${behavior.prefix} re-enabled.`);
      onActionComplete?.();
    } catch (err) {
      setActionError(err.message || "Failed to re-enable API key.");
    } finally {
      setActionLoading(null);
    }
  };

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-start gap-4 rounded-xl border border-slate-200 bg-slate-50/80 p-4 dark:border-slate-600 dark:bg-slate-800/40">
        <RiskGauge score={behavior.risk_score} band={band} />
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className={`rounded-full px-2.5 py-0.5 text-xs font-bold uppercase ${styles.badge}`}>
              {band} risk
            </span>
            {!behavior.is_active && (
              <span className="rounded-full bg-slate-200 px-2 py-0.5 text-xs font-semibold text-slate-600 dark:bg-slate-700 dark:text-slate-300">
                Key disabled
              </span>
            )}
          </div>
          <p className="mt-1 font-mono text-sm text-slate-800 dark:text-slate-100">{behavior.prefix}</p>
          <p className="text-xs text-slate-500 dark:text-slate-400">
            {behavior.name || "—"} · {behavior.project_id || "no project"}
            {behavior.owner_email ? ` · ${behavior.owner_email}` : ""}
          </p>
          <p className="mt-2 text-xs text-slate-600 dark:text-slate-300">
            Score {behavior.risk_score} · Velocity {behavior.velocity_spike}x · {behavior.request_count} requests
          </p>
        </div>
      </div>

      <div className="space-y-2">
        <MetricBar label="Blocked" value={behavior.block_rate_pct} colorClass="bg-red-500" />
        <MetricBar label="Redacted" value={behavior.redact_rate_pct} colorClass="bg-amber-500" />
        <MetricBar label="Allowed" value={allowRate} colorClass="bg-emerald-500" />
      </div>

      {(behavior.anomaly_flags || []).length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {behavior.anomaly_flags.map((flag) => (
            <span
              key={flag}
              className="inline-flex items-center gap-1 rounded-md border border-amber-300/60 bg-amber-50 px-2 py-0.5 text-[11px] font-medium text-amber-800 dark:border-amber-700 dark:bg-amber-950/40 dark:text-amber-200"
            >
              <AlertTriangle className="h-3 w-3" />
              {ANOMALY_LABELS[flag] || flag}
            </span>
          ))}
        </div>
      )}

      <div className="grid grid-cols-2 gap-3 text-xs">
        <div className="rounded-lg border border-slate-200 p-2 dark:border-slate-600">
          <p className="mb-1 font-semibold uppercase text-slate-500">Top threats</p>
          <p className="text-slate-700 dark:text-slate-300">
            {(behavior.top_threat_types || []).slice(0, 3).map(([t, c]) => `${t} (${c})`).join(", ") || "—"}
          </p>
        </div>
        <div className="rounded-lg border border-slate-200 p-2 dark:border-slate-600">
          <p className="mb-1 font-semibold uppercase text-slate-500">Models used</p>
          <p className="text-slate-700 dark:text-slate-300">
            {(behavior.top_models || []).slice(0, 3).map(([m, c]) => `${m} (${c})`).join(", ") || "—"}
          </p>
        </div>
      </div>

      <div className="rounded-lg border border-slate-200 p-3 dark:border-slate-600">
        <p className="mb-2 flex items-center gap-1 text-xs font-semibold uppercase text-slate-500">
          <ShieldAlert className="h-3.5 w-3.5" />
          Active credential kill switches
        </p>
        {ksLoading ? (
          <Loader2 className="h-4 w-4 animate-spin text-slate-400" />
        ) : scopedKillSwitches.length === 0 ? (
          <p className="text-xs text-slate-400">No kill switches scoped to this key prefix.</p>
        ) : (
          <ul className="space-y-1.5">
            {scopedKillSwitches.map((ks) => (
              <li key={ks.id} className="flex items-center justify-between text-xs">
                <span className="font-mono text-slate-700 dark:text-slate-200">
                  {ks.model_name}
                  {ks.is_active ? (
                    <span className="ml-2 text-red-600 dark:text-red-400">active</span>
                  ) : (
                    <span className="ml-2 text-slate-400">inactive</span>
                  )}
                </span>
                {ks.is_active && (
                  <button
                    type="button"
                    disabled={actionLoading === `deactivate-${ks.id}`}
                    onClick={async () => {
                      setActionLoading(`deactivate-${ks.id}`);
                      try {
                        await api.deactivateKillSwitch(ks.id);
                        await loadKillSwitches();
                      } finally {
                        setActionLoading(null);
                      }
                    }}
                    className="text-teal-600 hover:underline dark:text-teal-400"
                  >
                    Deactivate
                  </button>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>

      {actionError && (
        <p className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700 dark:border-red-900 dark:bg-red-950/40 dark:text-red-300">
          {actionError}
        </p>
      )}
      {actionSuccess && (
        <p className="rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2 text-xs text-emerald-800 dark:border-emerald-900 dark:bg-emerald-950/40 dark:text-emerald-200">
          {actionSuccess}
        </p>
      )}

      {showActions && (
        <div className="flex flex-wrap gap-2 border-t border-slate-200 pt-3 dark:border-slate-600">
          {behavior.is_active !== false && (
            <button
              type="button"
              disabled={!!actionLoading}
              onClick={() => setKillModalOpen(true)}
              className="inline-flex items-center gap-1.5 rounded-lg bg-red-600 px-3 py-2 text-xs font-semibold text-white hover:bg-red-700 disabled:opacity-60"
            >
              {actionLoading === "kill-switch" ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Power className="h-3.5 w-3.5" />}
              Apply credential kill switch
            </button>
          )}
          {behavior.is_active !== false ? (
            <button
              type="button"
              disabled={!!actionLoading}
              onClick={handleDisableKey}
              className="inline-flex items-center gap-1.5 rounded-lg border border-slate-300 px-3 py-2 text-xs font-semibold text-slate-700 hover:bg-slate-100 disabled:opacity-60 dark:border-slate-600 dark:text-slate-200 dark:hover:bg-slate-800"
            >
              {actionLoading === "disable-key" ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <PowerOff className="h-3.5 w-3.5" />}
              Disable API key
            </button>
          ) : (
            <button
              type="button"
              disabled={!!actionLoading}
              onClick={handleReenableKey}
              className="inline-flex items-center gap-1.5 rounded-lg border border-teal-300 px-3 py-2 text-xs font-semibold text-teal-700 hover:bg-teal-50 disabled:opacity-60 dark:border-teal-700 dark:text-teal-300"
            >
              Re-enable API key
            </button>
          )}
          <a
            href="/?tab=firewall-1-6"
            className="inline-flex items-center gap-1 rounded-lg px-2 py-2 text-xs text-slate-500 hover:text-teal-600 dark:hover:text-teal-400"
          >
            Open Kill Switch panel
            <ExternalLink className="h-3 w-3" />
          </a>
        </div>
      )}

      {showActions && killModalOpen && (
        <div className="fixed inset-0 z-[100] flex items-center justify-center bg-black/40 p-4">
          <div className="w-full max-w-md rounded-2xl border border-slate-200 bg-white p-5 shadow-xl dark:border-slate-700 dark:bg-slate-900">
            <h4 className="text-sm font-semibold text-slate-900 dark:text-white">Credential kill switch</h4>
            <p className="mt-1 text-xs text-slate-500">
              Blocks requests for key <span className="font-mono">{behavior.prefix}</span> on the selected model only.
            </p>
            <label className="mt-4 block text-xs font-medium text-slate-600 dark:text-slate-300">
              Target model
              {modelOptions.length > 0 ? (
                <select
                  value={selectedModel}
                  onChange={(e) => setSelectedModel(e.target.value)}
                  className="mt-1 w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm dark:border-slate-600 dark:bg-slate-800"
                >
                  {modelOptions.map((m) => (
                    <option key={m} value={m}>{m}</option>
                  ))}
                </select>
              ) : (
                <input
                  type="text"
                  value={selectedModel}
                  onChange={(e) => setSelectedModel(e.target.value)}
                  placeholder="e.g. gpt-4o"
                  className="mt-1 w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm dark:border-slate-600 dark:bg-slate-800"
                />
              )}
            </label>
            <div className="mt-4 flex justify-end gap-2">
              <button
                type="button"
                onClick={() => setKillModalOpen(false)}
                className="rounded-lg px-3 py-2 text-xs text-slate-600 hover:bg-slate-100 dark:text-slate-300 dark:hover:bg-slate-800"
              >
                Cancel
              </button>
              <button
                type="button"
                disabled={actionLoading === "kill-switch"}
                onClick={handleApplyKillSwitch}
                className="rounded-lg bg-red-600 px-3 py-2 text-xs font-semibold text-white hover:bg-red-700 disabled:opacity-60"
              >
                Activate kill switch
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
