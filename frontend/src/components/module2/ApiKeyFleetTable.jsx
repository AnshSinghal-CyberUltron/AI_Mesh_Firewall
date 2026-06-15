import { Fragment, useCallback, useEffect, useMemo, useState } from "react";
import {
  AlertTriangle, ChevronDown, ChevronRight, Loader2, Power, PowerOff, ShieldAlert,
} from "lucide-react";
import { createModule2Api } from "../../api/module2";
import {
  buildCredentialKillSwitchPayload,
  buildUebaKillSwitchReason,
  createKillSwitchApi,
} from "../../api/killSwitch";
import { ApiKeyRiskProfile } from "./ApiKeyRiskProfile";
import { InfoTooltip } from "./InfoTooltip";

const FILTERS = [
  { id: "all", label: "All keys" },
  { id: "active", label: "Active" },
  { id: "high", label: "High risk" },
  { id: "activity", label: "With activity" },
  { id: "kill-switch", label: "Kill switch" },
];

function riskBandClass(band) {
  if (band === "high") return "bg-red-100 text-red-700 dark:bg-red-900/40 dark:text-red-300";
  if (band === "medium") return "bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-300";
  return "bg-emerald-100 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-300";
}

function KillSwitchModal({ row, onClose, onConfirm, loading, modelOptions }) {
  const [model, setModel] = useState(modelOptions[0] || "");

  useEffect(() => {
    if (modelOptions.length && !model) setModel(modelOptions[0]);
  }, [modelOptions, model]);

  if (!row) return null;

  return (
    <div className="fixed inset-0 z-[100] flex items-center justify-center bg-black/40 p-4">
      <div className="w-full max-w-md rounded-2xl border border-slate-200 bg-white p-5 shadow-xl dark:border-slate-700 dark:bg-slate-900">
        <h4 className="text-sm font-semibold text-slate-900 dark:text-white">Credential kill switch</h4>
        <p className="mt-1 text-xs text-slate-500">
          Blocks requests for key <span className="font-mono">{row.prefix}</span> on the selected model only.
        </p>
        <label className="mt-4 block text-xs font-medium text-slate-600 dark:text-slate-300">
          Target model
          {modelOptions.length > 0 ? (
            <select
              value={model}
              onChange={(e) => setModel(e.target.value)}
              className="mt-1 w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm dark:border-slate-600 dark:bg-slate-800"
            >
              {modelOptions.map((m) => (
                <option key={m} value={m}>{m}</option>
              ))}
            </select>
          ) : (
            <input
              type="text"
              value={model}
              onChange={(e) => setModel(e.target.value)}
              placeholder="e.g. gpt-4o"
              className="mt-1 w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm dark:border-slate-600 dark:bg-slate-800"
            />
          )}
        </label>
        <div className="mt-4 flex justify-end gap-2">
          <button type="button" onClick={onClose} className="rounded-lg px-3 py-2 text-xs text-slate-600 hover:bg-slate-100 dark:hover:bg-slate-800">
            Cancel
          </button>
          <button
            type="button"
            disabled={loading || !model.trim()}
            onClick={() => onConfirm(model.trim())}
            className="rounded-lg bg-red-600 px-3 py-2 text-xs font-semibold text-white hover:bg-red-700 disabled:opacity-60"
          >
            {loading ? "Activating…" : "Activate kill switch"}
          </button>
        </div>
      </div>
    </div>
  );
}

