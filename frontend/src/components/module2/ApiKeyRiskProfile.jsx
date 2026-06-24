import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AlertTriangle, Loader2, Power, PowerOff, RefreshCw, ShieldAlert, ExternalLink } from "lucide-react";
import { createModule2Api } from "../../api/module2";
import {
  buildCredentialKillSwitchPayload,
  buildAnalystKillSwitchReason,
  createKillSwitchApi,
  filterKillSwitchesForPrefix,
} from "../../api/killSwitch";
import { RiskBandBadge } from "./RiskBandBadge";
import { UebaKeyOverridePanel } from "./UebaKeyOverridePanel";

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
  block_rate_deviation: "Block deviation",
  volume_anomaly: "Volume anomaly",
  model_novelty: "Model novelty",
};

const MODE_STYLES = {
  learning: "bg-sky-100 text-sky-800 dark:bg-sky-900/40 dark:text-sky-300",
  active: "bg-violet-100 text-violet-800 dark:bg-violet-900/40 dark:text-violet-300",
};

const LLM_VERDICT_STYLES = {
  benign: "text-emerald-700 dark:text-emerald-300",
  suspicious: "text-amber-700 dark:text-amber-300",
  malicious: "text-red-700 dark:text-red-300",
  skipped: "text-slate-500 dark:text-slate-400",
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

function formatRequestTime(timestamp) {
  return (timestamp || "").replace("T", " ").slice(0, 19);
}

function promptPreview(req) {
  return (req?.prompt_snippet || req?.intent || req?.detail || "").trim();
}

function compactRequestJson(req) {
  return {
    timestamp: req.timestamp,
    action: req.action,
    model: req.model,
    threat_type: req.threat_type,
    prompt: promptPreview(req) || null,
  };
}

function promptPositionLabel(index) {
  if (index === 0) return "Last prompt";
  if (index === 1) return "2nd last";
  if (index === 2) return "3rd last";
  if (index === 3) return "4th last";
  if (index === 4) return "5th last";
  return `${index + 1}th last`;
}

function requestIdentity(req) {
  if (!req) return "";
  return `${req.event_id ?? ""}:${req.timestamp ?? ""}:${promptPreview(req)}`;
}

function RecentRequestsSection({ requests, requestCount }) {
  const [selectedIndex, setSelectedIndex] = useState(0);
  const [showJson, setShowJson] = useState(false);
  const selectedIdentityRef = useRef("");

  useEffect(() => {
    if (!requests?.length) {
      setSelectedIndex(0);
      selectedIdentityRef.current = "";
      return;
    }
    const remembered = selectedIdentityRef.current;
    if (remembered) {
      const matchedIndex = requests.findIndex((req) => requestIdentity(req) === remembered);
      if (matchedIndex >= 0) {
        setSelectedIndex(matchedIndex);
        return;
      }
    }
    setSelectedIndex((prev) => {
      const next = Math.min(prev, requests.length - 1);
      selectedIdentityRef.current = requestIdentity(requests[next]);
      return next;
    });
  }, [requests]);

  if (!requestCount && !requests?.length) return null;

  const safeIndex = Math.min(selectedIndex, Math.max(0, (requests?.length || 1) - 1));
  const selected = requests?.[safeIndex];
  const canGoNewer = safeIndex > 0;
  const canGoOlder = safeIndex < (requests?.length || 0) - 1;

  return (
    <div className="rounded-lg border border-slate-200 p-3 dark:border-slate-600">
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
        <p className="text-xs font-semibold uppercase text-slate-500">
          Recent prompts
          {requestCount ? ` · ${requestCount} total` : ""}
        </p>
      </div>

      {requests?.length > 0 ? (
        <div className="space-y-3">
          <div className="flex flex-wrap items-center gap-1.5">
            {requests.map((req, i) => (
              <button
                key={`${req.event_id || req.timestamp}-${i}`}
                type="button"
                onClick={() => {
                  selectedIdentityRef.current = requestIdentity(req);
                  setSelectedIndex(i);
                  setShowJson(false);
                }}
                className={`rounded-full px-2.5 py-1 text-[11px] font-medium transition-colors ${
                  i === safeIndex
                    ? "bg-teal-600 text-white shadow-sm"
                    : "border border-slate-200 bg-white text-slate-600 hover:border-teal-300 hover:text-teal-700 dark:border-slate-600 dark:bg-slate-800 dark:text-slate-300 dark:hover:border-teal-600 dark:hover:text-teal-300"
                }`}
              >
                {promptPositionLabel(i)}
              </button>
            ))}
          </div>

          {requests.length > 1 && (
            <div className="flex items-center gap-2 text-[11px]">
              <button
                type="button"
                disabled={!canGoNewer}
                onClick={() => {
                  setSelectedIndex((i) => {
                    const next = Math.max(0, i - 1);
                    selectedIdentityRef.current = requestIdentity(requests[next]);
                    return next;
                  });
                  setShowJson(false);
                }}
                className="rounded-md border border-slate-200 px-2 py-1 font-medium text-slate-600 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-40 dark:border-slate-600 dark:text-slate-300 dark:hover:bg-slate-800"
              >
                Next (newer)
              </button>
              <span className="text-slate-400">
                Viewing {promptPositionLabel(safeIndex).toLowerCase()}
              </span>
              <button
                type="button"
                disabled={!canGoOlder}
                onClick={() => {
                  setSelectedIndex((i) => {
                    const next = Math.min(requests.length - 1, i + 1);
                    selectedIdentityRef.current = requestIdentity(requests[next]);
                    return next;
                  });
                  setShowJson(false);
                }}
                className="rounded-md border border-slate-200 px-2 py-1 font-medium text-slate-600 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-40 dark:border-slate-600 dark:text-slate-300 dark:hover:bg-slate-800"
              >
                Previous prompt
              </button>
            </div>
          )}

          {selected && (
            <div className="rounded-md border border-slate-100 bg-white/70 px-3 py-2 text-xs dark:border-slate-700 dark:bg-slate-800/60">
              <p className="text-[10px] font-semibold uppercase tracking-wide text-teal-600 dark:text-teal-400">
                {promptPositionLabel(safeIndex)}
              </p>
              <p className="mt-1 font-medium text-slate-700 dark:text-slate-200">
                {(selected.action || "—").toUpperCase()}
                {" · "}{selected.model || "—"}
                {" · "}{selected.threat_type || "—"}
              </p>
              <p className="mt-1 text-[10px] text-slate-400">
                {formatRequestTime(selected.timestamp)} UTC
              </p>
              <p className="mt-2 whitespace-pre-wrap break-words text-slate-600 dark:text-slate-300">
                {promptPreview(selected) || "No prompt captured for this event."}
              </p>
              <button
                type="button"
                onClick={() => setShowJson((v) => !v)}
                className="mt-2 text-[11px] font-medium text-teal-600 hover:underline dark:text-teal-400"
              >
                {showJson ? "Hide JSON" : "Show JSON"}
              </button>
              {showJson && (
                <pre className="mt-2 max-h-40 overflow-auto rounded-md bg-slate-900 p-2 font-mono text-[10px] leading-relaxed text-emerald-300 dark:bg-black/50">
                  {JSON.stringify(compactRequestJson(selected), null, 2)}
                </pre>
              )}
            </div>
          )}
        </div>
      ) : (
        <p className="text-xs text-slate-400">
          {requestCount} event(s) in this period — waiting for prompt detail…
        </p>
      )}
    </div>
  );
}

export function ApiKeyRiskProfile({
  behavior,
  fetchWithAuth,
  onActionComplete,
  showActions = true,
  simulatorKeyPrefix = "",
  canReassess = false,
  onReassessComplete,
}) {
  const api = useMemo(() => createKillSwitchApi(fetchWithAuth), [fetchWithAuth]);
  const module2Api = useMemo(() => createModule2Api(fetchWithAuth), [fetchWithAuth]);
  const [killSwitches, setKillSwitches] = useState([]);
  const [ksLoading, setKsLoading] = useState(true);
  const [actionLoading, setActionLoading] = useState(null);
  const [actionError, setActionError] = useState(null);
  const [actionSuccess, setActionSuccess] = useState(null);

  const scopedKillSwitches = useMemo(
    () => filterKillSwitchesForPrefix(killSwitches, behavior?.prefix),
    [killSwitches, behavior?.prefix],
  );

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

  if (!behavior) {
    return (
      <p className="py-8 text-center text-sm text-slate-400">
        Select a key from Top Risky Keys to inspect behavior and response controls.
      </p>
    );
  }

  const band = behavior.risk_band || "low";
  const styles = BAND_STYLES[band] || BAND_STYLES.low;
  const displayScore = behavior.final_score ?? behavior.risk_score;
  const breakdown = behavior.score_breakdown || {};
  const uebaMode = behavior.ueba_mode || "learning";
  const isActiveScoring = uebaMode === "active" || breakdown.mode === "active";
  const volumeMetric = breakdown.volume_z ?? behavior.velocity_spike;
  const volumeLabel = isActiveScoring
    ? `Volume z ${Number(volumeMetric ?? 0).toFixed(2)}`
    : `Velocity ${Number(behavior.velocity_spike ?? 1).toFixed(1)}x`;

  const breakdownChips = [];
  if (breakdown.block_rate != null) breakdownChips.push({ label: "Block", value: breakdown.block_rate });
  if (breakdown.block_deviation != null) breakdownChips.push({ label: "Block dev", value: breakdown.block_deviation });
  if (breakdown.volume_z != null) breakdownChips.push({ label: "Volume z", value: breakdown.volume_z });
  if (breakdown.model_novelty != null) breakdownChips.push({ label: "Model novelty", value: breakdown.model_novelty });
  if (breakdown.llm_weighted_delta != null) {
    breakdownChips.push({
      label: "LLM delta",
      value: `${breakdown.llm_weighted_delta >= 0 ? "+" : ""}${Number(breakdown.llm_weighted_delta).toFixed(3)}`,
    });
  } else if (behavior.traditional_score != null && behavior.final_score != null && behavior.traditional_score !== behavior.final_score) {
    breakdownChips.push({
      label: "LLM blend",
      value: `${behavior.traditional_score} → ${behavior.final_score}`,
    });
  }

  const handleApplyKillSwitch = async () => {
    const confirmed = window.confirm(
      `Apply credential kill switch for key ${behavior.prefix}? `
      + "All models using this API key will be blocked at the gateway.",
    );
    if (!confirmed) return;

    setActionLoading("kill-switch");
    setActionError(null);
    setActionSuccess(null);
    try {
      const payload = buildCredentialKillSwitchPayload({
        apiKeyPrefix: behavior.prefix,
        reason: buildAnalystKillSwitchReason(behavior),
      });
      await api.createAndActivateKillSwitch(payload);
      setActionSuccess(`Kill switch activated for ${behavior.prefix} (all models)`);
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

  const handleReassess = async () => {
    setActionLoading("reassess");
    setActionError(null);
    setActionSuccess(null);
    try {
      const updated = await module2Api.reassessUebaKey(behavior.key_id, { run_llm: true });
      onReassessComplete?.(updated);
      setActionSuccess(`Risk snapshot refreshed for ${behavior.prefix}.`);
      onActionComplete?.();
    } catch (err) {
      setActionError(err.message || "Failed to reassess key.");
    } finally {
      setActionLoading(null);
    }
  };

  return (
    <div className="space-y-4">
      {simulatorKeyPrefix && behavior.prefix && behavior.prefix !== simulatorKeyPrefix && (
        <div className="rounded-lg border border-amber-300 bg-amber-50 px-3 py-2 text-xs text-amber-900 dark:border-amber-800 dark:bg-amber-950/40 dark:text-amber-100">
          Attack Simulator records traffic under key{" "}
          <span className="font-mono font-semibold">{simulatorKeyPrefix}</span>, not{" "}
          <span className="font-mono font-semibold">{behavior.prefix}</span>.
          {" "}Expand the row marked <strong>Simulator</strong> to see new prompts and request counts.
        </div>
      )}
      <div className="flex flex-wrap items-start gap-4 rounded-xl border border-slate-200 bg-slate-50/80 p-4 dark:border-slate-600 dark:bg-slate-800/40">
        <RiskGauge score={displayScore} band={band} />
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <RiskBandBadge type="behavioral" band={band} score={displayScore} />
            <span className={`rounded-full px-2 py-0.5 text-[10px] font-bold uppercase ${MODE_STYLES[uebaMode] || MODE_STYLES.learning}`}>
              {uebaMode}
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
          <p className="mt-2 text-xs text-slate-600 dark:text-slate-300" title="Final score blends traditional UEBA math with optional LLM analyst adjustment (55/45).">
            Final {displayScore}
            {behavior.traditional_score != null ? ` · Traditional ${behavior.traditional_score}` : ""}
            {behavior.llm_score != null ? ` · LLM ${behavior.llm_score}` : ""}
            {" · "}{volumeLabel} · {behavior.request_count} requests
          </p>
          {behavior.computed_at && (
            <p className="mt-1 text-[10px] text-slate-400">
              Snapshot computed {String(behavior.computed_at).replace("T", " ").slice(0, 19)} UTC
            </p>
          )}
          {breakdown.scoring_deferred === "awaiting_baseline" && (
            <p className="mt-1 text-[11px] text-amber-700 dark:text-amber-300">
              Graduated to active mode — deviation scoring starts once the behavior baseline is ready.
            </p>
          )}
        </div>
      </div>

      {breakdownChips.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {breakdownChips.map((chip) => (
            <span
              key={chip.label}
              className="rounded-md border border-slate-200 bg-white px-2 py-0.5 text-[11px] font-medium text-slate-700 dark:border-slate-600 dark:bg-slate-800 dark:text-slate-200"
            >
              {chip.label}: {typeof chip.value === "number" ? chip.value.toFixed(3) : chip.value}
            </span>
          ))}
        </div>
      )}

      {behavior.llm_verdict === "skipped" && (behavior.traditional_score ?? 0) >= 0.45 && (
        <p className="text-[11px] text-slate-500 dark:text-slate-400">
          LLM triage skipped (disabled, below threshold, or Bedrock unavailable). Use Reassess to retry.
        </p>
      )}

      {behavior.llm_verdict && behavior.llm_verdict !== "skipped" && (
        <div className="rounded-lg border border-indigo-200 bg-indigo-50/80 p-3 dark:border-indigo-800 dark:bg-indigo-950/30">
          <p className="text-xs font-semibold uppercase text-indigo-600 dark:text-indigo-300">LLM SOC Analyst</p>
          <p className={`mt-1 text-sm font-semibold capitalize ${LLM_VERDICT_STYLES[behavior.llm_verdict] || ""}`}>
            {behavior.llm_verdict}
            {behavior.llm_confidence != null ? ` · ${(behavior.llm_confidence * 100).toFixed(0)}% confidence` : ""}
          </p>
          {behavior.llm_reasoning && (
            <p className="mt-2 text-xs text-slate-700 dark:text-slate-300">{behavior.llm_reasoning}</p>
          )}
          {behavior.llm_recommended_action && (
            <p className="mt-2 text-[11px] font-medium text-slate-600 dark:text-slate-400">
              Recommended: {behavior.llm_recommended_action} (analyst confirmation required for containment)
            </p>
          )}
        </div>
      )}

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
        {((behavior.top_collections || []).length > 0 || (behavior.top_mcp_tools || []).length > 0) && (
          <>
            <div className="rounded-lg border border-slate-200 p-2 dark:border-slate-600">
              <p className="mb-1 font-semibold uppercase text-slate-500">Vector collections</p>
              <p className="text-slate-700 dark:text-slate-300">
                {(behavior.top_collections || []).slice(0, 3).map(([c, n]) => `${c} (${n})`).join(", ") || "—"}
              </p>
            </div>
            <div className="rounded-lg border border-slate-200 p-2 dark:border-slate-600">
              <p className="mb-1 font-semibold uppercase text-slate-500">MCP tools</p>
              <p className="text-slate-700 dark:text-slate-300">
                {(behavior.top_mcp_tools || []).slice(0, 3).map(([t, n]) => `${t} (${n})`).join(", ") || "—"}
              </p>
            </div>
          </>
        )}
      </div>

      {canReassess && (
        <UebaKeyOverridePanel
          behavior={behavior}
          api={module2Api}
          onSaved={onActionComplete}
        />
      )}

      <RecentRequestsSection
        requests={behavior.recent_requests}
        requestCount={behavior.request_count}
      />

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
                        onActionComplete?.();
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

      {canReassess && (
        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            disabled={!!actionLoading}
            onClick={handleReassess}
            className="inline-flex items-center gap-1.5 rounded-lg border border-indigo-300 px-3 py-2 text-xs font-semibold text-indigo-700 hover:bg-indigo-50 disabled:opacity-60 dark:border-indigo-700 dark:text-indigo-300 dark:hover:bg-indigo-950/30"
          >
            {actionLoading === "reassess" ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <RefreshCw className="h-3.5 w-3.5" />
            )}
            Reassess now
          </button>
        </div>
      )}

      {showActions && (
        <div className="flex flex-wrap gap-2 border-t border-slate-200 pt-3 dark:border-slate-600">
          {behavior.is_active !== false && (
            <button
              type="button"
              disabled={!!actionLoading}
              onClick={handleApplyKillSwitch}
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
    </div>
  );
}
