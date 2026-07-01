import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  AlertTriangle, ChevronDown, ChevronRight, Loader2, Power, PowerOff, ShieldAlert, Zap,
} from "lucide-react";
import { createModule2Api } from "../../api/module2";
import { adoptSimulatorKeyById } from "../../api/gatewayContext";
import { syncModule2AfterGatewayKeyChange } from "../../utils/crossModuleSync";
import { TELEMETRY_ACTIVITY_EVENT, TELEMETRY_STORAGE_KEY } from "../../utils/telemetryEvents";
import {
  buildCredentialKillSwitchPayload,
  buildAnalystKillSwitchReason,
  createKillSwitchApi,
} from "../../api/killSwitch";
import { ApiKeyRiskProfile } from "./ApiKeyRiskProfile";
import { RiskBandBadge } from "./RiskBandBadge";
import { InfoTooltip } from "./InfoTooltip";

const FLASH_DISMISS_MS = 5000;
const BEHAVIOR_RELOAD_DELAYS_POLLING_MS = [0, 2000];
const BEHAVIOR_RELOAD_DELAYS_LIVE_MS = [0];
const BEHAVIOR_TELEMETRY_DEBOUNCE_MS = 150;
const BEHAVIOR_POLL_MS = 10_000;

const FILTERS = [
  { id: "all", label: "All keys" },
  { id: "active", label: "Active" },
  { id: "disabled", label: "Disabled" },
  { id: "high", label: "High risk" },
  { id: "activity", label: "With activity" },
  { id: "kill-switch", label: "Kill switch" },
];

function riskBandClass(band) {
  if (band === "high") return "bg-red-100 text-red-700 dark:bg-red-900/40 dark:text-red-300";
  if (band === "medium") return "bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-300";
  return "bg-emerald-100 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-300";
}

function KillSwitchModal({ row, onClose, onConfirm, loading, simulatorKeyId = "", simulatorKeyPrefix = "" }) {
  if (!row) return null;
  const isActiveSimulatorKey = simulatorKeyId && row.key_id === simulatorKeyId;
  const simulatorMismatch = simulatorKeyId && row.key_id !== simulatorKeyId;

  return (
    <div className="fixed inset-0 z-[100] flex items-center justify-center bg-black/40 p-4">
      <div className="w-full max-w-md rounded-2xl border border-slate-200 bg-white p-5 shadow-xl dark:border-slate-700 dark:bg-slate-900">
        <h4 className="text-sm font-semibold text-slate-900 dark:text-white">Credential kill switch</h4>
        <p className="mt-1 text-xs text-slate-500">
          Immediately blocks <span className="font-semibold">all models</span> for API key{" "}
          <span className="font-mono">{row.prefix}</span> at the gateway.
        </p>
        {simulatorMismatch && (
          <div className="mt-3 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-900 dark:border-amber-800 dark:bg-amber-950/30 dark:text-amber-100">
            Attack Simulator is currently using{" "}
            <span className="font-mono font-semibold">{simulatorKeyPrefix || "another key"}</span>.
            Kill switch on <span className="font-mono font-semibold">{row.prefix}</span> will not stop
            simulator traffic until you adopt this key (Create API Key → Use in Attack Simulator).
          </div>
        )}
        {isActiveSimulatorKey && (
          <div className="mt-3 rounded-lg border border-teal-200 bg-teal-50 px-3 py-2 text-xs text-teal-900 dark:border-teal-800 dark:bg-teal-950/30 dark:text-teal-100">
            This is the active Attack Simulator key — the next simulator run should return HTTP 503
            with <span className="font-mono">kill_switch_active</span>.
          </div>
        )}
        <div className="mt-4 flex justify-end gap-2">
          <button type="button" onClick={onClose} className="rounded-lg px-3 py-2 text-xs text-slate-600 hover:bg-slate-100 dark:hover:bg-slate-800">
            Cancel
          </button>
          <button
            type="button"
            disabled={loading}
            onClick={() => onConfirm()}
            className="rounded-lg bg-red-600 px-3 py-2 text-xs font-semibold text-white hover:bg-red-700 disabled:opacity-60"
          >
            {loading ? "Activating…" : "Activate kill switch"}
          </button>
        </div>
      </div>
    </div>
  );
}