function FleetRowActions({ row, fetchWithAuth, onActionComplete, onKillSwitchClick }) {
  const api = useMemo(() => createKillSwitchApi(fetchWithAuth), [fetchWithAuth]);
  const [loading, setLoading] = useState(null);

  const handleToggleActive = async () => {
    const disabling = row.is_active !== false;
    const confirmed = window.confirm(
      disabling
        ? `Disable API key ${row.prefix}? All requests with this credential will fail authentication.`
        : `Re-enable API key ${row.prefix}?`,
    );
    if (!confirmed) return;
    setLoading(disabling ? "disable" : "enable");
    try {
      await api.setGatewayKeyActive(row.key_id, !disabling);
      onActionComplete?.();
    } finally {
      setLoading(null);
    }
  };

  const handleDeactivateKs = async (ksId) => {
    setLoading(`ks-${ksId}`);
    try {
      await api.deactivateKillSwitch(ksId);
      onActionComplete?.();
    } finally {
      setLoading(null);
    }
  };

  return (
    <div className="flex flex-wrap items-center gap-1.5">
      {row.is_active !== false && (
        <button
          type="button"
          disabled={!!loading}
          onClick={() => onKillSwitchClick(row)}
          className="inline-flex items-center gap-1 rounded-md bg-red-600 px-2 py-1 text-[11px] font-semibold text-white hover:bg-red-700 disabled:opacity-60"
          title="Apply credential-scoped kill switch"
        >
          {loading === "kill" ? <Loader2 className="h-3 w-3 animate-spin" /> : <Power className="h-3 w-3" />}
          Kill switch
        </button>
      )}
      <button
        type="button"
        disabled={!!loading}
        onClick={handleToggleActive}
        className="inline-flex items-center gap-1 rounded-md border border-slate-300 px-2 py-1 text-[11px] font-semibold text-slate-700 hover:bg-slate-100 disabled:opacity-60 dark:border-slate-600 dark:text-slate-200 dark:hover:bg-slate-800"
      >
        {loading === "disable" || loading === "enable" ? (
          <Loader2 className="h-3 w-3 animate-spin" />
        ) : (
          <PowerOff className="h-3 w-3" />
        )}
        {row.is_active !== false ? "Disable" : "Enable"}
      </button>
      {(row.active_kill_switches || []).filter((ks) => ks.is_active).map((ks) => (
        <button
          key={ks.id}
          type="button"
          disabled={loading === `ks-${ks.id}`}
          onClick={() => handleDeactivateKs(ks.id)}
          className="rounded-md border border-red-200 px-2 py-1 text-[10px] font-medium text-red-700 hover:bg-red-50 dark:border-red-900 dark:text-red-300"
          title={`Deactivate kill switch on ${ks.model_name}`}
        >
          Off {ks.model_name}
        </button>
      ))}
    </div>
  );
}

export function ApiKeyFleetTable({
  rows = [],
  selectedKeyId,
  onSelectKey,
  fetchWithAuth,
  period,
  onActionComplete,
  loading = false,
}) {
  const module2Api = useMemo(() => createModule2Api(fetchWithAuth), [fetchWithAuth]);
  const killApi = useMemo(() => createKillSwitchApi(fetchWithAuth), [fetchWithAuth]);
  const [filter, setFilter] = useState("all");
  const [expandedKeyId, setExpandedKeyId] = useState(null);
  const [expandedBehavior, setExpandedBehavior] = useState(null);
  const [expandLoading, setExpandLoading] = useState(false);
  const [killModalRow, setKillModalRow] = useState(null);
  const [killModalLoading, setKillModalLoading] = useState(false);
  const [flash, setFlash] = useState(null);

  const filteredRows = useMemo(() => {
    return rows.filter((row) => {
      if (filter === "active") return row.is_active !== false;
      if (filter === "high") return row.risk_band === "high";
      if (filter === "activity") return (row.request_count ?? 0) > 0;
      if (filter === "kill-switch") return (row.active_kill_switch_count ?? 0) > 0;
      return true;
    });
  }, [rows, filter]);

  const loadExpandedBehavior = useCallback(async (keyId) => {
    setExpandLoading(true);
    try {
      setExpandedBehavior(await module2Api.getUebaBehavior(keyId, period));
    } catch {
      setExpandedBehavior(null);
    } finally {
      setExpandLoading(false);
    }
  }, [module2Api, period]);

  const toggleExpand = useCallback(async (row) => {
    const next = expandedKeyId === row.key_id ? null : row.key_id;
    setExpandedKeyId(next);
    onSelectKey?.(row.key_id);
    if (next) {
      await loadExpandedBehavior(row.key_id);
    } else {
      setExpandedBehavior(null);
    }
  }, [expandedKeyId, loadExpandedBehavior, onSelectKey]);

  const handleKillSwitchConfirm = async (modelName) => {
    if (!killModalRow) return;
    setKillModalLoading(true);
    try {
      const behavior = expandedBehavior?.key_id === killModalRow.key_id
        ? expandedBehavior
        : killModalRow;
      const payload = buildCredentialKillSwitchPayload({
        modelName,
        apiKeyPrefix: killModalRow.prefix,
        reason: buildUebaKillSwitchReason(behavior),
      });
      await killApi.createAndActivateKillSwitch(payload);
      setFlash(`Kill switch activated for ${killModalRow.prefix} → ${modelName}`);
      setKillModalRow(null);
      await onActionComplete?.();
      if (expandedKeyId === killModalRow.key_id) {
        await loadExpandedBehavior(killModalRow.key_id);
      }
    } catch (err) {
      setFlash(err.message || "Failed to apply kill switch.");
    } finally {
      setKillModalLoading(false);
    }
  };

  const killModalModels = useMemo(() => {
    if (!killModalRow) return [];
    const fromBehavior = killModalRow.key_id === expandedBehavior?.key_id
      ? (expandedBehavior?.top_models || []).map(([name]) => name)
      : (killModalRow.top_models || []).map(([name]) => name);
    return fromBehavior.filter((n) => n && n !== "unknown");
  }, [killModalRow, expandedBehavior]);

  return (
    <div className="rounded-xl border border-slate-200 bg-white shadow-sm dark:border-slate-700 dark:bg-slate-800/60">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-200 px-4 py-3 dark:border-slate-700">
        <div>
          <h3 className="text-sm font-semibold text-slate-800 dark:text-slate-100">
            API Key Fleet Inspector
          </h3>
          <p className="text-xs text-slate-500 dark:text-slate-400">
            Inspect behavior and apply kill-switch / disable on every key — updates live.
          </p>
        </div>
        <div className="flex flex-wrap gap-1.5">
          {FILTERS.map((f) => (
            <button
              key={f.id}
              type="button"
              onClick={() => setFilter(f.id)}
              className={`rounded-lg border px-2.5 py-1 text-xs font-medium transition-colors ${
                filter === f.id
                  ? "border-teal-500 bg-teal-600 text-white"
                  : "border-slate-200 bg-white text-slate-600 hover:border-teal-300 dark:border-slate-600 dark:bg-slate-800 dark:text-slate-300"
              }`}
            >
              {f.label}
            </button>
          ))}
        </div>
      </div>

      {flash && (
        <p className="mx-4 mt-3 rounded-lg border border-teal-200 bg-teal-50 px-3 py-2 text-xs text-teal-800 dark:border-teal-900 dark:bg-teal-950/40 dark:text-teal-200">
          {flash}
        </p>
      )}

      {loading && !rows.length ? (
        <div className="flex justify-center py-16">
          <Loader2 className="h-6 w-6 animate-spin text-teal-500" />
        </div>
      ) : !filteredRows.length ? (
        <p className="py-12 text-center text-sm text-slate-400">No keys match this filter.</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[960px] text-sm">
            <thead>
              <tr className="border-b border-slate-200 bg-slate-50/80 dark:border-slate-700 dark:bg-slate-900/40">
                {[
                  { label: "", help: "Expand row for full behavior drilldown" },
                  { label: "Key", help: "Truncated credential prefix" },
                  { label: "Name / Owner", help: "Human label and owning user" },
                  { label: "Status", help: "Active keys pass auth; disabled keys are rejected at ingress" },
                  { label: "Risk", help: "UEBA band from block rate, velocity, and anomaly signals" },
                  { label: "Block %", help: "Hard-block rate for this key in the selected period" },
                  { label: "Requests", help: "Enforcement events attributed to this key" },
                  { label: "Velocity", help: "Burst multiplier vs hourly baseline" },
                  { label: "Kill Switches", help: "Active credential-scoped model blocks" },
                  { label: "Actions", help: "Apply containment without leaving the fleet view" },
                ].map((col) => (
                  <th key={col.label || "expand"} className="px-3 py-2 text-left text-xs font-semibold uppercase tracking-wide text-slate-500">
                    <span className="inline-flex items-center gap-1">
                      {col.label}
                      {col.help && <InfoTooltip text={col.help} />}
                    </span>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {filteredRows.map((row) => {
                const isExpanded = expandedKeyId === row.key_id;
                const isSelected = selectedKeyId === row.key_id;
                return (
                  <Fragment key={row.key_id}>
                    <tr
                      className={`border-b border-slate-100 dark:border-slate-700/50 ${
                        isSelected ? "bg-teal-50/50 dark:bg-teal-950/20" : "hover:bg-slate-50 dark:hover:bg-slate-700/30"
                      }`}
                    >
                      <td className="px-3 py-2">
                        <button
                          type="button"
                          onClick={() => toggleExpand(row)}
                          className="rounded p-1 text-slate-500 hover:bg-slate-200 dark:hover:bg-slate-700"
                          aria-label={isExpanded ? "Collapse" : "Expand"}
                        >
                          {isExpanded ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
                        </button>
                      </td>
                      <td className="px-3 py-2 font-mono text-xs">{row.prefix}</td>
                      <td className="px-3 py-2">
                        <p className="font-medium text-slate-800 dark:text-slate-200">{row.name || "—"}</p>
                        <p className="text-[11px] text-slate-500">{row.owner_email || row.project_id || "—"}</p>
                      </td>
                      <td className="px-3 py-2">
                        <span className={`rounded px-2 py-0.5 text-xs font-medium ${
                          row.is_active !== false
                            ? "bg-emerald-100 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-300"
                            : "bg-slate-200 text-slate-600 dark:bg-slate-700 dark:text-slate-300"
                        }`}>
                          {row.is_active !== false ? "Active" : "Disabled"}
                        </span>
                      </td>
                      <td className="px-3 py-2">
                        <span className={`rounded px-2 py-0.5 text-xs font-semibold uppercase ${riskBandClass(row.risk_band)}`}>
                          {row.risk_band || "low"}
                        </span>
                        <span className="ml-1 text-xs text-slate-500">({row.risk_score ?? 0})</span>
                      </td>
                      <td className="px-3 py-2">
                        <span className={row.block_rate_pct >= 35 ? "font-semibold text-red-600" : ""}>
                          {row.block_rate_pct ?? 0}%
                        </span>
                      </td>
                      <td className="px-3 py-2">{row.request_count ?? 0}</td>
                      <td className="px-3 py-2">
                        {(row.velocity_spike ?? 1) >= 2.5 ? (
                          <span className="inline-flex items-center gap-0.5 font-semibold text-amber-600">
                            <AlertTriangle className="h-3 w-3" />
                            {row.velocity_spike}x
                          </span>
                        ) : (
                          `${row.velocity_spike ?? 1}x`
                        )}
                      </td>
                      <td className="px-3 py-2">
                        {(row.active_kill_switch_count ?? 0) > 0 ? (
                          <span className="inline-flex items-center gap-1 text-xs font-medium text-red-600">
                            <ShieldAlert className="h-3.5 w-3.5" />
                            {row.active_kill_switch_count}
                          </span>
                        ) : (
                          <span className="text-xs text-slate-400">—</span>
                        )}
                      </td>
                      <td className="px-3 py-2">
                        <FleetRowActions
                          row={row}
                          fetchWithAuth={fetchWithAuth}
                          onActionComplete={onActionComplete}
                          onKillSwitchClick={setKillModalRow}
                        />
                      </td>
                    </tr>
                    {isExpanded && (
                      <tr key={`${row.key_id}-detail`} className="border-b border-slate-100 bg-slate-50/60 dark:border-slate-700/50 dark:bg-slate-900/30">
                        <td colSpan={10} className="px-4 py-4">
                          {expandLoading ? (
                            <div className="flex justify-center py-8">
                              <Loader2 className="h-6 w-6 animate-spin text-teal-500" />
                            </div>
                          ) : (
                            <ApiKeyRiskProfile
                              behavior={expandedBehavior || row}
                              fetchWithAuth={fetchWithAuth}
                              onActionComplete={onActionComplete}
                              showActions={false}
                            />
                          )}
                        </td>
                      </tr>
                    )}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      <KillSwitchModal
        row={killModalRow}
        modelOptions={killModalModels}
        loading={killModalLoading}
        onClose={() => setKillModalRow(null)}
        onConfirm={handleKillSwitchConfirm}
      />
    </div>
  );
}