function FleetRowActions({
  row,
  fetchWithAuth,
  orgId,
  simulatorKeyId,
  onActionComplete,
  onKillSwitchClick,
  onFlash,
  onSimulatorKeyAdopted,
}) {
  const api = useMemo(() => createKillSwitchApi(fetchWithAuth), [fetchWithAuth]);
  const [loading, setLoading] = useState(null);
  const isSimulatorKey = simulatorKeyId && row.key_id === simulatorKeyId;

  const handleSetAsSimulator = async () => {
    if (!orgId) return;
    const confirmed = window.confirm(
      `Use API key ${row.prefix} as the Attack Simulator credential? `
      + "Module 1 simulators and M2.2 UEBA will track traffic under this key.",
    );
    if (!confirmed) return;
    setLoading("simulator");
    try {
      const ctx = await adoptSimulatorKeyById(fetchWithAuth, row.key_id, orgId);
      syncModule2AfterGatewayKeyChange("adopt-simulator", {
        prefix: ctx.prefix,
        key_id: ctx.keyId,
      });
      onSimulatorKeyAdopted?.(ctx);
      onFlash?.(`Simulator key set to ${ctx.prefix}.`, "success");
      onActionComplete?.();
    } catch (err) {
      onFlash?.(err.message || "Failed to set simulator key.", "error");
    } finally {
      setLoading(null);
    }
  };

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
    } catch (err) {
      onFlash?.(err.message || "Failed to update API key status.", "error");
    } finally {
      setLoading(null);
    }
  };

  const handleDeactivateKs = async (ksId) => {
    setLoading(`ks-${ksId}`);
    try {
      await api.deactivateKillSwitch(ksId);
      onActionComplete?.();
    } catch (err) {
      onFlash?.(err.message || "Failed to deactivate kill switch.", "error");
    } finally {
      setLoading(null);
    }
  };

  return (
    <div className="flex flex-wrap items-center gap-1.5">
      {row.is_active !== false && !isSimulatorKey && (
        <button
          type="button"
          disabled={!!loading}
          onClick={handleSetAsSimulator}
          className="inline-flex items-center gap-1 rounded-md border border-teal-300 bg-teal-50 px-2 py-1 text-[11px] font-semibold text-teal-800 hover:bg-teal-100 disabled:opacity-60 dark:border-teal-700 dark:bg-teal-950/30 dark:text-teal-200"
          title="Bind this key to Module 1 Attack Simulator"
        >
          {loading === "simulator" ? <Loader2 className="h-3 w-3 animate-spin" /> : <Zap className="h-3 w-3" />}
          Set simulator
        </button>
      )}
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
  refreshSignal = 0,
  simulatorKeyId = "",
  simulatorKeyPrefix = "",
  orgId = null,
  onSimulatorKeyAdopted,
  onActionComplete,
  loading = false,
  liveConnected = false,
}) {
  const module2Api = useMemo(() => createModule2Api(fetchWithAuth), [fetchWithAuth]);
  const killApi = useMemo(() => createKillSwitchApi(fetchWithAuth), [fetchWithAuth]);
  const [filter, setFilter] = useState("all");
  const [expandedKeyId, setExpandedKeyId] = useState(null);
  const [expandedBehavior, setExpandedBehavior] = useState(null);
  const [expandLoading, setExpandLoading] = useState(false);
  const reloadTimersRef = useRef([]);
  const behaviorSeqRef = useRef(0);
  const telemetryDebounceRef = useRef(null);
  const prevSelectedKeyIdRef = useRef(selectedKeyId);
  const [killModalRow, setKillModalRow] = useState(null);
  const [killModalLoading, setKillModalLoading] = useState(false);
  const [flash, setFlash] = useState(null);

  useEffect(() => {
    if (!flash) return undefined;
    const id = setTimeout(() => setFlash(null), FLASH_DISMISS_MS);
    return () => clearTimeout(id);
  }, [flash]);

  const showFlash = useCallback((message, tone = "success") => {
    setFlash({ message, tone });
  }, []);

  const filteredRows = useMemo(() => {
    return rows.filter((row) => {
      if (filter === "active") return row.is_active !== false;
      if (filter === "disabled") return row.is_active === false;
      if (filter === "high") return row.risk_band === "high";
      if (filter === "activity") return (row.request_count ?? 0) > 0;
      if (filter === "kill-switch") return (row.active_kill_switch_count ?? 0) > 0;
      return true;
    });
  }, [rows, filter]);

  const displayRows = useMemo(() => {
    if (!simulatorKeyId) return filteredRows;
    return [...filteredRows].sort((a, b) => {
      if (a.key_id === simulatorKeyId) return -1;
      if (b.key_id === simulatorKeyId) return 1;
      return 0;
    });
  }, [filteredRows, simulatorKeyId]);

  useEffect(() => {
    const prev = prevSelectedKeyIdRef.current;
    prevSelectedKeyIdRef.current = selectedKeyId;
    if (selectedKeyId && selectedKeyId !== prev) {
      setExpandedKeyId(selectedKeyId);
    }
  }, [selectedKeyId]);

  const behaviorSnapshotEqual = useCallback((prev, next) => {
    if (!prev || !next) return false;
    return (
      prev.request_count === next.request_count
      && prev.blocked_count === next.blocked_count
      && prev.redacted_count === next.redacted_count
      && prev.risk_score === next.risk_score
      && prev.risk_band === next.risk_band
      && JSON.stringify(prev.recent_requests) === JSON.stringify(next.recent_requests)
    );
  }, []);

  const loadExpandedBehavior = useCallback(async (keyId, { silent = false } = {}) => {
    const seq = ++behaviorSeqRef.current;
    if (!silent) {
      setExpandLoading(true);
      setExpandedBehavior(null);
    }
    try {
      const data = await module2Api.getUebaBehavior(keyId, period, { useCache: false });
      if (seq !== behaviorSeqRef.current) return;
      setExpandedBehavior((prev) => {
        if (silent && prev && behaviorSnapshotEqual(prev, data)) return prev;
        return data;
      });
    } catch (err) {
      if (seq !== behaviorSeqRef.current) return;
      if (!silent) {
        setExpandedBehavior(null);
      }
      showFlash(err.message || "Failed to load key behavior.", "error");
    } finally {
      if (seq !== behaviorSeqRef.current) return;
      if (!silent) setExpandLoading(false);
    }
  }, [module2Api, period, showFlash, behaviorSnapshotEqual]);

  const scheduleBehaviorReload = useCallback((keyId) => {
    const delays = liveConnected ? BEHAVIOR_RELOAD_DELAYS_LIVE_MS : BEHAVIOR_RELOAD_DELAYS_POLLING_MS;
    reloadTimersRef.current.forEach((timerId) => clearTimeout(timerId));
    reloadTimersRef.current = delays.map((delay) => (
      setTimeout(() => {
        if (expandedKeyId === keyId) {
          loadExpandedBehavior(keyId, { silent: true });
        }
      }, delay)
    ));
  }, [expandedKeyId, liveConnected, loadExpandedBehavior]);

  const debouncedBehaviorReload = useCallback((keyId) => {
    clearTimeout(telemetryDebounceRef.current);
    telemetryDebounceRef.current = setTimeout(() => {
      scheduleBehaviorReload(keyId);
    }, BEHAVIOR_TELEMETRY_DEBOUNCE_MS);
  }, [scheduleBehaviorReload]);

  useEffect(() => () => {
    reloadTimersRef.current.forEach((timerId) => clearTimeout(timerId));
    clearTimeout(telemetryDebounceRef.current);
  }, []);

  const profileBehavior = useMemo(() => {
    if (!expandedBehavior || expandedBehavior.key_id !== expandedKeyId) return null;
    const row = rows.find((r) => r.key_id === expandedKeyId);
    if (!row || row.request_count !== expandedBehavior.request_count) {
      return expandedBehavior;
    }
    return {
      ...expandedBehavior,
      ...row,
      key_id: expandedBehavior.key_id,
      prefix: expandedBehavior.prefix || row.prefix,
      name: expandedBehavior.name || row.name,
      owner_email: expandedBehavior.owner_email || row.owner_email,
      request_count: row.request_count ?? expandedBehavior.request_count,
      blocked_count: row.blocked_count ?? expandedBehavior.blocked_count,
      redacted_count: row.redacted_count ?? expandedBehavior.redacted_count,
      block_rate_pct: row.block_rate_pct ?? expandedBehavior.block_rate_pct,
      redact_rate_pct: row.redact_rate_pct ?? expandedBehavior.redact_rate_pct,
      risk_score: row.risk_score ?? expandedBehavior.risk_score,
      risk_band: row.risk_band ?? expandedBehavior.risk_band,
      velocity_spike: row.velocity_spike ?? expandedBehavior.velocity_spike,
      recent_requests: expandedBehavior.recent_requests,
      active_kill_switches: row.active_kill_switches ?? expandedBehavior.active_kill_switches,
    };
  }, [expandedBehavior, expandedKeyId, rows]);

  const expandedRowFingerprint = useMemo(() => {
    const row = rows.find((r) => r.key_id === expandedKeyId);
    if (!row) return "";
    return `${row.request_count}:${row.blocked_count}:${row.risk_score}`;
  }, [rows, expandedKeyId]);

  useEffect(() => {
    if (expandedKeyId) {
      loadExpandedBehavior(expandedKeyId);
    }
  }, [period, expandedKeyId, loadExpandedBehavior]);

  useEffect(() => {
    if (!expandedKeyId || refreshSignal === 0) return;
    debouncedBehaviorReload(expandedKeyId);
  }, [refreshSignal, expandedKeyId, debouncedBehaviorReload]);

  useEffect(() => {
    if (!expandedKeyId || !expandedRowFingerprint) return;
    debouncedBehaviorReload(expandedKeyId);
  }, [expandedRowFingerprint, expandedKeyId, debouncedBehaviorReload]);

  useEffect(() => {
    if (!expandedKeyId) return undefined;
    const onTelemetry = () => debouncedBehaviorReload(expandedKeyId);
    const onStorage = (event) => {
      if (event.key === TELEMETRY_STORAGE_KEY) onTelemetry();
    };
    window.addEventListener(TELEMETRY_ACTIVITY_EVENT, onTelemetry);
    window.addEventListener("storage", onStorage);
    const id = liveConnected
      ? null
      : setInterval(() => {
          loadExpandedBehavior(expandedKeyId, { silent: true });
        }, BEHAVIOR_POLL_MS);
    return () => {
      if (id) clearInterval(id);
      clearTimeout(telemetryDebounceRef.current);
      window.removeEventListener(TELEMETRY_ACTIVITY_EVENT, onTelemetry);
      window.removeEventListener("storage", onStorage);
    };
  }, [expandedKeyId, liveConnected, loadExpandedBehavior, debouncedBehaviorReload]);

  const toggleExpand = useCallback((row) => {
    const collapsing = expandedKeyId === row.key_id;
    const next = collapsing ? null : row.key_id;
    setExpandedKeyId(next);
    onSelectKey?.(collapsing ? null : row.key_id);
    if (collapsing) {
      setExpandedBehavior(null);
    }
  }, [expandedKeyId, onSelectKey]);

  const handleKillSwitchConfirm = async () => {
    if (!killModalRow) return;
    setKillModalLoading(true);
    try {
      const behavior = expandedBehavior?.key_id === killModalRow.key_id
        ? expandedBehavior
        : killModalRow;
      const payload = buildCredentialKillSwitchPayload({
        apiKeyPrefix: killModalRow.prefix,
        reason: buildAnalystKillSwitchReason(behavior),
      });
      await killApi.createAndActivateKillSwitch(payload);
      showFlash(`Kill switch activated for ${killModalRow.prefix} (all models)`);
      setKillModalRow(null);
      await onActionComplete?.();
      if (expandedKeyId === killModalRow.key_id) {
        await loadExpandedBehavior(killModalRow.key_id);
      }
    } catch (err) {
      showFlash(err.message || "Failed to apply kill switch.", "error");
    } finally {
      setKillModalLoading(false);
    }
  };

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
        <p className={`mx-4 mt-3 rounded-lg border px-3 py-2 text-xs ${
          flash.tone === "error"
            ? "border-red-200 bg-red-50 text-red-800 dark:border-red-900 dark:bg-red-950/40 dark:text-red-200"
            : "border-teal-200 bg-teal-50 text-teal-800 dark:border-teal-900 dark:bg-teal-950/40 dark:text-teal-200"
        }`}>
          {flash.message}
        </p>
      )}

      {loading && !rows.length ? (
        <div className="flex justify-center py-16">
          <Loader2 className="h-6 w-6 animate-spin text-teal-500" />
        </div>
      ) : !displayRows.length ? (
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
              {displayRows.map((row) => {
                const isExpanded = expandedKeyId === row.key_id;
                const isSelected = selectedKeyId === row.key_id;
                const isSimulatorKey = simulatorKeyId && row.key_id === simulatorKeyId;
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
                      <td className="px-3 py-2 font-mono text-xs">
                        {row.prefix}
                        {isSimulatorKey && (
                          <span
                            className="ml-1.5 inline-flex items-center gap-1 rounded bg-teal-600 px-1.5 py-0.5 text-[10px] font-bold uppercase text-white"
                            title="Attack Simulator key"
                          >
                            <Zap className="h-2.5 w-2.5" />
                            Simulator
                          </span>
                        )}
                      </td>
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
                        <RiskBandBadge type="behavioral" band={row.risk_band} score={row.risk_score} />
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
                          orgId={orgId}
                          simulatorKeyId={simulatorKeyId}
                          onActionComplete={onActionComplete}
                          onKillSwitchClick={setKillModalRow}
                          onFlash={showFlash}
                          onSimulatorKeyAdopted={onSimulatorKeyAdopted}
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
                          ) : profileBehavior ? (
                            <ApiKeyRiskProfile
                              behavior={profileBehavior}
                              fetchWithAuth={fetchWithAuth}
                              onActionComplete={onActionComplete}
                              showActions={false}
                              simulatorKeyPrefix={simulatorKeyPrefix}
                            />
                          ) : (
                            <p className="py-4 text-center text-xs text-amber-600 dark:text-amber-400">
                              Could not load request detail. Collapse and expand the row to retry.
                            </p>
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
        loading={killModalLoading}
        simulatorKeyId={simulatorKeyId}
        simulatorKeyPrefix={simulatorKeyPrefix}
        onClose={() => setKillModalRow(null)}
        onConfirm={handleKillSwitchConfirm}
      />
    </div>
  );
}
